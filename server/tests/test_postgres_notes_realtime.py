"""Atomic E2EE invalidations on the explicitly isolated PostgreSQL fixture."""
import asyncio
import json
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text

from app.modules.notes import e2ee
from app.modules.sync.outbox import publish_once
from test_postgres_security_sync import database, seed_user, doc, call, pytestmark
from test_outbox_delivery import Redis


def test_notes_invalidations_are_atomic_private_idempotent_and_retryable():
    async def run():
        async with database() as (sessions, _):
            owner, other = await seed_user(sessions), await seed_user(sessions)
            note = uuid4()
            async def rows():
                async with sessions() as db:
                    return (await db.execute(text("SELECT * FROM outbox_events ORDER BY created_at,id"))).mappings().all()
            await call(sessions, e2ee.put_document, note, doc(), user_id=owner['id'], _={})
            queued = await rows()
            assert len(queued) == 1
            first = queued[0]
            assert first['aggregate_id'] == owner['id'] and first['aggregate_type'] == 'user_notes'
            assert first['event_type'] == 'notes.updated' and first['payload'] == {}
            # A retried acknowledged write and denied cross-owner write emit nothing.
            await call(sessions, e2ee.put_document, note, doc(), user_id=owner['id'], _={})
            assert await call(sessions, e2ee.put_document, note, doc(), user_id=other['id'], _={}) == 404
            assert len(await rows()) == 1
            failed_note = uuid4()
            async def fail(*args):
                raise RuntimeError('synthetic queue failure')
            with patch.object(e2ee, 'enqueue_notes_change', fail):
                try:
                    await call(sessions, e2ee.put_document, failed_note, doc(), user_id=owner['id'], _={})
                    raise AssertionError('write incorrectly succeeded')
                except RuntimeError:
                    pass
            async with sessions() as db:
                assert not await db.scalar(text('SELECT count(*) FROM notes WHERE id=:id'), {'id': failed_note})
            assert len(await rows()) == 1
            body = e2ee.DeleteDocumentRequest(base_document_revision=1)
            await call(sessions, e2ee.delete_document, note, body, user_id=owner['id'], _={})
            await call(sessions, e2ee.delete_document, note, body, user_id=owner['id'], _={})
            assert [row['event_type'] for row in await rows()] == ['notes.updated', 'notes.deleted']
            failed = Redis(fail=True)
            try:
                await publish_once(failed, sessions)
                raise AssertionError('Redis failure incorrectly acknowledged')
            except ConnectionError:
                pass
            assert all(row['published_at'] is None for row in await rows())
            delivered = Redis()
            assert await publish_once(delivered, sessions) == 2
            assert all(row['published_at'] is not None for row in await rows())
            assert [event[1]['source_event_id'] for event in failed.events] == [event[1]['source_event_id'] for event in delivered.events]
            for _, fields in delivered.events:
                assert fields['topic'] == 'notes.events' and fields['owner_id'] == str(owner['id'])
                assert json.loads(fields['payload']) in ({'name': 'notes.updated', 'owner_id': str(owner['id'])}, {'name': 'notes.deleted', 'owner_id': str(owner['id'])})
                assert str(note) not in fields['payload'] and 'ciphertext' not in fields['payload']
    asyncio.run(run())
