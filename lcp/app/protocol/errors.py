from dataclasses import dataclass


@dataclass(slots=True)
class LCPError(Exception):
    code: str
    message: str
    retryable: bool = False
    retry_after_ms: int | None = None
    details: dict | None = None
    close_code: int | None = None


class BadFrame(LCPError):
    def __init__(self, message: str = "Malformed LCP frame") -> None:
        super().__init__("LCP_BAD_FRAME", message)


class InvalidState(LCPError):
    def __init__(self, message: str = "Frame is not valid in the current connection state") -> None:
        super().__init__("LCP_INVALID_STATE", message)
