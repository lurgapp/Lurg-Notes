from .constants import ConnectionState
from .errors import InvalidState, LCPError
from .models import AckFrame, BaseFrame, HelloFrame, RequestFrame


def validate_frame_for_state(frame: BaseFrame, state: ConnectionState) -> None:
    if state is ConnectionState.WAIT_HELLO:
        if not isinstance(frame, HelloFrame):
            raise InvalidState("HELLO is required before any other frame")
        return
    if state is ConnectionState.AUTHENTICATING:
        if not isinstance(frame, RequestFrame) or frame.method not in {
            "session.authenticate",
            "session.guest",
        }:
            raise InvalidState("Authenticate with session.authenticate or session.guest first")
        return
    if state is ConnectionState.READY:
        if not isinstance(frame, (RequestFrame, AckFrame)):
            raise LCPError("LCP_PROTOCOL_VIOLATION", "Client frame type is not allowed", close_code=4400)
        return
    raise InvalidState()
