from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID
import secrets
import asyncio
from starlette.concurrency import run_in_threadpool
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from pwdlib import PasswordHash
from .config import settings
from .db import get_db
from .modules.admin.accounts import release_expired_blocks

password_hash = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hash.hash(secrets.token_urlsafe(32))
bearer = HTTPBearer(auto_error=False)
COOKIE_NAME = "lurg_session"

def hash_password(value: str) -> str:
    return password_hash.hash(value)

def verify_password(value: str, hashed: str) -> bool:
    return password_hash.verify(value, hashed)

def verify_password_or_dummy(value: str, hashed: str | None) -> bool:
    """Keep login timing independent of whether an account exists."""
    try:
        return password_hash.verify(value, hashed or DUMMY_PASSWORD_HASH)
    except ValueError:
        return False

# Argon2 is deliberately expensive. Bound its memory use and keep it off the
# event loop so login bursts do not stall chat and sync requests.
_password_slots = asyncio.Semaphore(2)

async def hash_password_async(value: str) -> str:
    async with _password_slots:
        return await run_in_threadpool(hash_password, value)

async def verify_password_async(value: str, hashed: str | None) -> bool:
    async with _password_slots:
        return await run_in_threadpool(verify_password_or_dummy, value, hashed)

def make_token(user_id: UUID, security_version: int = 1) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "sv": security_version, "iat": now, "exp": now + timedelta(days=7)}, settings.jwt_secret, algorithm="HS256")


def make_access_token(*, user_id: UUID, email: str, name: str, app_name: str, email_verified: bool = False, security_version: int = 1, session_id: UUID | None = None) -> str:
    """Issue a short-lived ecosystem access token for a registered service."""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id), "email": email, "name": name, "email_verified": email_verified, "sv": security_version,
            "type": "access", "iss": "https://auth.lurg.pro", "aud": ["lurg", app_name.lower()],
            **({"sid": str(session_id)} if session_id is not None else {}),
            "iat": now, "exp": now + timedelta(minutes=settings.auth_access_token_minutes),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def digest_token(value: str) -> str:
    return sha256(value.encode()).hexdigest()

async def load_access_identity(db: AsyncSession, *, user_id: UUID, security_version: int, session_id: UUID | None = None) -> dict | None:
    """Check current account and refresh-session authority for validated claims.

    JWT signature/issuer/audience/expiry are checked by the caller. A refresh
    rotation or revocation invalidates its bound access identity immediately.
    """
    row = (await db.execute(text("""SELECT id,email,name,email_verified,security_version,status FROM users WHERE id=:id
      AND (CAST(:sid AS uuid) IS NULL OR EXISTS(SELECT 1 FROM auth_refresh_tokens t WHERE t.id=CAST(:sid AS uuid)
        AND t.user_id=users.id AND t.revoked_at IS NULL AND t.expires_at>now()
        AND t.security_version=users.security_version))"""), {"id": user_id, "sid": session_id})).mappings().first()
    if not row or row["status"] != "active" or not row["email_verified"] or security_version != row["security_version"]:
        return None
    return dict(row)

async def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: AsyncSession = Depends(get_db)) -> UUID:
    try:
        await release_expired_blocks(db)
        token = credentials.credentials if credentials else request.cookies.get(COOKIE_NAME)
        if not token:
            raise ValueError
        # Central Lurg ID access tokens carry an audience.  Product APIs are a
        # trusted ecosystem audience, so PyJWT must not reject their normal
        # audience validation before we validate the issuer/type ourselves.
        payload=jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"verify_aud": False})
        if payload.get("type") == "access":
            audience = payload.get("aud", [])
            if payload.get("iss") != "https://auth.lurg.pro" or "lurg" not in audience:
                raise ValueError
        elif payload.get("type") is not None:
            raise ValueError
        user_id=UUID(payload["sub"])
        session_id = UUID(str(payload["sid"])) if payload.get("sid") is not None else None
        row = await load_access_identity(db, user_id=user_id, security_version=int(payload.get("sv", 1)), session_id=session_id)
        if not row: raise ValueError
        return user_id
    except Exception:
        raise HTTPException(status_code=401, detail="Сессия недействительна")

def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=7 * 24 * 60 * 60, httponly=True,
        secure=settings.cookie_secure, samesite="lax", domain=settings.cookie_domain,
        path="/",
    )

def clear_session_cookie(response) -> None:
    response.delete_cookie(
        COOKIE_NAME, domain=settings.cookie_domain, path="/",
        secure=settings.cookie_secure, httponly=True, samesite="lax",
    )
