import json

from pydantic import TypeAdapter, ValidationError

from ..config import get_settings
from .errors import BadFrame
from .models import BaseFrame, Frame

_adapter = TypeAdapter(Frame)


async def parse_frame(raw: str) -> BaseFrame:
    settings = get_settings()
    if not isinstance(raw, str):
        raise BadFrame("Frame must be UTF-8 text")
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise BadFrame("Frame is not valid UTF-8") from exc
    if size > settings.lcp_max_frame_bytes:
        raise BadFrame("Frame exceeds the configured size limit")
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BadFrame("Frame is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BadFrame("Frame must be a JSON object")
    try:
        frame = _adapter.validate_python(value)
    except ValidationError as exc:
        raise BadFrame("Frame does not match the LCP schema") from exc
    if frame.v != settings.lcp_protocol_version:
        raise BadFrame("Unsupported protocol version")
    return frame
