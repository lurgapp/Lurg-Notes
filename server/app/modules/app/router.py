import hmac
import httpx
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_user
from ...db import get_db
from ...config import settings
from .releases import APPS, queue_release_push, release_metadata, verify_public_manifest

router = APIRouter(prefix="/app", tags=["App"])


class PushTokenRegistration(BaseModel):
    app_id: Literal["notes", "lurg"] = "notes"
    provider: str = Field(pattern="^(expo|fcm)$")
    platform: str = Field(pattern="^android$")
    device_id: str = Field(min_length=8, max_length=160)
    token: str = Field(min_length=20, max_length=512)
    app_version_name: str = Field(min_length=1, max_length=40)
    app_version_code: int = Field(ge=1)
    build_identifier: str = Field(min_length=1, max_length=160)


@router.post("/devices/push-token")
async def register_push_token(
    body: PushTokenRegistration,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # An FCM registration token identifies one app installation.  It can be
    # re-reported after an app restore or device-id rotation, so retire any
    # older active record before the regular per-device upsert.  Without this,
    # one physical installation can receive a campaign twice.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:token_lock))"),
        {"token_lock": f"{body.app_id}:{body.provider}:{body.token}"},
    )
    await db.execute(
        text(
            """
            UPDATE app_device_tokens
            SET status = 'inactive', updated_at = now(), deactivated_at = now()
            WHERE app_id = :app_id
              AND push_provider = :provider
              AND push_token = :token
              AND status = 'active'
              AND (user_id <> :user_id OR device_id <> :device_id)
            """
        ),
        {
            "app_id": body.app_id,
            "provider": body.provider,
            "token": body.token,
            "user_id": user_id,
            "device_id": body.device_id,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO app_device_tokens (
              user_id, device_id, app_id, platform, push_provider, push_token,
              app_version_name, app_version_code, build_identifier,
              status, updated_at, deactivated_at
            )
            VALUES (
              :user_id, :device_id, :app_id, :platform, :provider, :token,
              :app_version_name, :app_version_code, :build_identifier,
              'active', now(), NULL
            )
            ON CONFLICT (user_id, device_id, push_provider, app_id)
            DO UPDATE SET
              platform = EXCLUDED.platform,
              push_token = EXCLUDED.push_token,
              app_version_name = EXCLUDED.app_version_name,
              app_version_code = EXCLUDED.app_version_code,
              build_identifier = EXCLUDED.build_identifier,
              status = 'active',
              updated_at = now(),
              deactivated_at = NULL
            """
        ),
        {
            "user_id": user_id,
            "device_id": body.device_id,
            "app_id": body.app_id,
            "platform": body.platform,
            "provider": body.provider,
            "token": body.token,
            "app_version_name": body.app_version_name,
            "app_version_code": body.app_version_code,
            "build_identifier": body.build_identifier,
        },
    )
    await db.commit()
    return {"status": "ok"}


class NotesUpdatePushRequest(BaseModel):
    version_code: int = Field(ge=1)
    version_name: str = Field(min_length=1, max_length=40)
    apk_url: str = Field(max_length=300, pattern=r"^https://notes\.lurg\.pro/download/lurg-notes(?:-[1-9]\d*-[a-f0-9]{64})?\.apk$")


class AppUpdatePushRequest(BaseModel):
    app_id: Literal["notes", "lurg"]
    version_code: int = Field(ge=1, le=2147483647)
    version_name: str = Field(min_length=1, max_length=40)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_name: str = Field(max_length=80)
    manifest_url: str = Field(max_length=300)
    apk_url: str = Field(max_length=300)

    @model_validator(mode="after")
    def canonical_release(self):
        expected = release_metadata(self.app_id, self.version_code, self.version_name, self.sha256)
        if any(getattr(self, key) != expected[key] for key in ("package_name", "manifest_url", "apk_url")):
            raise ValueError("Release package and URLs must match the selected app and immutable artifact")
        return self


def require_release_token(token: str | None):
    if not settings.push_internal_token or not token or not hmac.compare_digest(token.encode(), settings.push_internal_token.encode()):
        raise HTTPException(403, "Недостаточно прав")


@router.post("/internal/notifications/app-update")
async def notify_app_update(body: AppUpdatePushRequest, x_lurg_push_token: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    require_release_token(x_lurg_push_token)
    metadata = body.model_dump()
    try:
        await verify_public_manifest(metadata)
    except httpx.HTTPError:
        raise HTTPException(502, "Не удалось проверить опубликованный релиз") from None
    except ValueError:
        raise HTTPException(409, "Опубликованный manifest не соответствует релизу") from None
    try:
        return await queue_release_push(db, metadata)
    except ValueError:
        raise HTTPException(409, "Версия уже зарегистрирована с другим содержимым или устарела") from None


@router.post("/internal/notifications/notes-update")
async def notify_notes_update(
    body: NotesUpdatePushRequest,
    x_lurg_push_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    # Backwards-compatible publisher entrypoint; it now creates the same
    # durable release/jobs and never sends an immediate second campaign.
    require_release_token(x_lurg_push_token)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(APPS["notes"]["manifest"], headers={"Cache-Control": "no-cache"})
            response.raise_for_status()
            manifest = response.json()
        metadata = release_metadata("notes", body.version_code, body.version_name, manifest["sha256"])
        if body.apk_url not in (metadata["apk_url"], "https://notes.lurg.pro/download/lurg-notes.apk"):
            raise ValueError("APK does not match release")
        canonical = AppUpdatePushRequest(**metadata)
    except httpx.HTTPError:
        raise HTTPException(502, "Не удалось проверить опубликованный релиз") from None
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "Опубликованный manifest не соответствует релизу") from None
    return await notify_app_update(canonical, x_lurg_push_token, db)


@router.delete("/devices/{device_id}/push-token", status_code=204)
async def deactivate_push_token(
    device_id: str,
    app_id: Literal["notes", "lurg"] = "notes",
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text(
            """
            UPDATE app_device_tokens
            SET status = 'inactive', updated_at = now(), deactivated_at = now()
            WHERE user_id = :user_id AND device_id = :device_id AND app_id = :app_id
            """
        ),
        {"user_id": user_id, "device_id": device_id, "app_id": app_id},
    )
    await db.commit()
