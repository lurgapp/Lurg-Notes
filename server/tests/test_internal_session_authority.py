"""Persistent transport authority, including revocation in real PostgreSQL."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import HTTPException

from app.config import settings
from app.db import get_db
from app.main import app
from app.modules.identity import central_router as identity


def claims(**changes):
    values = {
        "user_id": str(uuid4()), "session_id": str(uuid4()),
        "security_version": 1,
        "token_expires_at": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
    }
    values.update(changes)
    return values


@pytest.mark.parametrize("supplied", [None, "wrong", "non-ascii-секрет"])
def test_internal_service_key_rejected(supplied, monkeypatch):
    monkeypatch.setattr(settings, "lcp_internal_token", "synthetic-internal-service-key")
    with pytest.raises(HTTPException) as error:
        asyncio.run(identity.require_internal_service(supplied))
    assert error.value.status_code == 401


def test_internal_service_key_must_be_configured(monkeypatch):
    monkeypatch.setattr(settings, "lcp_internal_token", "")
    with pytest.raises(HTTPException) as error:
        asyncio.run(identity.require_internal_service("anything"))
    assert error.value.status_code == 401


@pytest.mark.parametrize("changes", [
    {"session_id": None},
    {"token_expires_at": int(datetime.now(timezone.utc).timestamp()) - 1},
])
def test_unbound_or_expired_claims_fail_before_database(changes, monkeypatch):
    lookup = AsyncMock(side_effect=AssertionError("No lookup needed for unusable claims"))
    monkeypatch.setattr(identity, "load_access_identity", lookup)
    result = asyncio.run(identity.internal_session_status(identity.InternalSessionClaims(**claims(**changes)), object()))
    assert result == {"active": False}
    lookup.assert_not_awaited()


def test_database_failure_cannot_enable_session(monkeypatch):
    monkeypatch.setattr(identity, "load_access_identity", AsyncMock(side_effect=RuntimeError("DB unavailable")))
    with pytest.raises(HTTPException) as error:
        asyncio.run(identity.internal_session_status(identity.InternalSessionClaims(**claims()), object()))
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "INTERNAL_AUTH_UNAVAILABLE"


def test_expiry_during_lookup_is_rejected(monkeypatch):
    now = datetime.now(timezone.utc)
    ticks = iter([now, now + timedelta(seconds=3)])
    monkeypatch.setattr(identity, "_now", lambda: next(ticks))
    monkeypatch.setattr(identity, "load_access_identity", AsyncMock(return_value={"id": uuid4()}))
    values = claims(token_expires_at=int((now + timedelta(seconds=2)).timestamp()))
    assert asyncio.run(identity.internal_session_status(identity.InternalSessionClaims(**values), object())) == {"active": False}


def test_service_auth_is_required_before_authority_lookup(monkeypatch):
    monkeypatch.setattr(settings, "lcp_internal_token", "synthetic-internal-service-key")
    monkeypatch.setattr("app.main.maintenance_enabled", AsyncMock(return_value=False))
    lookup = AsyncMock(side_effect=AssertionError("Unauthorized service cannot inspect authority"))
    monkeypatch.setattr(identity, "load_access_identity", lookup)
    async def db():
        yield object()
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = db
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for headers in [{}, {"X-Lurg-Internal-Token": "wrong"}]:
                response = await client.post("/v1/auth/internal/session-status", json=claims(), headers=headers)
                assert response.status_code == 401
            invalid = await client.post("/v1/auth/internal/session-status", json=claims(access_token="never-accepted"), headers={"X-Lurg-Internal-Token": settings.lcp_internal_token})
            assert invalid.status_code == 422
    try:
        asyncio.run(run())
        lookup.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.skipif(not os.environ.get("LURG_TEST_DATABASE_URL"), reason="explicit disposable PostgreSQL URL required")
def test_real_postgres_transport_authority_revocation_and_rotation(monkeypatch):
    from sqlalchemy import text
    from test_postgres_security_sync import database, seed_user
    monkeypatch.setattr(settings, "lcp_internal_token", "synthetic-internal-service-key")
    monkeypatch.setattr("app.main.maintenance_enabled", AsyncMock(return_value=False))

    async def run():
        async with database() as (sessions, _):
            user = await seed_user(sessions)
            other = await seed_user(sessions)
            async with sessions() as db:
                tokens = await identity._user_tokens(db, user=user, app_name="Graph")
            access = jwt.decode(tokens["access_token"], settings.jwt_secret, algorithms=["HS256"], options={"verify_aud": False})
            body = {"user_id": access["sub"], "session_id": access["sid"], "security_version": access["sv"], "token_expires_at": access["exp"]}
            previous = app.dependency_overrides.copy()
            async def get_test_db():
                async with sessions() as db:
                    yield db
            app.dependency_overrides[get_db] = get_test_db
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    async def check(expected, changes=None):
                        response = await client.post("/v1/auth/internal/session-status", json={**body, **(changes or {})}, headers={"X-Lurg-Internal-Token": settings.lcp_internal_token})
                        assert response.status_code == 200
                        assert response.json() == {"active": expected}
                    await check(True)
                    await check(False, {"user_id": str(other["id"])})
                    await check(False, {"user_id": str(uuid4())})
                    await check(False, {"session_id": str(uuid4())})
                    await check(False, {"session_id": None})
                    await check(False, {"security_version": 2})
                    await check(False, {"token_expires_at": int(datetime.now(timezone.utc).timestamp()) - 1})
                    async with sessions() as db:
                        await db.execute(text("UPDATE users SET status='blocked' WHERE id=:id"), {"id": user["id"]})
                        await db.commit()
                    await check(False)
                    async with sessions() as db:
                        await db.execute(text("UPDATE users SET status='active',email_verified=false WHERE id=:id"), {"id": user["id"]})
                        await db.commit()
                    await check(False)
                    async with sessions() as db:
                        await db.execute(text("UPDATE users SET email_verified=true WHERE id=:id"), {"id": user["id"]})
                        await db.execute(text("UPDATE auth_refresh_tokens SET expires_at=now()-interval '1 second' WHERE id=:id"), {"id": access["sid"]})
                        await db.commit()
                    await check(False)
                    async with sessions() as db:
                        await db.execute(text("UPDATE auth_refresh_tokens SET expires_at=now()+interval '1 day' WHERE id=:id"), {"id": access["sid"]})
                        await db.commit()
                    await check(True)
                    async with sessions() as db:
                        rotated = await identity.refresh(identity.RefreshRequest(refresh_token=tokens["refresh_token"]), db, "Graph")
                    await check(False)
                    fresh = jwt.decode(rotated["access_token"], settings.jwt_secret, algorithms=["HS256"], options={"verify_aud": False})
                    body.update(session_id=fresh["sid"], token_expires_at=fresh["exp"])
                    await check(True)
                    async with sessions() as db:
                        await identity.logout_all(identity.RefreshRequest(refresh_token=rotated["refresh_token"]), db, "Graph")
                    await check(False)
            finally:
                app.dependency_overrides.clear()
                app.dependency_overrides.update(previous)
    asyncio.run(run())
