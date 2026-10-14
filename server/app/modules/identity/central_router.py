"""Versioned API served exclusively by https://auth.lurg.pro/api/v1.

This module is deliberately independent from product backends.  It is the
single owner of passwords, email verification, refresh tokens and logout for
the Lurg ecosystem.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import asyncio
from datetime import datetime, timedelta, timezone
from secrets import randbelow, token_urlsafe
from uuid import UUID
from typing import Awaitable, Callable

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import digest_token, hash_password_async, load_access_identity, make_access_token, verify_password_async
from ...config import settings
from ...db import get_db
from ..admin.accounts import release_expired_blocks
from ...schemas import EmailAddress, EmailCode, EmailRegistrationRequest, Login, RefreshRequest, PasswordResetConfirm
from .email import EmailDeliveryError, send_verification_code

router = APIRouter(prefix="/v1/auth", tags=["Central Identity"])
bearer = HTTPBearer(auto_error=False)
OTP_TTL_SECONDS = 5 * 60
RESEND_SECONDS = 60
MAX_OTP_ATTEMPTS = 5


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _email(value: str) -> str:
    return value.strip().lower()


def _otp_digest(code: str) -> str:
    secret = settings.auth_otp_hmac_secret or settings.jwt_secret
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


async def _app_name(
    db: AsyncSession = Depends(get_db),
    x_lurg_app_name: str | None = Header(default=None, alias="X-Lurg-App-Name"),
) -> str:
    supplied = (x_lurg_app_name or "").strip()
    if not supplied:
        raise _error(400, "APP_NAME_REQUIRED", "Укажите имя приложения")
    row = (await db.execute(text("SELECT display_name FROM auth_applications WHERE slug=:slug AND active=true"), {"slug": supplied.lower()})).mappings().first()
    if not row:
        raise _error(400, "UNKNOWN_APP", "Приложение не зарегистрировано в Lurg Auth")
    return str(row["display_name"])


async def _otp_lock(db: AsyncSession, email: str, purpose: str) -> None:
    # Serializes both existing and absent rows; a SELECT FOR UPDATE alone cannot
    # prevent two first requests from sending different codes simultaneously.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": f"auth-email:{email}"})


async def _issue_code(*, db: AsyncSession, email: str, purpose: str, app_name: str, metadata: dict | None = None, metadata_factory: Callable[[], Awaitable[dict]] | None = None) -> dict:
    await _otp_lock(db, email, purpose)
    existing = (await db.execute(text("SELECT resend_after,metadata FROM auth_email_otps WHERE email=:email AND purpose=:purpose FOR UPDATE"), {"email": email, "purpose": purpose})).mappings().first()
    now = _now()
    if existing and existing["resend_after"] > now:
        seconds = max(1, int((existing["resend_after"] - now).total_seconds()))
        raise HTTPException(429, detail={"code": "OTP_COOLDOWN", "message": "Подождите перед повторной отправкой кода"}, headers={"Retry-After": str(seconds)})
    if metadata_factory is not None:
        metadata = await metadata_factory()
    elif metadata is None:
        metadata = dict(existing["metadata"]) if existing else {}
    code = f"{randbelow(1_000_000):06d}"
    try:
        await send_verification_code(email=email, code=code, app_name=app_name)
    except EmailDeliveryError as exc:
        raise _error(503, "EMAIL_DELIVERY_UNAVAILABLE", "Не удалось отправить письмо. Попробуйте позже") from exc
    await db.execute(
        text("""INSERT INTO auth_email_otps(email,purpose,app_name,code_digest,attempts,expires_at,resend_after,metadata,updated_at)
        VALUES(:email,:purpose,:app,:digest,0,:expires,:resend,CAST(:metadata AS jsonb),:now)
        ON CONFLICT(email,purpose) DO UPDATE SET app_name=EXCLUDED.app_name,code_digest=EXCLUDED.code_digest,
        attempts=0,expires_at=EXCLUDED.expires_at,resend_after=EXCLUDED.resend_after,metadata=EXCLUDED.metadata,updated_at=EXCLUDED.updated_at"""),
        {"email": email, "purpose": purpose, "app": app_name, "digest": _otp_digest(code), "expires": now + timedelta(seconds=OTP_TTL_SECONDS), "resend": now + timedelta(seconds=RESEND_SECONDS), "metadata": json.dumps(metadata), "now": now},
    )
    await db.commit()
    return {"accepted": True, "resend_after_seconds": RESEND_SECONDS, "expires_in_seconds": OTP_TTL_SECONDS}


async def _verify_code(db: AsyncSession, *, email: str, purpose: str, code: str, app_name: str, user_id: UUID | None = None) -> dict | None:
    await _otp_lock(db, email, purpose)
    row = (await db.execute(text("SELECT code_digest,attempts,expires_at,metadata,app_name FROM auth_email_otps WHERE email=:email AND purpose=:purpose FOR UPDATE"), {"email": email, "purpose": purpose})).mappings().first()
    now = _now()
    if not row or row["expires_at"] <= now:
        await db.execute(text("DELETE FROM auth_email_otps WHERE email=:email AND purpose=:purpose"), {"email": email, "purpose": purpose})
        await db.commit()
        return None
    metadata = dict(row["metadata"] or {})
    valid_scope = row["app_name"] == app_name and (user_id is None or metadata.get("user_id") == str(user_id))
    if not valid_scope or not hmac.compare_digest(str(row["code_digest"]), _otp_digest(code)):
        attempts = int(row["attempts"]) + 1
        if attempts >= MAX_OTP_ATTEMPTS:
            await db.execute(text("DELETE FROM auth_email_otps WHERE email=:email AND purpose=:purpose"), {"email": email, "purpose": purpose})
        else:
            await db.execute(text("UPDATE auth_email_otps SET attempts=:attempts,updated_at=:now WHERE email=:email AND purpose=:purpose"), {"attempts": attempts, "now": now, "email": email, "purpose": purpose})
        await db.commit()
        return None
    await db.execute(text("DELETE FROM auth_email_otps WHERE email=:email AND purpose=:purpose"), {"email": email, "purpose": purpose})
    # The verified mutation and OTP consumption must commit together.
    return metadata


async def _central_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: AsyncSession = Depends(get_db)) -> dict:
    if not credentials:
        raise _error(401, "AUTH_REQUIRED", "Требуется авторизация")
    try:
        claims = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"], issuer="https://auth.lurg.pro", options={"verify_aud": False})
        if claims.get("type") != "access" or "lurg" not in claims.get("aud", []):
            raise ValueError
        user_id = UUID(str(claims["sub"]))
        session_id = UUID(str(claims["sid"])) if claims.get("sid") is not None else None
    except Exception as exc:
        raise _error(401, "AUTH_REQUIRED", "Сессия истекла. Войдите снова") from exc
    await release_expired_blocks(db)
    row = await load_access_identity(db, user_id=user_id, security_version=claims.get("sv"), session_id=session_id)
    if not row:
        raise _error(401, "AUTH_REQUIRED", "Сессия истекла. Войдите снова")
    return row


class InternalSessionClaims(BaseModel):
    """Only signed claims already verified by a trusted ecosystem service."""
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    security_version: int = Field(ge=1, strict=True)
    session_id: UUID | None = None
    token_expires_at: int = Field(gt=0, strict=True)


async def require_internal_service(x_lurg_internal_token: str | None = Header(default=None)) -> None:
    configured = settings.lcp_internal_token
    if not configured or not x_lurg_internal_token or not hmac.compare_digest(
        x_lurg_internal_token.encode("utf-8"), configured.encode("utf-8")
    ):
        raise _error(401, "INTERNAL_AUTH_REQUIRED", "Недействительный сервисный ключ")


@router.post("/internal/session-status", include_in_schema=False, dependencies=[Depends(require_internal_service)])
async def internal_session_status(payload: InternalSessionClaims, db: AsyncSession = Depends(get_db)):
    # No raw access/refresh token crosses this endpoint. Legacy unbound claims
    # must reauthenticate before opening a persistent ecosystem transport.
    if payload.session_id is None or payload.token_expires_at <= _now().timestamp():
        return {"active": False}
    try:
        async with asyncio.timeout(1.5):
            identity = await load_access_identity(db, user_id=payload.user_id,
                security_version=payload.security_version, session_id=payload.session_id)
    except Exception as exc:
        raise _error(503, "INTERNAL_AUTH_UNAVAILABLE", "Проверка сессии временно недоступна") from exc
    # Recheck expiry after the database roundtrip, including a slow query.
    return {"active": identity is not None and payload.token_expires_at > _now().timestamp()}


async def limit_password_attempts(db: AsyncSession, email: str) -> None:
    # Shared by central and cookie login, so alternating endpoints cannot reset
    # the account's budget. Commit before credential checks also counts failures.
    row = (await db.execute(text("""INSERT INTO admin_rate_limits(scope,subject_hash,window_started_at,attempts)
      VALUES('identity-password',:subject,now(),1) ON CONFLICT(scope,subject_hash) DO UPDATE SET
      attempts=CASE WHEN admin_rate_limits.window_started_at<now()-interval '15 minutes' THEN 1 ELSE admin_rate_limits.attempts+1 END,
      window_started_at=CASE WHEN admin_rate_limits.window_started_at<now()-interval '15 minutes' THEN now() ELSE admin_rate_limits.window_started_at END
      RETURNING attempts"""), {"subject": hashlib.sha256(email.encode()).digest()})).mappings().one()
    await db.commit()
    if row["attempts"] > 20:
        raise HTTPException(429, {"code": "LOGIN_RATE_LIMIT", "message": "Слишком много попыток. Повторите позже"}, headers={"Retry-After": "900"})


async def _user_tokens(db: AsyncSession, *, user: dict, app_name: str) -> dict:
    raw_refresh = token_urlsafe(48)
    refresh_row = (await db.execute(text("INSERT INTO auth_refresh_tokens(user_id,token_hash,app_name,expires_at,security_version) VALUES(:user_id,:hash,:app,:expires,:sv) RETURNING id"), {"user_id": user["id"], "hash": digest_token(raw_refresh), "app": app_name, "expires": _now() + timedelta(days=settings.auth_refresh_token_days), "sv": int(user["security_version"])})).mappings().one()
    await db.commit()
    access = make_access_token(user_id=user["id"], email=user["email"], name=user["name"], app_name=app_name, email_verified=bool(user["email_verified"]), security_version=int(user["security_version"]), session_id=refresh_row["id"])
    return {
        "access_token": access, "refresh_token": raw_refresh, "token_type": "bearer",
        "user": {"id": str(user["id"]), "name": user["name"], "avatar_url": None, "email": user["email"], "email_verified": bool(user["email_verified"]), "notification_radius_meters": 5000, "notifications_enabled": True, "locale": "ru", "theme": "system"},
    }


@router.post("/email/register/request-code", status_code=status.HTTP_202_ACCEPTED)
async def register_request_code(payload: EmailRegistrationRequest, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    email = _email(str(payload.email))
    row = (await db.execute(text("SELECT id,email_verified FROM users WHERE lower(email)=:email"), {"email": email})).mappings().first()
    if row and bool(row["email_verified"]):
        raise _error(409, "EMAIL_ALREADY_EXISTS", "Этот email уже зарегистрирован. Войдите в аккаунт")
    # Pending credentials belong to this verification challenge, never to the
    # account until the mailbox owner proves possession of the matching code.
    async def pending_credentials() -> dict:
        return {"name": payload.name.strip(), "password_hash": await hash_password_async(payload.password)}
    return await _issue_code(db=db, email=email, purpose="register", app_name=app_name, metadata_factory=pending_credentials)


@router.post("/email/register/resend-code", status_code=status.HTTP_202_ACCEPTED)
async def register_resend_code(payload: EmailAddress, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    email = _email(str(payload.email))
    row = (await db.execute(text("SELECT email_verified FROM users WHERE lower(email)=:email"), {"email": email})).mappings().first()
    if row and bool(row["email_verified"]):
        raise _error(409, "EMAIL_ALREADY_EXISTS", "Этот email уже зарегистрирован. Войдите в аккаунт")
    pending = await db.scalar(text("SELECT 1 FROM auth_email_otps WHERE email=:email AND purpose='register'"), {"email": email})
    if not pending:
        raise _error(400, "OTP_REQUIRED", "Сначала начните регистрацию")
    return await _issue_code(db=db, email=email, purpose="register", app_name=app_name)


async def _register_verified_user(payload: EmailCode, db: AsyncSession, app_name: str) -> dict:
    email = _email(str(payload.email))
    metadata = await _verify_code(db, email=email, purpose="register", code=payload.code, app_name=app_name)
    if metadata is None or not metadata.get("password_hash") or not metadata.get("name"):
        raise _error(400, "OTP_REQUIRED", "Код неверный или истёк. Запросите новый")
    owner = (await db.execute(text("SELECT id,email_verified FROM users WHERE lower(email)=:email FOR UPDATE"), {"email": email})).mappings().first()
    if owner and owner["email_verified"]:
        raise _error(409, "EMAIL_ALREADY_EXISTS", "Этот email уже зарегистрирован")
    if owner:
        statement = """UPDATE users SET name=:name,password_hash=:password,email_verified=true
          WHERE id=:id RETURNING id,email,name,email_verified,security_version"""
    else:
        statement = """INSERT INTO users(email,name,password_hash,email_verified)
          VALUES(:email,:name,:password,true) RETURNING id,email,name,email_verified,security_version"""
    row = (await db.execute(text(statement), {"email": email, "name": metadata["name"], "password": metadata["password_hash"], "id": owner["id"] if owner else None})).mappings().first()
    if not row:
        raise _error(409, "EMAIL_ALREADY_EXISTS", "Этот email уже зарегистрирован")
    return dict(row)


@router.post("/email/register/verify")
async def register_verify(payload: EmailCode, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    user = await _register_verified_user(payload, db, app_name)
    return await _user_tokens(db, user=user, app_name=app_name)


@router.post("/email/login")
async def email_login(payload: Login, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    email = _email(str(payload.email))
    await limit_password_attempts(db, email)
    await release_expired_blocks(db)
    row = (await db.execute(text("SELECT id,email,name,password_hash,email_verified,security_version,status FROM users WHERE lower(email)=:email"), {"email": email})).mappings().first()
    password_ok = await verify_password_async(payload.password, row["password_hash"] if row else None)
    if not row or row["status"] != "active" or not password_ok:
        raise _error(401, "INVALID_CREDENTIALS", "Неверная почта или пароль")
    if not bool(row["email_verified"]):
        raise _error(403, "EMAIL_NOT_VERIFIED", "Подтвердите email кодом из письма")
    return await _user_tokens(db, user=dict(row), app_name=app_name)


@router.post("/password/reset/request-code", status_code=status.HTTP_202_ACCEPTED)
async def password_reset_request(payload: EmailAddress, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    email = _email(str(payload.email))
    row = (await db.execute(text("SELECT email_verified FROM users WHERE lower(email)=:email"), {"email": email})).mappings().first()
    if row and bool(row["email_verified"]):
        return await _issue_code(db=db, email=email, purpose="reset", app_name=app_name)
    return {"accepted": True, "resend_after_seconds": RESEND_SECONDS, "expires_in_seconds": OTP_TTL_SECONDS}


@router.post("/password/reset/verify")
async def password_reset_verify(payload: EmailCode, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    email = _email(str(payload.email))
    if await _verify_code(db, email=email, purpose="reset", code=payload.code, app_name=app_name) is None:
        raise _error(400, "OTP_REQUIRED", "Код неверный или истёк. Запросите новый")
    now = _now()
    jti = token_urlsafe(32)
    await db.execute(text("DELETE FROM auth_reset_proofs WHERE expires_at<:now"), {"now": now})
    await db.execute(text("""INSERT INTO auth_reset_proofs(token_hash,email,app_name,expires_at)
      VALUES(:hash,:email,:app,:expires)"""), {"hash": digest_token(jti), "email": email, "app": app_name, "expires": now + timedelta(minutes=10)})
    await db.commit()
    proof = jwt.encode({"purpose": "reset", "email": email, "app": app_name, "jti": jti, "iat": now, "exp": now + timedelta(minutes=10)}, settings.jwt_secret, algorithm="HS256")
    return {"reset_token": proof}


@router.post("/password/reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset_confirm(payload: PasswordResetConfirm, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    email = _email(str(payload.email))
    try:
        proof = jwt.decode(payload.reset_token, settings.jwt_secret, algorithms=["HS256"])
        if proof.get("purpose") != "reset" or proof.get("email") != email or proof.get("app") != app_name or not proof.get("jti"):
            raise ValueError
    except (jwt.PyJWTError, ValueError) as exc:
        raise _error(400, "OTP_REQUIRED", "Подтверждение сброса истекло") from exc
    password = await hash_password_async(payload.password)
    row = (await db.execute(text("""UPDATE auth_reset_proofs SET consumed_at=:now
      WHERE token_hash=:hash AND email=:email AND app_name=:app AND consumed_at IS NULL AND expires_at>:now
      RETURNING token_hash"""), {"hash": digest_token(proof["jti"]), "email": email, "app": app_name, "now": _now()})).mappings().first()
    if not row:
        raise _error(400, "OTP_REQUIRED", "Подтверждение сброса истекло")
    await db.execute(text("UPDATE users SET password_hash=:password,security_version=security_version+1 WHERE lower(email)=:email AND email_verified=true"), {"password": password, "email": email})
    await db.execute(text("UPDATE auth_refresh_tokens SET revoked_at=:now WHERE user_id=(SELECT id FROM users WHERE lower(email)=:email) AND revoked_at IS NULL"), {"now": _now(), "email": email})
    await db.execute(text("UPDATE admin_sessions SET revoked_at=:now WHERE user_id=(SELECT id FROM users WHERE lower(email)=:email) AND revoked_at IS NULL"), {"now": _now(), "email": email})
    await db.commit()


@router.post("/refresh")
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    await release_expired_blocks(db)
    row = (await db.execute(text("""SELECT u.id,u.email,u.name,u.email_verified,u.security_version,u.status,t.app_name,t.security_version AS refresh_security_version
        FROM auth_refresh_tokens t JOIN users u ON u.id=t.user_id
        WHERE t.token_hash=:hash AND t.revoked_at IS NULL AND t.expires_at>:now FOR UPDATE OF u,t"""), {"hash": digest_token(payload.refresh_token), "now": _now()})).mappings().first()
    if not row or row["app_name"] != app_name or row["status"] != "active" or not row["email_verified"] or row["refresh_security_version"] != row["security_version"]:
        raise _error(401, "INVALID_REFRESH_TOKEN", "Сессия истекла. Войдите снова")
    await db.execute(text("UPDATE auth_refresh_tokens SET revoked_at=:now WHERE token_hash=:hash"), {"now": _now(), "hash": digest_token(payload.refresh_token)})
    return await _user_tokens(db, user=dict(row), app_name=app_name)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    await db.execute(text("UPDATE auth_refresh_tokens SET revoked_at=:now WHERE token_hash=:hash AND app_name=:app AND revoked_at IS NULL"), {"now": _now(), "hash": digest_token(payload.refresh_token), "app": app_name})
    await db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(payload: RefreshRequest, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name)):
    """Revoke every refresh session for the authenticated account.

    The presented token is checked first, so clients can distinguish a genuine
    server-side completion from a local-only fallback.
    """
    row = (await db.execute(text("""SELECT u.id AS user_id FROM users u JOIN auth_refresh_tokens t ON t.user_id=u.id
        WHERE t.token_hash=:hash AND t.app_name=:app AND t.revoked_at IS NULL AND t.expires_at>:now
        AND t.security_version=u.security_version FOR UPDATE OF u,t"""), {
        "hash": digest_token(payload.refresh_token), "app": app_name, "now": _now(),
    })).mappings().first()
    if not row:
        raise _error(401, "INVALID_REFRESH_TOKEN", "Сессия истекла. Войдите снова")
    await db.execute(text("UPDATE users SET security_version=security_version+1 WHERE id=:id"), {"id": row["user_id"]})
    await db.execute(text("UPDATE auth_refresh_tokens SET revoked_at=:now WHERE user_id=:user_id AND revoked_at IS NULL"), {"now": _now(), "user_id": row["user_id"]})
    await db.execute(text("UPDATE admin_sessions SET revoked_at=:now WHERE user_id=:user_id AND revoked_at IS NULL"), {"now": _now(), "user_id": row["user_id"]})
    await db.commit()


@router.get("/me")
async def me(app_name: str = Depends(_app_name), user: dict = Depends(_central_user)):
    """Return the current central identity for an approved Lurg application."""
    return {"id": str(user["id"]), "email": user["email"], "name": user["name"], "email_verified": bool(user["email_verified"])}


@router.post("/email/change/request-code", status_code=status.HTTP_202_ACCEPTED)
async def change_email_request(payload: EmailAddress, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name), user: dict = Depends(_central_user)):
    email = _email(str(payload.email))
    owner = (await db.execute(text("SELECT id FROM users WHERE lower(email)=:email"), {"email": email})).mappings().first()
    if owner and owner["id"] != user["id"]:
        raise _error(409, "EMAIL_ALREADY_EXISTS", "Этот email уже используется")
    return await _issue_code(db=db, email=email, purpose="change_email", app_name=app_name, metadata={"user_id": str(user["id"])})


@router.post("/email/change/verify")
async def change_email_verify(payload: EmailCode, db: AsyncSession = Depends(get_db), app_name: str = Depends(_app_name), user: dict = Depends(_central_user)):
    email = _email(str(payload.email))
    if await _verify_code(db, email=email, purpose="change_email", code=payload.code, app_name=app_name, user_id=user["id"]) is None:
        raise _error(400, "OTP_REQUIRED", "Код неверный или истёк. Запросите новый")
    owner = (await db.execute(text("SELECT id FROM users WHERE lower(email)=:email"), {"email": email})).mappings().first()
    if owner and owner["id"] != user["id"]:
        raise _error(409, "EMAIL_ALREADY_EXISTS", "Этот email уже используется")
    await db.execute(text("UPDATE users SET email=:email,email_verified=true WHERE id=:id"), {"email": email, "id": user["id"]})
    await db.commit()
    return {"email": email, "email_verified": True}


@router.get("/applications")
async def applications(db: AsyncSession = Depends(get_db)):
    """Public metadata; application registration is intentionally an operator action."""
    rows = (await db.execute(text("SELECT slug,display_name,redirect_uris FROM auth_applications WHERE active=true ORDER BY slug"))).mappings().all()
    return [{"slug": row["slug"], "app_name": row["display_name"], "redirect_uris": row["redirect_uris"]} for row in rows]
