"""Real disposable PostgreSQL; provider calls are always substituted."""
import asyncio
import time
import threading
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

from app.modules.app import releases, router
from test_postgres_security_sync import database, seed_user, TEST_URL

pytestmark = pytest.mark.skipif(not TEST_URL, reason="explicit disposable PostgreSQL URL required")


async def token(sessions, user, app="notes", version=1, value=None):
    async with sessions() as db:
        result = await db.scalar(text("""INSERT INTO app_device_tokens(user_id,device_id,app_id,platform,push_provider,push_token,app_version_name,app_version_code,build_identifier)
          VALUES(:user,:device,:app,'android','fcm',:token,'test',:version,'synthetic-release-test') RETURNING id"""),
          {"user": user["id"], "device": uuid4().hex, "app": app, "token": value or "synthetic-" + uuid4().hex, "version": version})
        await db.commit()
        return result


async def queue(sessions, app="notes", code=21, sha="a"*64):
    async with sessions() as db:
        return await releases.queue_release_push(db, releases.release_metadata(app, code, "test", sha))


async def delivery(sessions):
    async with sessions() as db:
        return await releases.deliver_app_release_batch(db)


def provider(monkeypatch, send):
    monkeypatch.setattr(releases, "firebase_enabled", lambda: True)
    monkeypatch.setattr(releases, "firebase_app", lambda: None)
    monkeypatch.setattr(releases.messaging, "send", send)


def test_app_scope_exact_release_concurrency_and_installed_version(monkeypatch):
    messages=[]
    provider(monkeypatch, lambda message: messages.append(message))
    async def run():
        async with database() as (sessions, _):
            user=await seed_user(sessions)
            await token(sessions,user,'notes',value='same-token-between-apps')
            await token(sessions,user,'lurg',value='same-token-between-apps')
            await token(sessions,user,'notes',version=21)
            first,second=await asyncio.gather(queue(sessions),queue(sessions))
            assert first['release_id']==second['release_id'] and first['targeted']==1
            assert {first['deduplicated'],second['deduplicated']}=={True,False}
            assert await delivery(sessions)==1
            assert len(messages)==1 and messages[0].data['app_id']=='notes'
            assert messages[0].data['package_name']=='pro.lurg.notes'
            assert messages[0].data['manifest_url']=='https://notes.lurg.pro/update.json'
            assert messages[0].android.notification.click_action is None
            assert await delivery(sessions)==0
            assert (await queue(sessions,'lurg',11,'b'*64))['targeted']==1
            await delivery(sessions)
            assert messages[-1].data['app_id']=='lurg' and messages[-1].data['package_name']=='pro.lurg.mobile'
            assert messages[-1].android.notification.channel_id=='lurg_general'
            with pytest.raises(ValueError): await queue(sessions,code=21,sha='c'*64)
    asyncio.run(run())


def test_transient_failure_retries_without_deactivating_or_recording_exception(monkeypatch):
    calls=[]
    def send(message):
        calls.append(message)
        if len(calls)==1: raise RuntimeError('private-provider-token-must-not-be-stored')
    provider(monkeypatch,send)
    async def run():
        async with database() as (sessions,_):
            user=await seed_user(sessions)
            await token(sessions,user)
            await queue(sessions)
            await delivery(sessions)
            async with sessions() as db:
                row=(await db.execute(text('SELECT status,attempts,error_code FROM app_release_deliveries'))).mappings().one()
                assert dict(row)==dict(status='pending',attempts=1,error_code='delivery_error')
                assert await db.scalar(text('SELECT status FROM app_device_tokens'))=='active'
                await db.execute(text("UPDATE app_release_deliveries SET next_attempt_at=now()-interval '1 second'"))
                await db.commit()
            await delivery(sessions)
            async with sessions() as db:
                assert await db.scalar(text('SELECT status FROM app_release_deliveries'))=='sent'
                assert await db.scalar(text('SELECT attempts FROM app_release_deliveries'))==2
            assert await delivery(sessions)==0 and len(calls)==2
    asyncio.run(run())


@pytest.mark.parametrize('change',["logout","upgrade","optout","blocked","rotation","superseded","exhausted"])
def test_changed_recipient_and_exhausted_jobs_never_send(monkeypatch,change):
    messages=[]
    provider(monkeypatch,lambda message:messages.append(message))
    async def run():
        async with database() as (sessions,_):
            user=await seed_user(sessions)
            device=await token(sessions,user)
            await queue(sessions)
            async with sessions() as db:
                statements={
                    'logout':"UPDATE app_device_tokens SET status='inactive'",
                    'upgrade':"UPDATE app_device_tokens SET app_version_code=21",
                    'optout':"UPDATE users SET preferences=jsonb_build_object('notifications_enabled',false)",
                    'blocked':"UPDATE users SET status='blocked'",
                    'rotation':"UPDATE app_device_tokens SET push_token='another-synthetic-token'",
                    'exhausted':"UPDATE app_release_deliveries SET attempts=8,status='sending',lease_until=now()-interval '1 second'",
                }
                if change!='superseded':
                    await db.execute(text(statements[change]))
                    await db.commit()
            if change=='superseded':
                await queue(sessions,code=22,sha='b'*64)
                # New release is also excluded; test only obsolescence of21.
                async with sessions() as db:
                    await db.execute(text("UPDATE app_release_deliveries SET next_attempt_at=now()+interval '1 day' WHERE release_id=(SELECT id FROM app_releases WHERE version_code=22)"))
                    await db.commit()
            await delivery(sessions)
            assert messages==[]
            async with sessions() as db:
                status=await db.scalar(text('SELECT status FROM app_release_deliveries WHERE release_id=(SELECT id FROM app_releases WHERE version_code=21)'))
                assert status==('failed' if change=='exhausted' else 'skipped')
    asyncio.run(run())


