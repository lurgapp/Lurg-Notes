from enum import Enum

PROTOCOL_VERSION = 1


class ConnectionState(str, Enum):
    WAIT_HELLO = "wait_hello"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    CLOSING = "closing"
    CLOSED = "closed"
