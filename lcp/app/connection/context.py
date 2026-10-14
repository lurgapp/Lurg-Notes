import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import WebSocket

from ..protocol.constants import ConnectionState

if TYPE_CHECKING:
    from ..realtime.subscriptions import Subscription


@dataclass(slots=True)
class ConnectionContext:
    connection_id: str
    websocket: WebSocket
    state: ConnectionState = ConnectionState.WAIT_HELLO
    session_id: str | None = None
    user_id: str | None = None
    generation: int | None = None
    connected_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    pending_requests: dict[str, asyncio.Future] = field(default_factory=dict)
    subscriptions: dict[str, "Subscription"] = field(default_factory=dict)
    resume_token: str | None = None
    handshake_deadline: float | None = None
    last_sent_seq: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    delivery_task: asyncio.Task | None = None
