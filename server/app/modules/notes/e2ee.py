"""Opaque, device-authorized E2EE v2 transport for Lurg Notes.

The API deliberately has no decryption operation. All crypto is performed by
the Android client; this module only validates bounded opaque payloads.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_user
from ...db import get_db


router = APIRouter(prefix="/notes/e2ee", tags=["Lurg Notes E2EE v2"])

DEVICE_HEADER = "X-Lurg-Notes-Device-Id"
CREDENTIAL_HEADER = "X-Lurg-Notes-Device-Credential"
B64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-")


def opaque_token() -> str:
    return secrets.token_urlsafe(32)


def credential_digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def valid_b64(value: str, minimum: int = 1) -> str:
    if len(value) < minimum or any(char not in B64_CHARS for char in value):
        raise ValueError("malformed base64 material")
    try:
        padded = value.replace("-", "+").replace("_", "/") + "=" * (-len(value) % 4)
        base64.b64decode(padded, validate=True)
    except Exception as error:
        raise ValueError("malformed base64 material") from error
    return value


class DeviceRegisterRequest(BaseModel):
    device_id: UUID
    name: str = Field(min_length=1, max_length=120)
    public_key: str = Field(min_length=300, max_length=20000)

    @field_validator("public_key")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        return valid_b64(value, 300)


class WrappedKey(BaseModel):
    wrapped_umk: str = Field(min_length=64, max_length=20000)
    wrap_algorithm: str = Field(pattern="^RSA-OAEP-SHA-256$", max_length=64)
    wrap_context: str = Field(min_length=10, max_length=300)

    @field_validator("wrapped_umk")
    @classmethod
    def validate_wrapped_umk(cls, value: str) -> str:
        return valid_b64(value, 64)


class DeviceApproveRequest(WrappedKey):
    pass


class EncryptedDocument(BaseModel):
    format_version: int = Field(ge=2, le=2)
    key_version: int = Field(ge=2, le=2)
    algorithm: str = Field(pattern="^AES-256-GCM$", max_length=40)
    nonce: str = Field(min_length=16, max_length=128)
    ciphertext: str = Field(min_length=16, max_length=2_000_000)
    content_hash: str = Field(min_length=32, max_length=128)
    document_revision: int = Field(ge=1)
    base_document_revision: int | None = Field(default=None, ge=0)

    @field_validator("nonce")
    @classmethod
    def validate_nonce(cls, value: str) -> str:
        valid_b64(value, 16)
        if len(base64.b64decode(value.replace("-", "+").replace("_", "/") + "=" * (-len(value) % 4))) != 12:
            raise ValueError("nonce must be 96 bits")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return valid_b64(value, 32)

    @field_validator("ciphertext")
    @classmethod
    def validate_ciphertext(cls, value: str) -> str:
        return valid_b64(value, 16)


class RecoverySetupRequest(BaseModel):
    recovery_salt: str = Field(min_length=16, max_length=128)
    nonce: str = Field(min_length=16, max_length=128)
    ciphertext: str = Field(min_length=32, max_length=20000)
    recovery_auth_verifier: str = Field(min_length=32, max_length=128)

    @field_validator("recovery_salt", "nonce", "ciphertext", "recovery_auth_verifier")
    @classmethod
    def validate_material(cls, value: str) -> str:
        return valid_b64(value, 16)


class RecoveryEnrollRequest(DeviceRegisterRequest):
    recovery_proof: str = Field(min_length=32, max_length=128)

    @field_validator("recovery_proof")
    @classmethod
    def validate_proof(cls, value: str) -> str:
        return valid_b64(value, 32)


async def device_from_headers(
    user_id: UUID,
    db: AsyncSession,
    device_header: Annotated[str | None, Header(alias=DEVICE_HEADER)] = None,
    credential: Annotated[str | None, Header(alias=CREDENTIAL_HEADER)] = None,
    *,
    active: bool = True,
) -> dict:
    if not device_header or not credential or len(credential) > 256 or not credential.isascii():
        raise HTTPException(401, "Требуется credential доверенного устройства Notes.")
    try:
        parsed_id = UUID(device_header)
    except ValueError as error:
        raise HTTPException(401, "Некорректный идентификатор устройства.") from error
    row = (await db.execute(text("""SELECT id,user_id,status,credential_hash FROM note_e2ee_devices
      WHERE id=:id AND user_id=:uid"""), {"id": parsed_id, "uid": user_id})).mappings().first()
    if not row or not hmac.compare_digest(row["credential_hash"], credential_digest(credential)):
        raise HTTPException(401, "Credential устройства недействителен.")
    if active and row["status"] != "active":
        raise HTTPException(403, "Устройство не одобрено или отозвано.")
    if not active and row["status"] == "revoked":
        raise HTTPException(403, "Устройство отозвано.")
    return dict(row)


async def active_device(
    user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db),
    device_header: Annotated[str | None, Header(alias=DEVICE_HEADER)] = None,
    credential: Annotated[str | None, Header(alias=CREDENTIAL_HEADER)] = None,
) -> dict:
    return await device_from_headers(user_id, db, device_header, credential, active=True)


async def registered_device(
    user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db),
    device_header: Annotated[str | None, Header(alias=DEVICE_HEADER)] = None,
    credential: Annotated[str | None, Header(alias=CREDENTIAL_HEADER)] = None,
) -> dict:
    return await device_from_headers(user_id, db, device_header, credential, active=False)


def device_view(row: dict, include_public_key: bool = False) -> dict:
    result = {key: row[key] for key in ("id", "name", "public_key_fingerprint", "status", "created_at", "approved_at", "revoked_at") if key in row}
    if include_public_key:
        result["public_key"] = row["public_key"]
    return result


@router.post("/devices", status_code=201)
async def register_device(body: DeviceRegisterRequest, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await lock_notes_owner(db, user_id)
    fingerprint = hashlib.sha256(base64.b64decode(body.public_key.replace("-", "+").replace("_", "/") + "=" * (-len(body.public_key) % 4))).hexdigest()
    existing = (await db.execute(text("SELECT * FROM note_e2ee_devices WHERE id=:id AND user_id=:uid"), {"id": body.device_id, "uid": user_id})).mappings().first()
    if existing:
        if existing["public_key_fingerprint"] != fingerprint:
            raise HTTPException(409, "Идентификатор устройства уже связан с другим ключом.")
        # Never reissue the credential: a caller which lost it must use recovery.
        raise HTTPException(409, "Устройство уже зарегистрировано.")
    active_count = int(await db.scalar(text("SELECT count(*) FROM note_e2ee_devices WHERE user_id=:uid"), {"uid": user_id}) or 0)
    credential = opaque_token()
    status = "active" if active_count == 0 else "pending"
    approved_at = datetime.now(timezone.utc) if status == "active" else None
    row = (await db.execute(text("""INSERT INTO note_e2ee_devices(
      id,user_id,name,public_key,public_key_fingerprint,credential_hash,status,approved_at
    ) VALUES(:id,:uid,:name,:public_key,:fingerprint,:credential,:status,:approved_at) RETURNING *"""), {
        "id": body.device_id, "uid": user_id, "name": body.name.strip(), "public_key": body.public_key,
        "fingerprint": fingerprint, "credential": credential_digest(credential), "status": status, "approved_at": approved_at,
    })).mappings().one()
    await db.commit()
    return {"device": device_view(dict(row)), "device_credential": credential}


@router.get("/devices/me")
async def my_device(device: dict = Depends(registered_device)):
    return {"device": device_view(device)}


@router.get("/devices")
async def list_devices(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), _: dict = Depends(active_device)):
    rows = (await db.execute(text("SELECT * FROM note_e2ee_devices WHERE user_id=:uid ORDER BY created_at"), {"uid": user_id})).mappings().all()
    return {"devices": [device_view(dict(row), include_public_key=row["status"] == "pending") for row in rows]}


@router.post("/devices/{device_id}/approve")
async def approve_device(device_id: UUID, body: DeviceApproveRequest, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), approver: dict = Depends(active_device)):
    target = (await db.execute(text("SELECT * FROM note_e2ee_devices WHERE id=:id AND user_id=:uid FOR UPDATE"), {"id": device_id, "uid": user_id})).mappings().first()
    if not target or target["status"] != "pending":
        raise HTTPException(409, "Ожидающее одобрения устройство не найдено.")
    expected_context = f"lurg-notes:v2:umk:{user_id}:{device_id}"
    if body.wrap_context != expected_context:
        raise HTTPException(422, "Контекст wrapping не соответствует получателю.")
    row = (await db.execute(text("""UPDATE note_e2ee_devices SET status='active',wrapped_umk=:wrapped,
      wrap_algorithm=:algorithm,wrap_context=:context,approved_by=:approved_by,approved_at=now(),updated_at=now()
      WHERE id=:id AND user_id=:uid RETURNING *"""), {
        "id": device_id, "uid": user_id, "wrapped": body.wrapped_umk, "algorithm": body.wrap_algorithm,
        "context": body.wrap_context, "approved_by": approver["id"],
    })).mappings().one()
    await db.commit()
    return {"device": device_view(dict(row))}


@router.get("/key-envelope")
async def my_key_envelope(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), device: dict = Depends(registered_device)):
    row = (await db.execute(text("SELECT wrapped_umk,wrap_algorithm,wrap_context,status FROM note_e2ee_devices WHERE id=:id AND user_id=:uid"), {"id": device["id"], "uid": user_id})).mappings().one()
    if row["status"] != "active" or not row["wrapped_umk"]:
        raise HTTPException(409, "UMK ещё не предоставлен доверенным устройством.")
    return {"wrapped_umk": row["wrapped_umk"], "wrap_algorithm": row["wrap_algorithm"], "wrap_context": row["wrap_context"]}


@router.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), revoker: dict = Depends(active_device)):
    if UUID(str(revoker["id"])) == device_id:
        raise HTTPException(422, "Нельзя отозвать текущее устройство этим действием.")
    row = (await db.execute(text("""UPDATE note_e2ee_devices SET status='revoked',revoked_at=now(),
      credential_hash=:revoked,updated_at=now() WHERE id=:id AND user_id=:uid AND status!='revoked' RETURNING *"""), {
        "id": device_id, "uid": user_id, "revoked": credential_digest(opaque_token()),
    })).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Устройство не найдено или уже отозвано.")
    await db.commit()
    return {"device": device_view(dict(row))}


class DeleteDocumentRequest(BaseModel):
    base_document_revision: int | None = Field(default=None, ge=0)


async def lock_notes_owner(db: AsyncSession, user_id: UUID) -> None:
    # Sequence numbers alone are not commit ordered. Serialize this owner's
    # short sync writes and reads so a cursor never skips a late commit.
    await db.execute(text("SELECT id FROM users WHERE id=:uid FOR UPDATE"), {"uid": user_id})


def revision_conflict(revision: int) -> HTTPException:
    return HTTPException(409, {"code": "REVISION_CONFLICT", "document_revision": revision})


def validate_document_write(envelope: EncryptedDocument, current: dict | None) -> bool:
    """Return false for a retry; reject stale/missing bases before any mutation."""
    revision = int(current["document_revision"]) if current else 0
    if current and envelope.document_revision == revision and hmac.compare_digest(envelope.content_hash, current["content_hash"]):
        return False
    base = envelope.base_document_revision
    # Older clients may create their first envelope, but may not overwrite an
    # existing v2 document without acknowledging its current revision.
    if base is None:
        base = 0 if current is None else -1
    if base != revision or envelope.document_revision <= revision:
        raise revision_conflict(revision)
    return True


@router.put("/documents/{note_id}")
async def put_document(note_id: UUID, envelope: EncryptedDocument, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), _: dict = Depends(active_device)):
    await lock_notes_owner(db, user_id)
    note = (await db.execute(text("SELECT deleted_at,revision FROM notes WHERE id=:id AND user_id=:uid FOR UPDATE"), {"id": note_id, "uid": user_id})).mappings().first()
    if note and note["deleted_at"] is not None:
        raise HTTPException(410, "Заметка была удалена.")
    current = (await db.execute(text("""SELECT format_version,key_version,algorithm,nonce,ciphertext,content_hash,document_revision
      FROM note_e2ee_documents WHERE note_id=:id AND user_id=:uid AND format_version=2"""), {"id": note_id, "uid": user_id})).mappings().first()
    if not validate_document_write(envelope, dict(current) if current else None):
        return dict(current)
    if not note:
        # ON CONFLICT does not disclose another account's note id or write its
        # envelope; UUID ownership is verified before the document upsert.
        created = (await db.execute(text("""INSERT INTO notes(id,user_id,title,revision)
          VALUES(:id,:uid,'Encrypted note',:revision) ON CONFLICT(id) DO NOTHING RETURNING id"""),
          {"id": note_id, "uid": user_id, "revision": envelope.document_revision})).mappings().first()
        if not created:
            raise HTTPException(404, "Заметка не найдена.")
    row = (await db.execute(text("""INSERT INTO note_e2ee_documents(note_id,user_id,format_version,key_version,algorithm,nonce,ciphertext,content_hash,document_revision)
      VALUES(:note_id,:uid,:format_version,:key_version,:algorithm,:nonce,:ciphertext,:content_hash,:document_revision)
      ON CONFLICT(note_id,format_version) DO UPDATE SET key_version=EXCLUDED.key_version,algorithm=EXCLUDED.algorithm,
        nonce=EXCLUDED.nonce,ciphertext=EXCLUDED.ciphertext,content_hash=EXCLUDED.content_hash,
        document_revision=EXCLUDED.document_revision,updated_at=now()
      WHERE note_e2ee_documents.user_id=:uid
      RETURNING format_version,key_version,algorithm,nonce,ciphertext,content_hash,document_revision"""),
      {"note_id": note_id, "uid": user_id, **envelope.model_dump(exclude={"base_document_revision"})})).mappings().one()
    await db.execute(text("""UPDATE notes SET revision=:revision,updated_at=now(),sync_sequence=nextval('notes_sync_sequence')
      WHERE id=:id AND user_id=:uid"""), {"revision": envelope.document_revision, "id": note_id, "uid": user_id})
    await enqueue_notes_change(db, user_id, "notes.updated")
    await db.commit()
    return dict(row)


@router.get("/sync")
async def sync_documents(cursor: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100), user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), _: dict = Depends(active_device)):
    await lock_notes_owner(db, user_id)
    rows = (await db.execute(text("""SELECT n.id AS note_id,n.revision,n.deleted_at,n.sync_sequence,
      d.format_version,d.key_version,d.algorithm,d.nonce,d.ciphertext,d.content_hash,d.document_revision,d.updated_at
      FROM notes n LEFT JOIN note_e2ee_documents d ON d.note_id=n.id AND d.user_id=n.user_id AND d.format_version=2
      WHERE n.user_id=:uid AND n.sync_sequence>:cursor AND (n.deleted_at IS NOT NULL OR d.note_id IS NOT NULL)
      ORDER BY n.sync_sequence LIMIT :limit"""), {"uid": user_id, "cursor": cursor, "limit": limit + 1})).mappings().all()
    page = rows[:limit]
    documents = []
    tombstones = []
    for row in page:
        if row["deleted_at"] is not None:
            tombstones.append({"note_id": row["note_id"], "document_revision": row["revision"], "deleted_at": row["deleted_at"]})
        else:
            documents.append({key: row[key] for key in ("note_id", "format_version", "key_version", "algorithm", "nonce", "ciphertext", "content_hash", "document_revision", "updated_at")})
    next_cursor = page[-1]["sync_sequence"] if page else cursor
    await db.commit()
    return {"documents": documents, "tombstones": tombstones, "next_cursor": str(next_cursor), "has_more": len(rows) > limit}


@router.get("/documents")
async def list_documents(cursor: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=200), user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), _: dict = Depends(active_device)):
    await lock_notes_owner(db, user_id)
    rows = (await db.execute(text("""SELECT d.note_id,d.format_version,d.key_version,d.algorithm,d.nonce,d.ciphertext,d.content_hash,d.document_revision,d.updated_at,n.sync_sequence
      FROM note_e2ee_documents d JOIN notes n ON n.id=d.note_id AND n.user_id=d.user_id
      WHERE d.user_id=:uid AND d.format_version=2 AND n.deleted_at IS NULL AND n.sync_sequence>:cursor
      ORDER BY n.sync_sequence LIMIT :limit"""), {"uid": user_id, "cursor": cursor, "limit": limit + 1})).mappings().all()
    page = rows[:limit]
    await db.commit()
    return {"documents": [{key: value for key, value in dict(row).items() if key != "sync_sequence"} for row in page], "next_cursor": str(page[-1]["sync_sequence"] if page else cursor), "has_more": len(rows) > limit}


@router.get("/documents/{note_id}")
async def get_document(note_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), _: dict = Depends(active_device)):
    row = (await db.execute(text("""SELECT d.format_version,d.key_version,d.algorithm,d.nonce,d.ciphertext,d.content_hash,d.document_revision,d.updated_at
      FROM note_e2ee_documents d JOIN notes n ON n.id=d.note_id AND n.user_id=d.user_id
      WHERE d.note_id=:id AND d.user_id=:uid AND d.format_version=2 AND n.deleted_at IS NULL"""), {"id": note_id, "uid": user_id})).mappings().first()
    if not row:
        raise HTTPException(404, "Зашифрованная версия не найдена.")
    return dict(row)


@router.get("/tombstones")
async def list_tombstones(cursor: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=200), user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), _: dict = Depends(active_device)):
    await lock_notes_owner(db, user_id)
    rows = (await db.execute(text("""SELECT id AS note_id,revision AS document_revision,deleted_at,sync_sequence
      FROM notes WHERE user_id=:uid AND deleted_at IS NOT NULL AND sync_sequence>:cursor ORDER BY sync_sequence LIMIT :limit"""), {"uid": user_id, "cursor": cursor, "limit": limit + 1})).mappings().all()
    page = rows[:limit]
    await db.commit()
    return {"tombstones": [{key: value for key, value in dict(row).items() if key != "sync_sequence"} for row in page], "next_cursor": str(page[-1]["sync_sequence"] if page else cursor), "has_more": len(rows) > limit}


@router.post("/documents/{note_id}/delete", status_code=204)
async def delete_document(note_id: UUID, body: DeleteDocumentRequest | None = None, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), _: dict = Depends(active_device)):
    await lock_notes_owner(db, user_id)
    state = (await db.execute(text("SELECT deleted_at,revision FROM notes WHERE id=:id AND user_id=:uid FOR UPDATE"), {"id": note_id, "uid": user_id})).mappings().first()
    if not state or state["deleted_at"] is not None:
        return
    if body is None or body.base_document_revision != state["revision"]:
        raise revision_conflict(state["revision"])
    await db.execute(text("""UPDATE notes SET deleted_at=now(),updated_at=now(),revision=revision+1,sync_sequence=nextval('notes_sync_sequence')
      WHERE id=:id AND user_id=:uid"""), {"id": note_id, "uid": user_id})
    await enqueue_notes_change(db, user_id, "notes.deleted")
    await db.commit()


async def enqueue_notes_change(db: AsyncSession, user_id: UUID, name: str) -> None:
    # The invalidation and ciphertext mutation commit together. Never place
    # plaintext, ciphertext, device credentials or note IDs in the event bus.
    await db.execute(text("""INSERT INTO outbox_events(aggregate_type,aggregate_id,event_type,payload)
      VALUES('user_notes',:uid,:name,'{}'::jsonb)"""), {"uid": user_id, "name": name})


@router.put("/recovery")
async def setup_recovery(body: RecoverySetupRequest, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db), _: dict = Depends(active_device)):
    await db.execute(text("""INSERT INTO note_e2ee_recovery(user_id,recovery_salt,nonce,ciphertext,recovery_auth_verifier)
      VALUES(:uid,:salt,:nonce,:ciphertext,:verifier)
      ON CONFLICT(user_id) DO UPDATE SET recovery_salt=EXCLUDED.recovery_salt,nonce=EXCLUDED.nonce,
      ciphertext=EXCLUDED.ciphertext,recovery_auth_verifier=EXCLUDED.recovery_auth_verifier,version=note_e2ee_recovery.version+1,updated_at=now()"""), {
        "uid": user_id, "salt": body.recovery_salt, "nonce": body.nonce, "ciphertext": body.ciphertext,
        "verifier": body.recovery_auth_verifier,
    })
    await db.commit()
    return {"status": "stored"}


@router.get("/recovery")
async def recovery_envelope(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(text("SELECT recovery_salt,nonce,ciphertext,version FROM note_e2ee_recovery WHERE user_id=:uid"), {"uid": user_id})).mappings().first()
    if not row:
        raise HTTPException(404, "Recovery material не настроен.")
    return dict(row)


@router.post("/recovery/enroll", status_code=201)
async def recovery_enroll(body: RecoveryEnrollRequest, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    recovery = (await db.execute(text("SELECT recovery_auth_verifier FROM note_e2ee_recovery WHERE user_id=:uid"), {"uid": user_id})).mappings().first()
    fingerprint = hashlib.sha256(base64.b64decode(body.public_key.replace("-", "+").replace("_", "/") + "=" * (-len(body.public_key) % 4))).hexdigest()
    if not recovery:
        raise HTTPException(403, "Recovery proof недействителен.")
    auth_key = base64.b64decode(recovery["recovery_auth_verifier"] + "=" * (-len(recovery["recovery_auth_verifier"]) % 4))
    context = f"lurg-notes:v2:recovery-enroll:{user_id}:{body.device_id}:{fingerprint}".encode("utf-8")
    expected_proof = base64.b64encode(hmac.new(auth_key, context, hashlib.sha256).digest()).decode("ascii")
    if not hmac.compare_digest(expected_proof, body.recovery_proof):
        raise HTTPException(403, "Recovery proof недействителен.")
    existing = await db.scalar(text("SELECT 1 FROM note_e2ee_devices WHERE id=:id AND user_id=:uid"), {"id": body.device_id, "uid": user_id})
    if existing:
        raise HTTPException(409, "Идентификатор устройства уже зарегистрирован.")
    credential = opaque_token()
    row = (await db.execute(text("""INSERT INTO note_e2ee_devices(id,user_id,name,public_key,public_key_fingerprint,credential_hash,status,approved_at)
      VALUES(:id,:uid,:name,:public_key,:fingerprint,:credential,'active',now()) RETURNING *"""), {
        "id": body.device_id, "uid": user_id, "name": body.name.strip(), "public_key": body.public_key,
        "fingerprint": fingerprint, "credential": credential_digest(credential),
    })).mappings().one()
    await db.commit()
    return {"device": device_view(dict(row)), "device_credential": credential}
