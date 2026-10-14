import hashlib
from uuid import uuid4

from ..connection.manager import ConnectionManager
from ..observability.metrics import Metrics
from ..protocol.errors import LCPError
from ..protocol.models import EventFrame
from ..session.manager import SessionManager
from .events import EventBus
from .replay import ReplayManager
from .subscriptions import SubscriptionManager


class RealtimeManager:
    def __init__(
        self, bus: EventBus, connections: ConnectionManager, replay: ReplayManager, metrics: Metrics,
        subscriptions: SubscriptionManager | None = None,
        sessions: SessionManager | None = None,
    ) -> None:
        self.bus, self.connections, self.replay = bus, connections, replay
        self.metrics = metrics
        self.subscriptions = subscriptions or SubscriptionManager(replay.redis)
        self.sessions = sessions or SessionManager(replay.redis, replay.settings)

    async def start(self) -> None:
        await self.subscriptions.migrate_existing()
        await self.bus.subscribe_many(sorted(SubscriptionManager.ALLOWED_TOPICS), self.handle_event)

    async def handle_event(
        self, topic: str, payload: dict, event_id: str | None = None, owner_id: str | None = None
    ) -> None:
        # Persist for offline sessions as well. The bus ACK happens only after all fanout writes.
        if topic in SubscriptionManager.PRIVATE_TOPICS and not owner_id:
            raise ValueError("Private event requires an owner_id envelope")
        source_id = event_id or uuid4().hex
        async for subscription in self.subscriptions.for_topic(topic):
            if topic in SubscriptionManager.PRIVATE_TOPICS and subscription.owner_id != owner_id:
                continue
            if topic in SubscriptionManager.PRIVATE_TOPICS:
                session = await self.sessions.get(subscription.session_id)
                if session is None:
                    continue
                try:
                    await self.sessions.authorize(session, force=True)
                except LCPError as exc:
                    if exc.code in {"LCP_AUTH_INVALID", "LCP_SESSION_EXPIRED"}:
                        await self.sessions.destroy(subscription.session_id)
                        continue
                    raise  # Authority outage keeps ingress pending and readiness closed.
            if not self._matches(subscription.filter, payload):
                continue
            event_key = "evt_" + hashlib.sha256(f"{source_id}:{subscription.id}".encode()).hexdigest()
            frame = EventFrame(
                v=1, id=event_key, seq=1, subscription_id=subscription.id,
                name=payload.get("name", "updated"), payload=payload,
            )
            try:
                await self.replay.append(subscription.session_id, None, frame, event_key=event_key)
                self.metrics.inc("lcp_events_persisted_total")
            except RuntimeError:
                # A session may expire/logout while fanout is in progress.
                continue

    @staticmethod
    def _matches(filter: dict, payload: dict) -> bool:
        return all(payload.get(key) == value for key, value in filter.items())
