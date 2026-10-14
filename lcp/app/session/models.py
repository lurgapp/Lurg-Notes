from dataclasses import dataclass
from enum import Enum


@dataclass(slots=True)
class Session:
    id: str
    user_id: str | None
    auth_kind: str
    generation: int
    owner_gateway: str | None
    last_seq: int
    last_acked_seq: int
    created_at: int
    expires_at: int
    security_version: int | None = None
    auth_session_id: str | None = None
    token_expires_at: int | None = None


class ResumeStatus(str, Enum):
    RESUMED = "resumed"
    RESYNC_REQUIRED = "resync_required"
    REJECTED = "rejected"


@dataclass(slots=True)
class ResumeResult:
    status: ResumeStatus
    session: Session | None = None
    raw_token: str | None = None
