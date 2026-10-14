"""Behavior regressions; no production DB, mail, or external AI is contacted."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import make_access_token
from app.config import settings
from app.schemas import EmailRegistrationRequest, Login, PasswordResetConfirm, RefreshRequest
from app.modules.identity import central_router as identity
from app.modules.identity import router as cookie_identity
from app.modules.notes import e2ee
from app.modules.notes import router as notes
from app.modules.chat import router as chat
from app.schemas import ChatMessageCreate


class Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)
    def mappings(self):
        return self
    def first(self):
        return self.rows[0] if self.rows else None
    def one_or_none(self):
        return self.first()
    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]
    def all(self):
        return self.rows


class Db:
    def __init__(self, replies=()):
        self.replies = iter(replies)
        self.writes = []
        self.commits = 0
    async def execute(self, sql, params=None):
        self.writes.append((str(sql), params or {}))
        return Rows(next(self.replies, ()))
    async def commit(self):
        self.commits += 1


def envelope(revision=2, base=1, content_hash="h" * 32):
    return e2ee.EncryptedDocument(format_version=2, key_version=2, algorithm="AES-256-GCM",
        nonce=base64.b64encode(b"x" * 12).decode(), ciphertext=base64.b64encode(b"x" * 32).decode(),
        content_hash=content_hash, document_revision=revision, base_document_revision=base)


def test_registration_cooldown_never_mutates_existing_credentials(monkeypatch):
    now = datetime.now(timezone.utc)
    db = Db([[{"id": uuid4(), "email_verified": False}], [], [{"resend_after": now + timedelta(seconds=60), "metadata": {}}]])
    monkeypatch.setattr(identity, "hash_password_async", AsyncMock(return_value="pending-hash"))
    mail = AsyncMock()
    monkeypatch.setattr(identity, "send_verification_code", mail)
    with pytest.raises(HTTPException) as error:
        asyncio.run(identity.register_request_code(EmailRegistrationRequest(email="audit@example.com", name="Audit", password="test-only-password"), db, "Graph"))
    assert error.value.status_code == 429
    assert db.commits == 0
    assert not any("UPDATE users" in sql or "INSERT INTO users" in sql for sql, _ in db.writes)
    mail.assert_not_awaited()


def test_otp_success_is_not_committed_before_its_mutation():
    now = datetime.now(timezone.utc)
    db = Db([[], [{"code_digest": identity._otp_digest("123456"), "attempts": 0, "expires_at": now + timedelta(minutes=5), "metadata": {"user_id": "owner"}, "app_name": "Graph"}], []])
    result = asyncio.run(identity._verify_code(db, email="audit@example.com", purpose="reset", code="123456", app_name="Graph"))
    assert result == {"user_id": "owner"}
    assert db.commits == 0
    assert "FOR UPDATE" in db.writes[1][0]
    assert "DELETE FROM auth_email_otps" in db.writes[-1][0]


def test_change_email_otp_is_bound_to_requesting_user():
    now = datetime.now(timezone.utc)
    db = Db([[], [{"code_digest": identity._otp_digest("123456"), "attempts": 0, "expires_at": now + timedelta(minutes=5), "metadata": {"user_id": str(uuid4())}, "app_name": "Graph"}], []])
    result = asyncio.run(identity._verify_code(db, email="audit@example.com", purpose="change_email", code="123456", app_name="Graph", user_id=uuid4()))
    assert result is None
    assert db.commits == 1
    assert "UPDATE auth_email_otps" in db.writes[-1][0]


def test_central_me_rejects_access_token_after_security_version_change(monkeypatch):
    uid = uuid4()
    token = make_access_token(user_id=uid, email="audit@example.com", name="Audit", app_name="Graph", email_verified=True, security_version=1)
    db = Db([[{"id": uid, "status": "active", "email_verified": True, "security_version": 2}]])
    monkeypatch.setattr(identity, "release_expired_blocks", AsyncMock())
    with pytest.raises(HTTPException) as error:
        asyncio.run(identity._central_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db))
    assert error.value.status_code == 401


def test_cookie_login_does_not_authenticate_unverified_accounts(monkeypatch):
    db = Db([[{"id": uuid4(), "email": "audit@example.com", "name": "Audit", "password_hash": "hash", "status": "active", "email_verified": False, "security_version": 1}]])
    monkeypatch.setattr(identity, "limit_password_attempts", AsyncMock())
    monkeypatch.setattr(cookie_identity, "release_expired_blocks", AsyncMock())
    monkeypatch.setattr(cookie_identity, "verify_password_async", AsyncMock(return_value=True))
    response = Response()
    with pytest.raises(HTTPException) as error:
        asyncio.run(cookie_identity.login(Login(email="audit@example.com", password="test-only-password"), response, db))
    assert error.value.status_code == 403
    assert "set-cookie" not in response.headers


def test_reset_proof_without_jti_is_rejected_before_mutation():
    now = datetime.now(timezone.utc)
    token = jwt.encode({"purpose": "reset", "email": "audit@example.com", "app": "Graph", "exp": now + timedelta(minutes=5)}, settings.jwt_secret, algorithm="HS256")
    db = Db()
    with pytest.raises(HTTPException):
        asyncio.run(identity.password_reset_confirm(PasswordResetConfirm(email="audit@example.com", password="test-only-password", reset_token=token), db, "Graph"))
    assert db.writes == []


def test_refresh_cannot_restore_session_with_stale_security_version(monkeypatch):
    db = Db([[{"id": uuid4(), "app_name": "Graph", "status": "active", "email_verified": True, "security_version": 2, "refresh_security_version": 1}]])
    monkeypatch.setattr(identity, "release_expired_blocks", AsyncMock())
    with pytest.raises(HTTPException) as error:
        asyncio.run(identity.refresh(RefreshRequest(refresh_token="x" * 48), db, "Graph"))
    assert error.value.status_code == 401
    assert not any("INSERT INTO auth_refresh_tokens" in sql for sql, _ in db.writes)


@pytest.mark.parametrize("base,revision", [(0, 2), (None, 2), (1, 1), (2, 3)])
def test_cas_rejects_missing_stale_or_non_advancing_revision(base, revision):
    with pytest.raises(HTTPException) as error:
        e2ee.validate_document_write(envelope(revision, base), {"document_revision": 1, "content_hash": "s" * 32})
    assert error.value.status_code == 409
    assert error.value.detail["document_revision"] == 1


def test_cas_allows_retry_and_acknowledged_update_only():
    current = {"document_revision": 2, "content_hash": "h" * 32}
    assert e2ee.validate_document_write(envelope(2, 1), current) is False
    assert e2ee.validate_document_write(envelope(3, 2), current) is True
    assert e2ee.validate_document_write(envelope(1, 0), None) is True


def test_v2_deleted_note_cannot_be_reuploaded():
    db = Db([[], [{"deleted_at": datetime.now(timezone.utc), "revision": 3}]])
    with pytest.raises(HTTPException) as error:
        asyncio.run(e2ee.put_document(uuid4(), envelope(), uuid4(), db, {}))
    assert error.value.status_code == 410
    assert not any("INSERT INTO note_e2ee_documents" in sql for sql, _ in db.writes)


def test_sync_separates_tombstones_and_pages_without_truncation():
    rows = [{"note_id": uuid4(), "revision": 3, "deleted_at": datetime.now(timezone.utc), "sync_sequence": index} for index in range(1, 204)]
    async def drain():
        cursor = 0
        seen = []
        while True:
            eligible = [row for row in rows if row["sync_sequence"] > cursor]
            db = Db([[], eligible[:51]])
            page = await e2ee.sync_documents(cursor, 50, uuid4(), db, {})
            assert page["documents"] == []
            seen.extend(page["tombstones"])
            cursor = int(page["next_cursor"])
            if not page["has_more"]:
                break
        return seen, cursor
    seen, cursor = asyncio.run(drain())
    assert len(seen) == 203 and cursor == 203


def test_graph_optout_calls_neither_model_nor_graph():
    class Forbidden:
        async def execute(self, *args, **kwargs):
            raise AssertionError("Graph must not be read")
        async def extract_graph(self, *args, **kwargs):
            raise AssertionError("Memory extraction must not run")
    route = asyncio.run(chat.route_message(Forbidden(), uuid4(), "Сколько мне лет?", Forbidden(), [], use_graph=False))
    assert route.kind == "general"


def test_personal_question_fast_path_does_not_extract_graph(monkeypatch):
    lookup = AsyncMock(return_value={"answer": "synthetic", "sources": []})
    extraction = AsyncMock(side_effect=AssertionError("unnecessary extraction"))
    monkeypatch.setattr(chat, "direct_graph_lookup", lookup)
    monkeypatch.setattr(chat, "ai_candidate_change", extraction)
    route = asyncio.run(chat.route_message(Db(), uuid4(), "Сколько мне лет?", object(), []))
    assert route.kind == "personal_query"
    extraction.assert_not_awaited()


@pytest.mark.parametrize("handler", [chat.post_message, chat.stream_message])
def test_chat_handlers_enforce_preference_before_pending_graph_lookup(monkeypatch, handler):
    uid = uuid4()
    monkeypatch.setattr(chat, "ensure_thread", AsyncMock(return_value={"id": uuid4()}))
    monkeypatch.setattr(chat, "chat_preferences", AsyncMock(return_value=(False, False)))
    monkeypatch.setattr(chat, "get_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat, "save_message", AsyncMock(return_value={"id": uuid4()}))
    pending = AsyncMock(side_effect=AssertionError("pending Graph must not be read"))
    monkeypatch.setattr(chat, "latest_pending_candidate", pending)
    routed = AsyncMock(return_value=chat.MessageRoute("local", local_answer="synthetic local answer"))
    monkeypatch.setattr(chat, "route_message", routed)
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def session():
        yield Db()
    monkeypatch.setattr(chat, "Session", session)
    async def run():
        result = await handler(ChatMessageCreate(message="Сколько мне лет?"), uid, Db(), object())
        if handler is chat.stream_message:
            async for _ in result.body_iterator:
                pass
    asyncio.run(run())
    assert routed.call_args.kwargs["use_graph"] is False
    pending.assert_not_awaited()


def test_embedding_cache_uses_full_content_digest_and_model(monkeypatch):
    from app import openrouter
    monkeypatch.setattr(openrouter.settings, "gemini_api_key", "synthetic-not-used")
    monkeypatch.setattr(openrouter, "_embedding_cache", {})
    embed = AsyncMock(side_effect=[[1.0], [2.0]])
    monkeypatch.setattr(openrouter, "embedding", embed)
    async def run():
        engine = openrouter.OpenRouterAIEngine()
        one = await engine.embed("x" * 4000 + "first", "RETRIEVAL_DOCUMENT")
        two = await engine.embed("x" * 4000 + "second", "RETRIEVAL_DOCUMENT")
        again = await engine.embed("x" * 4000 + "first", "RETRIEVAL_DOCUMENT")
        assert one == again == [1.0] and two == [2.0]
        assert embed.await_count == 2
        assert all(len(key[-1]) == 64 for key in openrouter._embedding_cache)
    asyncio.run(run())
