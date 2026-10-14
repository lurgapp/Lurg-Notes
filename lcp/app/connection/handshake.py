import asyncio

from ..config import Settings
from ..protocol.constants import ConnectionState
from ..protocol.errors import LCPError
from ..protocol.models import HelloAckFrame, HelloFrame
from ..session.resume import ResumeManager
from .context import ConnectionContext


class HandshakeManager:
    def __init__(self, resume: ResumeManager, settings: Settings) -> None:
        self.resume, self.settings = resume, settings

    async def handle_hello(self, context: ConnectionContext, frame: HelloFrame) -> HelloAckFrame:
        if context.state is not ConnectionState.WAIT_HELLO:
            raise LCPError("LCP_INVALID_STATE", "HELLO was already received")
        status, session_id, token = "new", None, None
        if frame.resume_token:
            result = await self.resume.resume(
                frame.resume_token,
                frame.last_acked_seq,
                self.settings.lcp_gateway_id,
                frame.resume_session_id,
            )
            status = result.status.value
            if result.session:
                context.session_id = result.session.id
                context.user_id = result.session.user_id
                context.generation = result.session.generation
                context.resume_token = result.raw_token
                context.state = ConnectionState.READY
                session_id, token = result.session.id, result.raw_token
            else:
                context.state = ConnectionState.AUTHENTICATING
        else:
            context.state = ConnectionState.AUTHENTICATING
        if context.state is ConnectionState.AUTHENTICATING:
            context.handshake_deadline = asyncio.get_running_loop().time() + self.settings.lcp_auth_timeout_seconds
        else:
            context.handshake_deadline = None
        return HelloAckFrame(
            v=self.settings.lcp_protocol_version,
            connection_id=context.connection_id,
            session_id=session_id,
            resume=status,
            resume_token=token,
            limits={
                "max_frame_bytes": self.settings.lcp_max_frame_bytes,
                "max_inflight_requests": self.settings.lcp_max_inflight_requests,
            },
            heartbeat_ms=self.settings.lcp_heartbeat_seconds * 1000,
        )
