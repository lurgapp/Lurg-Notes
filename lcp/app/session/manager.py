import json
import time
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import WatchError

from ..config import Settings
from ..protocol.errors import LCPError
from ..security.auth import AuthManager
from ..storage import keys
from .models import Session
from .tokens import generate_resume_token


class SessionManager:
    def __init__(self, redis: Redis, settings: Settings, authority=None) -> None:
        self.redis, self.settings = redis, settings
        self.authority = authority if authority is not None else AuthManager(settings)

    def _from_hash(self, session_id: str, data: dict[str, str]) -> Session:
        return Session(
            id=session_id,
            user_id=data.get("user_id") or None,
            auth_kind=data["auth_kind"],
            generation=int(data["generation"]),
            owner_gateway=data.get("owner_gateway") or None,
            last_seq=int(data.get("last_seq", 0)),
            last_acked_seq=int(data.get("last_acked_seq", 0)),
            created_at=int(data["created_at"]),
            expires_at=int(data["expires_at"]),
            security_version=int(data["security_version"]) if data.get("security_version") else None,
            auth_session_id=data.get("auth_session_id") or None,
            token_expires_at=int(data["token_expires_at"]) if data.get("token_expires_at") else None,
        )

    async def _create(
        self, user_id: str | None, auth_kind: str, gateway_id: str,
        *, security_version=None, auth_session_id=None, token_expires_at=None,
    ) -> tuple[Session, str]:
        now = int(time.time())
        session_id = f"sess_{uuid4().hex}"
        raw_token, token_hash = generate_resume_token()
        expires_at = now + self.settings.lcp_session_ttl_seconds
        if auth_kind == "account":
            if not security_version or not auth_session_id or not token_expires_at or token_expires_at <= now:
                raise LCPError("LCP_AUTH_INVALID", "Current access session claims are required", close_code=4401)
            expires_at = min(expires_at, token_expires_at)
        values = {
            "user_id": user_id or "",
            "auth_kind": auth_kind,
            "resume_token_hash": token_hash,
            "generation": "1",
            "owner_gateway": gateway_id,
            "last_seq": "0",
            "last_acked_seq": "0",
            "created_at": str(now),
            "expires_at": str(expires_at),
            "security_version": str(security_version) if security_version is not None else "",
            "auth_session_id": auth_session_id or "",
            "token_expires_at": str(token_expires_at) if token_expires_at is not None else "",
        }
        key = keys.session(session_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=values)
            pipe.expire(key, expires_at - now)
            await pipe.execute()
        return self._from_hash(session_id, values), raw_token

    async def create_authenticated(
        self, user_id: str, gateway_id: str, *, security_version: int, auth_session_id: str, token_expires_at: int
    ) -> tuple[Session, str]:
        return await self._create(
            user_id, "account", gateway_id, security_version=security_version,
            auth_session_id=auth_session_id, token_expires_at=token_expires_at,
        )

    async def create_guest(self, gateway_id: str) -> tuple[Session, str]:
        return await self._create(None, "guest", gateway_id)

    async def get(self, session_id: str) -> Session | None:
        value = await self.redis.hgetall(keys.session(session_id))
        return self._from_hash(session_id, value) if value else None

    async def authorize(self, session: Session, *, force: bool = False) -> None:
        if session.expires_at <= int(time.time()):
            raise LCPError("LCP_SESSION_EXPIRED", "Session expired", close_code=4401)
        await self.authority.validate_session(session, force=force)

    async def require_current(
        self, session_id: str, generation: int, gateway_id: str, *, force_authority: bool = False
    ) -> Session:
        session = await self.get(session_id)
        if not session or session.expires_at <= int(time.time()):
            raise LCPError("LCP_SESSION_EXPIRED", "Session expired", close_code=4401)
        if session.generation != generation or session.owner_gateway != gateway_id:
            raise LCPError("LCP_STALE_OWNER", "Session ownership changed", close_code=4409)
        await self.authorize(session, force=force_authority)
        return session

    async def destroy(
        self, session_id: str, generation: int | None = None, gateway_id: str | None = None
    ) -> None:
        # WATCH fences the logout itself, including a resume concurrent with its initial guard.
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(keys.session(session_id), keys.subscriptions(session_id))
                    data = await pipe.hgetall(keys.session(session_id))
                    if generation is not None and (
                        not data or int(data["generation"]) != generation or data["owner_gateway"] != gateway_id
                    ):
                        raise LCPError("LCP_STALE_OWNER", "Session ownership changed", close_code=4409)
                    subscriptions = await pipe.hgetall(keys.subscriptions(session_id))
                    memberships = [(json.loads(value)["topic"], sub_id) for sub_id, value in subscriptions.items()]
                    pipe.multi()
                    for topic, sub_id in memberships:
                        pipe.hdel(keys.topic_subscriptions(topic), sub_id)
                    pipe.delete(
                        keys.session(session_id), keys.replay(session_id), keys.subscriptions(session_id),
                        keys.replay_seen(session_id), f"{keys.replay(session_id)}:age",
                    )
                    await pipe.execute()
                    return
                except WatchError:
                    continue

    async def update_ack(self, session_id: str, generation: int, seq: int) -> bool:
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(keys.session(session_id))
                    data = await pipe.hgetall(keys.session(session_id))
                    if not data or int(data["generation"]) != generation or seq > int(data.get("last_seq", 0)):
                        return False
                    if int(data["expires_at"]) <= int(time.time()):
                        return False
                    pipe.multi()
                    pipe.hset(keys.session(session_id), "last_acked_seq", max(seq, int(data.get("last_acked_seq", 0))))
                    await pipe.execute()
                    return True
                except WatchError:
                    continue
