from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaseFrame(StrictModel):
    v: int
    type: str
    id: str | None = None
    ts: int | None = None


class HelloFrame(BaseFrame):
    type: Literal["hello"]
    client: dict = Field(default_factory=dict)
    # v0.1 originally accepted a flat list.  Native clients need to declare
    # required and optional capabilities independently, so keep accepting the
    # list form while also supporting the documented object form.
    capabilities: dict[str, list[str]] | list[str] = Field(default_factory=dict)
    resume_session_id: str | None = None
    resume_token: str | None = None
    last_acked_seq: int = Field(default=0, ge=0)


class HelloAckFrame(BaseFrame):
    type: Literal["hello_ack"] = "hello_ack"
    connection_id: str
    session_id: str | None = None
    resume: Literal["new", "resumed", "resync_required", "rejected"] = "new"
    resume_token: str | None = None
    limits: dict = Field(default_factory=dict)
    heartbeat_ms: int


class RequestFrame(BaseFrame):
    type: Literal["request"]
    id: str
    method: str
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class ResponseFrame(BaseFrame):
    type: Literal["response"] = "response"
    id: str
    payload: dict = Field(default_factory=dict)


class ErrorPayload(StrictModel):
    code: str
    message: str
    retryable: bool = False
    retry_after_ms: int | None = None
    details: dict | None = None


class ErrorFrame(BaseFrame):
    type: Literal["error"] = "error"
    id: str | None = None
    error: ErrorPayload


class EventFrame(BaseFrame):
    type: Literal["event"] = "event"
    id: str
    seq: int = Field(ge=1)
    subscription_id: str
    name: str
    payload: dict = Field(default_factory=dict)


class AckFrame(BaseFrame):
    type: Literal["ack"]
    seq: int = Field(ge=0)


Frame = Annotated[
    HelloFrame | HelloAckFrame | RequestFrame | ResponseFrame | ErrorFrame | EventFrame | AckFrame,
    Field(discriminator="type"),
]


def frame_json(frame: BaseFrame) -> str:
    return frame.model_dump_json(exclude_none=True)
