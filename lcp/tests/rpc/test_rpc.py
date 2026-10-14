from app.rpc.client import RPCClient
from app.rpc.idempotency import IdempotencyManager, IdempotencyStatus
from app.rpc.methods import metadata_for
from app.rpc.router import PrincipalContext, ServiceRouter


async def test_idempotency_returns_completed_result(redis):
    manager = IdempotencyManager(redis)
    assert (await manager.acquire("usr", "nearby.favorites.set", "k")).status is IdempotencyStatus.NEW
    assert (await manager.acquire("usr", "nearby.favorites.set", "k")).status is IdempotencyStatus.PROCESSING
    await manager.complete("usr", "nearby.favorites.set", "k", {"saved": True})
    completed = await manager.acquire("usr", "nearby.favorites.set", "k")
    assert completed.status is IdempotencyStatus.COMPLETED and completed.payload == {"saved": True}


async def test_router_forwards_only_internal_contract(settings, monkeypatch):
    client = RPCClient()
    called = {}

    async def post(url, body, timeout_seconds, internal_token, **kwargs):
        called.update(url=url, body=body, token=internal_token)
        return {"ok": True, "payload": {"events": []}}

    monkeypatch.setattr(client, "post", post)
    result = await ServiceRouter(client, settings).route(
        "nearby.events.list",
        PrincipalContext(None, "sess_1", "guest"),
        {"location": {}},
        metadata_for("nearby.events.list"),
        "req_1",
    )
    assert result["ok"] and called["url"].endswith("/api/v1/internal/lcp/rpc")
    assert called["body"]["principal"] == {"user_id": None, "session_id": "sess_1", "auth_kind": "guest"}
    await client.close()
