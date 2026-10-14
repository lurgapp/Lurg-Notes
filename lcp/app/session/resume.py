import time

from redis.asyncio import Redis
from redis.exceptions import WatchError

from ..config import Settings
from ..protocol.errors import LCPError
from ..storage import keys
from .manager import SessionManager
from .models import ResumeResult, ResumeStatus
from .tokens import generate_resume_token, hash_resume_token


class ResumeManager:
    def __init__(self, redis: Redis, sessions: SessionManager, settings: Settings) -> None:
        self.redis, self.sessions, self.settings = redis, sessions, settings

    async def resume(
        self, token: str, last_acked_seq: int, gateway_id: str, session_id: str | None
    ) -> ResumeResult:
        if not session_id or not token.startswith("lcp_rt_"):
            return ResumeResult(ResumeStatus.REJECTED)
        raw_token, rotated_hash = generate_resume_token()
        now = int(time.time())
        key = keys.session(session_id)
        while True:
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    data = await pipe.hgetall(key)
                    if not data or data.get("resume_token_hash") != hash_resume_token(token):
                        return ResumeResult(ResumeStatus.REJECTED)
                    if int(data.get("expires_at", 0)) <= now or last_acked_seq > int(data.get("last_seq", 0)):
                        return ResumeResult(ResumeStatus.REJECTED)
                    # Validate central revocation before rotating the transport token/generation.
                    try:
                        await self.sessions.authorize(self.sessions._from_hash(session_id, data), force=True)
                    except LCPError as exc:
                        if exc.code in {"LCP_AUTH_INVALID", "LCP_SESSION_EXPIRED"}:
                            return ResumeResult(ResumeStatus.REJECTED)
                        raise
                    ack = max(int(data.get("last_acked_seq", 0)), last_acked_seq)
                    pipe.multi()
                    pipe.hincrby(key, "generation", 1)
                    pipe.hset(key, mapping={
                        "owner_gateway": gateway_id, "resume_token_hash": rotated_hash, "last_acked_seq": ack,
                    })
                    await pipe.execute()
                    break
                except WatchError:
                    continue
        session = await self.sessions.get(session_id)
        if not session:
            return ResumeResult(ResumeStatus.REJECTED)
        can_replay = await self._can_replay(session_id, last_acked_seq)
        return ResumeResult(
            ResumeStatus.RESUMED if can_replay else ResumeStatus.RESYNC_REQUIRED, session, raw_token
        )

    async def _can_replay(self, session_id: str, seq: int) -> bool:
        from ..realtime.replay import ReplayManager

        return await ReplayManager(self.redis, self.settings).can_replay_from(session_id, seq)
