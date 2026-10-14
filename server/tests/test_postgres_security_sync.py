"""Real PostgreSQL regression tests in disposable schemas.

Opt in with LURG_TEST_DATABASE_URL pointing at the explicit localhost lurgtest
instance. Tests never use DATABASE_URL for destructive schema operations.
"""
import asyncio
import base64
import importlib.util
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch, AsyncMock
from uuid import uuid4

import asyncpg
import httpx
from app.main import app
from app.auth import current_user
from app.db import get_db
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url

from app.modules.identity import central_router as auth
from app.modules.identity import router as cookie
from app.modules.notes import e2ee, router as notes
from app.modules.sync.outbox import publish_once
from app.schemas import EmailRegistrationRequest, EmailCode, PasswordResetConfirm, RefreshRequest

TEST_URL = os.environ.get("LURG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="explicit disposable PostgreSQL URL required")
ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def database(before_0025=False):
    url = make_url(TEST_URL)
    # A guard against accidentally opting these schema-mutating tests into prod.
    assert url.host in {"localhost", "127.0.0.1"} and url.database == "lurgtest"
    schema = "audit_" + uuid4().hex
    admin = await asyncpg.connect(str(url).replace("postgresql+asyncpg://", "postgresql://"))
    await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    await admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    engine = create_async_engine(TEST_URL, connect_args={"server_settings": {"search_path": schema + ",public"}}, pool_size=5, max_overflow=5)
    try:
        async with engine.begin() as connection:
            raw = (await connection.get_raw_connection()).driver_connection
            await raw.execute((ROOT / "schema.sql").read_text())
            for path in sorted((ROOT / "alembic/versions").glob("*.py")):
                if before_0025 and path.name.startswith("20261003_"):
                    break
                await connection.run_sync(lambda sync, path=path: migrate(sync, path))
        yield async_sessionmaker(engine, expire_on_commit=False), engine
    finally:
        await engine.dispose()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


def migrate(connection, path):
    spec = importlib.util.spec_from_file_location("migration_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()


async def seed_user(sessions):
    async with sessions() as db:
        user = (await db.execute(text("""INSERT INTO users(email,name,password_hash,email_verified)
          VALUES(:email,'Synthetic audit','not-a-real-password-hash',true)
          RETURNING id,email,name,email_verified,security_version"""), {"email": uuid4().hex + "@example.com"})).mappings().one()
        await db.commit()
        return dict(user)


def doc(revision=1, base=0, content_hash="a" * 32):
    return e2ee.EncryptedDocument(format_version=2, key_version=2, algorithm="AES-256-GCM",
        nonce=base64.b64encode(b"x" * 12).decode(), ciphertext=base64.b64encode(b"x" * 32).decode(),
        content_hash=content_hash, document_revision=revision, base_document_revision=base)


async def call(sessions, handler, *args, **kwargs):
    async with sessions() as db:
        try:
            return await handler(*args, db=db, **kwargs)
        except HTTPException as error:
            return error.status_code


def test_postgres_fresh_schema_and_upgrade_existing_envelopes():
    async def run():
        async with database(before_0025=True) as (sessions, engine):
            user = await seed_user(sessions)
            note_id = uuid4()
            async with sessions() as db:
                await db.execute(text("INSERT INTO notes(id,user_id,title,revision) VALUES(:id,:uid,'Opaque',1)"), {"id": note_id, "uid": user["id"]})
                await db.execute(text("""INSERT INTO note_e2ee_documents(note_id,user_id,format_version,key_version,algorithm,nonce,ciphertext,content_hash,document_revision)
                  VALUES(:id,:uid,2,2,'AES-256-GCM','nonce','ciphertext','hash',8)"""), {"id": note_id, "uid": user["id"]})
                await db.execute(text("""INSERT INTO auth_refresh_tokens(user_id,token_hash,app_name,expires_at)
                  VALUES(:uid,'synthetic-hash','Graph',now()+interval '1 day')"""), {"uid": user["id"]})
                await db.commit()
            async with engine.begin() as connection:
                for path in sorted((ROOT / "alembic/versions").glob("20261003_*.py")):
                    await connection.run_sync(lambda sync, path=path: migrate(sync, path))
            async with sessions() as db:
                assert await db.scalar(text("SELECT revision FROM notes WHERE id=:id"), {"id": note_id}) == 8
                assert await db.scalar(text("SELECT security_version FROM auth_refresh_tokens")) == 1
                await db.execute(text("INSERT INTO auth_refresh_tokens(user_id,token_hash,app_name,expires_at) VALUES(:uid,'legacy-after-upgrade','Graph',now()+interval '1 day')"), {"uid": user["id"]})
                assert await db.scalar(text("SELECT security_version FROM auth_refresh_tokens WHERE token_hash='legacy-after-upgrade'")) == 1
                assert await db.scalar(text("SELECT sync_sequence FROM notes WHERE id=:id"), {"id": note_id}) > 0
                assert await db.scalar(text("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND indexname='embeddings_halfvec_idx'")) == 1
            from test_outbox_delivery import Redis
            event_ids = [uuid4(), uuid4()]
            async with sessions() as db:
                for event_id in event_ids:
                    await db.execute(text("""INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload)
                      VALUES(:id,'user_graph',:uid,'graph.updated','{}')"""), {"id": event_id, "uid": user["id"]})
                await db.commit()
            writer = sessions()
            await writer.execute(text("SELECT id FROM outbox_events WHERE id=:id FOR UPDATE"), {"id": event_ids[0]})
            redis = Redis()
            assert await publish_once(redis, sessions) == 1
            assert redis.events[0][1]["source_event_id"] == str(event_ids[1])
            await writer.rollback()
            await writer.close()
            assert await publish_once(Redis(), sessions) == 1
            async with sessions() as db:
                assert await db.scalar(text("SELECT count(*) FROM outbox_events WHERE published_at IS NOT NULL")) == 2
    asyncio.run(run())


def test_postgres_otp_registration_and_refresh_are_single_use():
    async def run():
        async with database() as (sessions, _):
            sent = []
            async def mail(**kwargs):
                sent.append(kwargs["code"])
            payload = EmailRegistrationRequest(email=uuid4().hex + "@example.com", name="Synthetic", password="synthetic-only-password")
            with patch.object(auth, "send_verification_code", mail), patch.object(auth, "hash_password_async", AsyncMock(return_value="synthetic-password-hash")):
                async with sessions() as db:
                    await auth.register_request_code(payload, db, "Graph")
                async with sessions() as db:
                    assert await db.scalar(text("SELECT count(*) FROM users")) == 0
                verify = EmailCode(email=payload.email, code=sent[0])
                results = await asyncio.gather(*(call(sessions, auth.register_verify, verify, app_name="Graph") for _ in range(2)))
                assert sorted(isinstance(result, dict) for result in results) == [False, True]
                assert 400 in results
                token = next(result for result in results if isinstance(result, dict))
                refresh = RefreshRequest(refresh_token=token["refresh_token"])
                rotated = await asyncio.gather(*(call(sessions, auth.refresh, refresh, app_name="Graph") for _ in range(2)))
                assert sorted(isinstance(result, dict) for result in rotated) == [False, True]
                assert 401 in rotated
                credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token["access_token"])
                assert await call(sessions, auth._central_user, credentials) == 401
                current = next(result for result in rotated if isinstance(result, dict))
                await call(sessions, auth.logout_all, RefreshRequest(refresh_token=current["refresh_token"]), app_name="Graph")
                assert await call(sessions, auth.refresh, RefreshRequest(refresh_token=current["refresh_token"]), app_name="Graph") == 401
    asyncio.run(run())


def test_postgres_reset_proof_cannot_replay_even_concurrently():
    async def run():
        async with database() as (sessions, _):
            user = await seed_user(sessions)
            sent = []
            async def mail(**kwargs):
                sent.append(kwargs["code"])
            with patch.object(auth, "send_verification_code", mail), patch.object(auth, "hash_password_async", AsyncMock(return_value="new-synthetic-hash")):
                async with sessions() as db:
                    await auth._issue_code(db=db, email=user["email"], purpose="reset", app_name="Graph")
                async with sessions() as db:
                    proof = await auth.password_reset_verify(EmailCode(email=user["email"], code=sent[0]), db, "Graph")
                payload = PasswordResetConfirm(email=user["email"], password="synthetic-only-password", reset_token=proof["reset_token"])
                results = await asyncio.gather(*(call(sessions, auth.password_reset_confirm, payload, app_name="Graph") for _ in range(2)))
                assert results.count(None) == 1 and results.count(400) == 1
                async with sessions() as db:
                    assert await db.scalar(text("SELECT security_version FROM users WHERE id=:id"), {"id": user["id"]}) == 2
    asyncio.run(run())


def test_postgres_notes_cas_delete_and_legacy_guard():
    async def run():
        async with database() as (sessions, _):
            user = await seed_user(sessions)
            # Two first-device requests race: only one may bootstrap an UMK.
            device_requests = [e2ee.DeviceRegisterRequest(device_id=uuid4(), name="Synthetic device", public_key=base64.b64encode(bytes([index])*512).decode()) for index in (1, 2)]
            devices = await asyncio.gather(*(call(sessions, e2ee.register_device, request, user_id=user["id"]) for request in device_requests))
            assert sorted(result["device"]["status"] for result in devices) == ["active", "pending"]
            active = next(result for result in devices if result["device"]["status"] == "active")
            pending = next(result for result in devices if result["device"]["status"] == "pending")
            async def get_test_db():
                async with sessions() as db:
                    yield db
            app.dependency_overrides[get_db] = get_test_db
            app.dependency_overrides[current_user] = lambda: user["id"]
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    no_device = await client.get("/notes/e2ee/sync")
                    assert no_device.status_code == 401
                    headers = {e2ee.DEVICE_HEADER: str(pending["device"]["id"]), e2ee.CREDENTIAL_HEADER: pending["device_credential"]}
                    assert (await client.get("/notes/e2ee/sync", headers=headers)).status_code == 403
                    headers = {e2ee.DEVICE_HEADER: str(active["device"]["id"]), e2ee.CREDENTIAL_HEADER: active["device_credential"]}
                    nid = uuid4()
                    uploaded = await client.put(f"/notes/e2ee/documents/{nid}", json=doc().model_dump(), headers=headers)
                    assert uploaded.status_code == 200
                    assert (await client.delete(f"/notes/{nid}")).status_code == 409
                    conflict = await client.post(f"/notes/e2ee/documents/{nid}/delete", json={"base_document_revision": 0}, headers=headers)
                    assert conflict.status_code == 409 and conflict.json()["detail"]["document_revision"] == 1
                    assert (await client.post(f"/notes/e2ee/documents/{nid}/delete", json={"base_document_revision": 1}, headers=headers)).status_code == 204
                    assert (await client.get(f"/notes/e2ee/documents/{nid}", headers=headers)).status_code == 404
                    page = await client.get("/notes/e2ee/sync?cursor=0&limit=1", headers=headers)
                    assert page.status_code == 200 and page.json()["documents"] == []
                    assert page.json()["tombstones"][0]["note_id"] == str(nid)
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(current_user, None)
            note_id = uuid4()
            assert isinstance(await call(sessions, e2ee.put_document, note_id, doc(), user_id=user["id"], _={}), dict)
            writes = await asyncio.gather(*(call(sessions, e2ee.put_document, note_id, doc(2, 1, char * 32), user_id=user["id"], _={}) for char in "bc"))
            assert sum(isinstance(result, dict) for result in writes) == 1 and 409 in writes
            assert await call(sessions, notes.delete_note, note_id, user_id=user["id"]) == 409
            assert await call(sessions, e2ee.delete_document, note_id, e2ee.DeleteDocumentRequest(base_document_revision=1), user_id=user["id"], _={}) == 409
            assert await call(sessions, e2ee.delete_document, note_id, e2ee.DeleteDocumentRequest(base_document_revision=2), user_id=user["id"], _={}) is None
            assert await call(sessions, e2ee.get_document, note_id, user_id=user["id"], _={}) == 404
            assert await call(sessions, e2ee.put_document, note_id, doc(4, 2), user_id=user["id"], _={}) == 410
            page = await call(sessions, e2ee.sync_documents, 0, 50, user_id=user["id"], _={})
            assert page["documents"] == [] and any(item["note_id"] == note_id and item["document_revision"] == 3 for item in page["tombstones"])
    asyncio.run(run())


def test_postgres_sync_waits_for_writer_and_drains_over_200_documents():
    async def run():
        async with database() as (sessions, _):
            user = await seed_user(sessions)
            # Hold the owner lock after allocating a sequence. The sync reader
            # must wait for that commit, preventing late-commit cursor loss.
            writer = sessions()
            await e2ee.lock_notes_owner(writer, user["id"])
            note_id = uuid4()
            await writer.execute(text("""INSERT INTO notes(id,user_id,title,deleted_at,sync_sequence)
              VALUES(:id,:uid,'Opaque',now(),nextval('notes_sync_sequence'))"""), {"id": note_id, "uid": user["id"]})
            task = asyncio.create_task(call(sessions, e2ee.sync_documents, 0, 50, user_id=user["id"], _={}))
            await asyncio.sleep(0.1)
            assert not task.done()
            await writer.commit()
            await writer.close()
            assert (await task)["tombstones"][0]["note_id"] == note_id
            async with sessions() as db:
                for _ in range(205):
                    nid = uuid4()
                    await db.execute(text("""INSERT INTO notes(id,user_id,title,sync_sequence)
                      VALUES(:id,:uid,'Opaque',nextval('notes_sync_sequence'))"""), {"id": nid, "uid": user["id"]})
                    await db.execute(text("""INSERT INTO note_e2ee_documents(note_id,user_id,format_version,key_version,algorithm,nonce,ciphertext,content_hash,document_revision)
                      VALUES(:id,:uid,2,2,'AES-256-GCM','nonce','ciphertext','hash',1)"""), {"id": nid, "uid": user["id"]})
                await db.commit()
            cursor = 0
            documents = []
            while True:
                page = await call(sessions, e2ee.sync_documents, cursor, 50, user_id=user["id"], _={})
                documents.extend(page["documents"])
                assert int(page["next_cursor"]) > cursor
                cursor = int(page["next_cursor"])
                if not page["has_more"]:
                    break
            assert len(documents) == 205 and len({doc["note_id"] for doc in documents}) == 205
    asyncio.run(run())
