"""Published app releases and durable, scoped notification jobs."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
from firebase_admin import messaging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .push import firebase_app, firebase_enabled

APPS = {
    "notes": {"package": "pro.lurg.notes", "manifest": "https://notes.lurg.pro/update.json", "apk_prefix": "https://notes.lurg.pro/download/lurg-notes", "name": "Lurg Notes", "channel": "lurg_notes_updates", "app": "lurg_notes"},
    "lurg": {"package": "pro.lurg.mobile", "manifest": "https://lurg.pro/app/version.json", "apk_prefix": "https://lurg.pro/app/lurg", "name": "Lurg", "channel": "lurg_general", "app": "lurg"},
}
MAX_ATTEMPTS = 8
SEND_TIMEOUT_SECONDS = 15
_send_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="release-fcm")


def release_metadata(app_id: str, version_code: int, version_name: str, sha256: str) -> dict:
    app = APPS[app_id]
    return {"app_id": app_id, "version_code": version_code, "version_name": version_name, "sha256": sha256,
            "package_name": app["package"], "manifest_url": app["manifest"],
            "apk_url": f"{app['apk_prefix']}-{version_code}-{sha256}.apk"}


async def verify_public_manifest(metadata: dict) -> None:
    # Fixed server-side URL; no publisher-controlled network destinations.
    # Publisher verifies the immutable APK bytes before promoting the manifest.
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(metadata["manifest_url"], params={"release": metadata["sha256"]}, headers={"Cache-Control": "no-cache", "User-Agent": "LurgReleaseVerifier/1.0"})
        response.raise_for_status()
        manifest = response.json()
    if not isinstance(manifest, dict):
        raise ValueError("Public manifest is not an object")
    for key in ("version_code", "version_name", "sha256", "package_name", "apk_url"):
        if manifest.get(key) != metadata[key]:
            raise ValueError("Public manifest does not match release")


async def queue_release_push(db: AsyncSession, metadata: dict) -> dict:
    app_id = metadata["app_id"]
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "release-push:" + app_id})
    previous = (await db.execute(text("SELECT * FROM app_releases WHERE app_id=:app AND version_code=:code"), {"app": app_id, "code": metadata["version_code"]})).mappings().first()
    if previous:
        if previous["sha256"] != metadata["sha256"]:
            raise ValueError("Version code already registered with different content")
        targeted = await db.scalar(text("SELECT count(*) FROM app_release_deliveries WHERE release_id=:id"), {"id": previous["id"]})
        await db.commit()
        return {"release_id": str(previous["id"]), "targeted": targeted, "deduplicated": True}
    latest = await db.scalar(text("SELECT max(version_code) FROM app_releases WHERE app_id=:app"), {"app": app_id})
    if latest is not None and metadata["version_code"] <= latest:
        raise ValueError("A newer release is already registered")
    release_id = await db.scalar(text("""INSERT INTO app_releases(app_id,version_code,version_name,sha256,package_name,apk_url,manifest_url)
      VALUES(:app_id,:version_code,:version_name,:sha256,:package_name,:apk_url,:manifest_url) RETURNING id"""), metadata)
    deliveries = await db.execute(text("""INSERT INTO app_release_deliveries(release_id,device_token_id,user_id,token_hash)
      SELECT :release,d.id,d.user_id,digest(d.push_token,'sha256') FROM (
      SELECT DISTINCT ON (d.push_token) d.id,d.user_id,d.push_token
      FROM app_device_tokens d JOIN users u ON u.id=d.user_id
      WHERE d.app_id=:app AND d.push_provider='fcm' AND d.platform='android' AND d.status='active'
        AND d.app_version_code < :code AND u.status='active'
        AND u.preferences->'notifications_enabled' IS DISTINCT FROM 'false'::jsonb
      ORDER BY d.push_token,d.updated_at DESC,d.id DESC) d ON CONFLICT DO NOTHING"""),
      {"release": release_id, "app": app_id, "code": metadata["version_code"]})
    await db.commit()
    return {"release_id": str(release_id), "targeted": deliveries.rowcount or 0, "deduplicated": False}


def update_message(row):
    app = APPS[row["app_id"]]
    data = {"type": "app_update", "app_id": row["app_id"], "app": app["app"],
            "release_id": str(row["release_id"]), "package_name": row["package_name"],
            "manifest_url": row["manifest_url"], "version_code": str(row["version_code"]),
            "version_name": row["version_name"], "sha256": row["sha256"]}
    # Default launcher is compatible with clients predating UPDATE filters.
    return messaging.Message(token=row["push_token"], notification=messaging.Notification(
        title=f"Доступно обновление {app['name']}", body=f"Версия {row['version_name']} уже готова."),
        data=data, android=messaging.AndroidConfig(priority="high", notification=messaging.AndroidNotification(channel_id=app["channel"])))


async def deliver_app_release_batch(db: AsyncSession, batch_size: int = 4) -> int:
    if await db.scalar(text("SELECT enabled FROM operations_controls WHERE key='push_paused'")):
        return 0
    if not firebase_enabled():
        return 0
    firebase_app()
    lease = uuid4()
    rows = (await db.execute(text("""SELECT q.id,q.release_id,q.user_id AS queued_user,q.token_hash,q.attempts,
      d.id AS token_id,d.user_id,d.push_token,d.status AS token_status,d.app_id AS token_app,d.app_version_code,
      u.status AS user_status,u.preferences->'notifications_enabled' AS notifications_enabled,
      r.app_id,r.version_code,r.version_name,r.package_name,r.manifest_url,r.sha256,
      (SELECT max(version_code) FROM app_releases newer WHERE newer.app_id=r.app_id) AS latest_code
      FROM app_release_deliveries q JOIN app_releases r ON r.id=q.release_id
      JOIN app_device_tokens d ON d.id=q.device_token_id JOIN users u ON u.id=d.user_id
      WHERE (q.status='pending' AND q.next_attempt_at<=now()) OR (q.status='sending' AND q.lease_until<now())
      ORDER BY q.created_at LIMIT :limit FOR UPDATE OF q SKIP LOCKED"""), {"limit": min(max(batch_size, 1), 4)})).mappings().all()
    eligible = []
    for row in rows:
        valid = (row["token_status"] == "active" and row["user_status"] == "active" and row["notifications_enabled"] is not False
                 and row["queued_user"] == row["user_id"] and row["token_app"] == row["app_id"]
                 and row["attempts"] < MAX_ATTEMPTS and row["app_version_code"] < row["version_code"] and row["version_code"] == row["latest_code"]
                 and hmac.compare_digest(bytes(row["token_hash"]), hashlib.sha256(row["push_token"].encode()).digest()))
        if not valid:
            await db.execute(text("UPDATE app_release_deliveries SET status=:status,lease_token=NULL,lease_until=NULL,error_code=:error WHERE id=:id"), {"id": row["id"], "status": "failed" if row["attempts"] >= MAX_ATTEMPTS else "skipped", "error": "retry_exhausted" if row["attempts"] >= MAX_ATTEMPTS else "recipient_changed"})
        else:
            await db.execute(text("UPDATE app_release_deliveries SET status='sending',attempts=attempts+1,lease_token=:lease,lease_until=now()+interval '5 minutes' WHERE id=:id"), {"id": row["id"], "lease": lease})
            eligible.append(row)
    await db.commit()
    semaphore = asyncio.Semaphore(4)

    async def send(row):
        async with semaphore:
            try:
                future = asyncio.get_running_loop().run_in_executor(_send_executor, messaging.send, update_message(row))
                await asyncio.wait_for(future, timeout=SEND_TIMEOUT_SECONDS)
                return "sent"
            except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
                return "invalid_token"
            except Exception:
                # Never persist provider exception text or a registration token.
                return "delivery_error"

    outcomes = await asyncio.gather(*(send(row) for row in eligible))
    for row, outcome in zip(eligible, outcomes):
        attempt = row["attempts"] + 1
        status = "sent" if outcome == "sent" else "failed" if outcome == "invalid_token" or attempt >= MAX_ATTEMPTS else "pending"
        updated = await db.execute(text("""UPDATE app_release_deliveries SET status=:status,lease_token=NULL,lease_until=NULL,
          error_code=:error,delivered_at=CASE WHEN :success THEN now() ELSE delivered_at END,
          next_attempt_at=now()+make_interval(secs=>:delay) WHERE id=:id AND lease_token=:lease"""),
          {"id": row["id"], "lease": lease, "status": status, "success": status == "sent", "error": None if outcome == "sent" else outcome, "delay": min(3600, 30 * 2 ** (attempt - 1))})
        if outcome == "invalid_token" and updated.rowcount:
            await db.execute(text("UPDATE app_device_tokens SET status='inactive',deactivated_at=now(),updated_at=now() WHERE id=:id AND digest(push_token,'sha256')=:hash AND user_id=:user AND app_id=:app"), {"id": row["token_id"], "hash": bytes(row["token_hash"]), "user": row["queued_user"], "app": row["app_id"]})
    await db.commit()
    return len(rows)
