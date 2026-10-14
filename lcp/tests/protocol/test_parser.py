import pytest

from app.protocol.constants import ConnectionState
from app.protocol.errors import BadFrame, InvalidState
from app.protocol.models import HelloFrame, RequestFrame
from app.protocol.parser import parse_frame
from app.protocol.validator import validate_frame_for_state


async def test_parse_hello():
    frame = await parse_frame('{"v":1,"type":"hello","capabilities":["replay"]}')
    assert isinstance(frame, HelloFrame)


async def test_parse_hello_with_required_and_optional_capabilities():
    frame = await parse_frame(
        '{"v":1,"type":"hello","capabilities":{"required":["subscriptions"],"optional":["session_resume"]}}'
    )
    assert isinstance(frame, HelloFrame)
    assert frame.capabilities == {"required": ["subscriptions"], "optional": ["session_resume"]}


@pytest.mark.parametrize("raw", ["not-json", "[]", '{"v":1,"type":"unknown"}', '{"v":1,"type":"request"}'])
async def test_invalid_frame_is_rejected(raw):
    with pytest.raises(BadFrame):
        await parse_frame(raw)


def test_state_rejects_rpc_before_hello():
    with pytest.raises(InvalidState):
        validate_frame_for_state(
            RequestFrame(v=1, type="request", id="r1", method="nearby.events.list", payload={}),
            ConnectionState.WAIT_HELLO,
        )
