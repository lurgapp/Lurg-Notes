import os
import time

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.mark.skipif(not os.environ.get("LCP_TEST_REDIS_URL"), reason="Requires native isolated Redis")
def test_native_lifespan_websocket_offline_resume_and_shutdown(redis, settings, monkeypatch):
    settings.redis_url = os.environ["LCP_TEST_REDIS_URL"]
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    with TestClient(main.app) as client:
        # Native XREADGROUP blocks up to 1s before worker readiness; allow bounded
        # startup time plus SSH/CI Redis latency rather than relying on a 1s race.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if client.get("/health/ready").status_code == 200:
                break
            time.sleep(0.02)
        else:
            pytest.fail("Native event worker never became ready")
        with client.websocket_connect("/connect") as websocket:
            websocket.send_json({"v": 1, "type": "hello"})
            assert websocket.receive_json()["type"] == "hello_ack"
            websocket.send_json({"v": 1, "type": "request", "id": "auth", "method": "session.guest"})
            session = websocket.receive_json()["payload"]
            websocket.send_json({
                "v": 1, "type": "request", "id": "sub", "method": "realtime.subscribe",
                "payload": {"topic": "nearby.events", "filter": {}},
            })
            assert websocket.receive_json()["type"] == "response"
        client.portal.call(main.app.state.bus.publish, "nearby.events", {"name": "updated", "event_id": "offline"})
        time.sleep(0.05)
        with client.websocket_connect("/connect") as websocket:
            websocket.send_json({
                "v": 1, "type": "hello", "resume_session_id": session["session_id"],
                "resume_token": session["resume_token"], "last_acked_seq": 0,
            })
            assert websocket.receive_json()["resume"] == "resumed"
            event = websocket.receive_json()
            assert event["type"] == "event" and event["seq"] == 1 and event["payload"]["event_id"] == "offline"
            websocket.send_json({"v": 1, "type": "ack", "seq": event["seq"]})
            websocket.send_json({"v": 1, "type": "request", "id": "logout", "method": "session.logout"})
            assert websocket.receive_json()["payload"]["logged_out"]
        assert client.get("/health/ready").status_code == 200
    assert not main.app.state.ready
    assert main.app.state.heartbeat.done() and main.app.state.bus._task.done()
