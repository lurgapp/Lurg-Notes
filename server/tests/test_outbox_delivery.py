import asyncio
import json
from uuid import uuid4
import pytest
from app.modules.sync.outbox import event_fields, publish_once


def event():
    return {"id": uuid4(), "aggregate_type": "user_graph", "aggregate_id": uuid4(),
            "event_type": "graph.updated", "payload": {"candidate_id": str(uuid4()), "user_id": "untrusted", "email": "private@example.com", "content": "private content"}}


class Rows:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def all(self): return self.rows


class Db:
    def __init__(self, rows): self.rows, self.commits, self.marked = rows, 0, []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def execute(self, statement, params=None):
        sql = str(statement)
        if "UPDATE outbox_events" in sql: self.marked.append(params["id"])
        return Rows(self.rows if "SELECT id," in sql else [])
    async def commit(self): self.commits += 1


class Redis:
    def __init__(self, fail=False): self.fail, self.events = fail, []
    def pipeline(self, transaction=False): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def xadd(self, stream, fields, **kwargs): self.events.append((stream, fields))
    async def execute(self):
        if self.fail: raise ConnectionError("synthetic unavailable")
        return ["1-0"] * len(self.events)


def test_outbox_private_envelope_is_sanitized_and_owner_is_trusted():
    row = event()
    fields = event_fields(row)
    assert fields["owner_id"] == str(row["aggregate_id"])
    assert fields["source_event_id"] == str(row["id"])
    assert json.loads(fields["payload"]) == {"name": "graph.updated", "candidate_id": row["payload"]["candidate_id"]}
    assert "private content" not in fields["payload"] and "email" not in fields["payload"]


def test_outbox_marks_published_only_after_stream_ack():
    row = event()
    db, redis = Db([row]), Redis()
    assert asyncio.run(publish_once(redis, lambda: db)) == 1
    assert db.commits == 1 and db.marked == [row["id"]]
    assert redis.events[0][0] == "lcp:v1:event-stream"


def test_outbox_failure_keeps_rows_unpublished_and_stable_retry_source():
    row = event()
    failed_db, failed_redis = Db([row]), Redis(fail=True)
    with pytest.raises(ConnectionError): asyncio.run(publish_once(failed_redis, lambda: failed_db))
    assert failed_db.commits == 0 and failed_db.marked == []
    retry_db, retry_redis = Db([row]), Redis()
    asyncio.run(publish_once(retry_redis, lambda: retry_db))
    assert failed_redis.events[0][1]["source_event_id"] == retry_redis.events[0][1]["source_event_id"]


def test_notes_push_accepts_verified_release_url_shapes_only():
    from app.modules.app.router import NotesUpdatePushRequest
    from pydantic import ValidationError
    for url in ["https://notes.lurg.pro/download/lurg-notes.apk", "https://notes.lurg.pro/download/lurg-notes-19-" + "a" * 64 + ".apk"]:
        assert NotesUpdatePushRequest(version_code=19, version_name="1.0.3", apk_url=url).apk_url == url
    for url in ["http://notes.lurg.pro/download/lurg-notes.apk", "https://evil.example/download/lurg-notes.apk", "https://notes.lurg.pro/download/lurg-notes-19-bad.apk"]:
        with pytest.raises(ValidationError):
            NotesUpdatePushRequest(version_code=19, version_name="1.0.3", apk_url=url)