def test_invalid_token_is_permanent_and_deactivated(monkeypatch):
    def send(message): raise releases.messaging.UnregisteredError('synthetic invalid token')
    provider(monkeypatch,send)
    async def run():
        async with database() as (sessions,_):
            await token(sessions,await seed_user(sessions))
            await queue(sessions)
            await delivery(sessions)
            async with sessions() as db:
                assert await db.scalar(text('SELECT status FROM app_release_deliveries'))=='failed'
                assert await db.scalar(text('SELECT status FROM app_device_tokens'))=='inactive'
            assert await delivery(sessions)==0
    asyncio.run(run())


def test_concurrent_workers_and_expired_lease_recovery(monkeypatch):
    sent=[]
    def send(message):
        time.sleep(.05)
        sent.append(message)
    provider(monkeypatch,send)
    async def run():
        async with database() as (sessions,_):
            await token(sessions,await seed_user(sessions))
            await queue(sessions)
            async with sessions() as db:
                await db.execute(text("UPDATE app_release_deliveries SET status='sending',attempts=1,lease_token=:lease,lease_until=now()-interval '1 second'"),{'lease':uuid4()})
                await db.commit()
            first,second=await asyncio.gather(delivery(sessions),delivery(sessions))
            assert sorted([first,second])==[0,1] and len(sent)==1
            async with sessions() as db:
                assert await db.scalar(text('SELECT attempts FROM app_release_deliveries'))==2
    asyncio.run(run())


def test_auth_and_public_failure_prevent_any_enqueue(monkeypatch):
    async def run():
        async with database() as (sessions,_):
            monkeypatch.setattr(router.settings,'push_internal_token','synthetic-internal-key')
            verifier=AsyncMock(side_effect=httpx.TimeoutException('synthetic unavailable'))
            monkeypatch.setattr(router,'verify_public_manifest',verifier)
            body=router.AppUpdatePushRequest(**releases.release_metadata('notes',21,'test','a'*64))
            async with sessions() as db:
                with pytest.raises(HTTPException) as denied:
                    await router.notify_app_update(body,'wrong',db)
                assert denied.value.status_code==403 and verifier.await_count==0
                with pytest.raises(HTTPException) as failed:
                    await router.notify_app_update(body,'synthetic-internal-key',db)
                assert failed.value.status_code==502
                assert await db.scalar(text('SELECT count(*) FROM app_releases'))==0
            with pytest.raises(ValidationError):
                router.AppUpdatePushRequest(**(body.model_dump()|{'manifest_url':'https://evil.invalid/update.json'}))
    asyncio.run(run())


def test_manifest_verification_uses_only_canonical_app_url(monkeypatch):
    async def run():
        metadata=releases.release_metadata('lurg',11,'test','b'*64)
        urls=[]
        def handler(request):
            urls.append(str(request.url))
            return httpx.Response(200,json=metadata)
        original=httpx.AsyncClient
        monkeypatch.setattr(releases.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
        await releases.verify_public_manifest(metadata)
        assert urls[0].startswith('https://lurg.pro/app/version.json?release=')
        def wrong(request): return httpx.Response(200,json=metadata|{'sha256':'c'*64})
        monkeypatch.setattr(releases.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(wrong),**kwargs))
        with pytest.raises(ValueError): await releases.verify_public_manifest(metadata)
    asyncio.run(run())


def test_provider_timeout_is_bounded_and_preserves_retry(monkeypatch):
    def send(message): time.sleep(.15)
    provider(monkeypatch,send)
    monkeypatch.setattr(releases,'SEND_TIMEOUT_SECONDS',.01)
    async def run():
        async with database() as (sessions,_):
            await token(sessions,await seed_user(sessions))
            await queue(sessions)
            await delivery(sessions)
            async with sessions() as db:
                assert await db.scalar(text('SELECT status FROM app_release_deliveries'))=='pending'
                assert await db.scalar(text('SELECT error_code FROM app_release_deliveries'))=='delivery_error'
                assert await db.scalar(text('SELECT status FROM app_device_tokens'))=='active'
            await asyncio.sleep(.16)  # Let the synthetic provider thread finish.
    asyncio.run(run())


def test_late_invalid_token_result_cannot_mutate_another_lease(monkeypatch):
    started,finish=threading.Event(),threading.Event()
    def send(message):
        started.set()
        finish.wait(timeout=3)
        raise releases.messaging.UnregisteredError('synthetic invalid')
    provider(monkeypatch,send)
    async def run():
        async with database() as (sessions,_):
            await token(sessions,await seed_user(sessions))
            await queue(sessions)
            worker=asyncio.create_task(delivery(sessions))
            while not started.is_set(): await asyncio.sleep(.005)
            replacement=uuid4()
            async with sessions() as db:
                await db.execute(text("UPDATE app_release_deliveries SET lease_token=:lease,status='pending'"),{'lease':replacement})
                await db.commit()
            finish.set()
            await worker
            async with sessions() as db:
                assert await db.scalar(text('SELECT lease_token FROM app_release_deliveries'))==replacement
                assert await db.scalar(text('SELECT status FROM app_release_deliveries'))=='pending'
                assert await db.scalar(text('SELECT status FROM app_device_tokens'))=='active'
    asyncio.run(run())
