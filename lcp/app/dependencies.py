from fastapi import Request

from .connection.manager import ConnectionManager
from .protocol.dispatcher import FrameDispatcher


def connections(request: Request) -> ConnectionManager:
    return request.app.state.connections


def dispatcher(request: Request) -> FrameDispatcher:
    return request.app.state.dispatcher
