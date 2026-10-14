from uuid import UUID
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ...auth import (
    clear_session_cookie, current_user, hash_password_async, make_token,
    set_session_cookie, verify_password_async,
)
from ...db import get_db
from ..admin.accounts import release_expired_blocks
from ...schemas import DeleteAccountRequest, Login, ProfileUpdate, UserCreate, UserPreferences, EmailCode

router = APIRouter(tags=["Identity"])

DEFAULT_PREFERENCES = {"theme": "dark", "language": "ru", "notifications_enabled": True, "auto_use_graph": True, "allow_web_search": True, "haptics_enabled": True}

async def user_status(db: AsyncSession, user_id: UUID) -> dict:
    row = (await db.execute(
        text("""SELECT u.id,u.email,u.name,u.created_at,
          COALESCE(u.onboarding_completed,false) onboarding_completed,
          COALESCE(u.preferences,'{}'::jsonb) preferences
          FROM users u WHERE u.id=:uid"""),
        {"uid": user_id},
    )).mappings().one()
    preferences = {**DEFAULT_PREFERENCES, **(row["preferences"] or {})}
    onboarding_completed = bool(row["onboarding_completed"])
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "display_name": row["name"],
        "created_at": row["created_at"].isoformat(),
        "is_new_user": not onboarding_completed,
        "onboarding_completed": onboarding_completed,
        "graph_initialized": onboarding_completed,
        "node_count": 0,
        "preferences": preferences,
    }

@router.post("/users", status_code=202)
async def create_user(body: UserCreate, response: Response, db: AsyncSession = Depends(get_db)):
    from .central_router import register_request_code
    return await register_request_code(body, db=db, app_name="Graph")


@router.post("/auth/register/verify")
async def verify_registration(body: EmailCode, response: Response, db: AsyncSession = Depends(get_db)):
    from .central_router import _register_verified_user
    row = await _register_verified_user(body, db, "Graph")
    await db.commit()
    token = make_token(row["id"], row["security_version"])
    set_session_cookie(response, token)
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "token": token}

@router.post("/auth/login")
async def login(body: Login, response: Response, db: AsyncSession = Depends(get_db)):
    from .central_router import limit_password_attempts
    await limit_password_attempts(db, str(body.email).strip().lower())
    await release_expired_blocks(db)
    row = (await db.execute(
        text("SELECT id,email,name,password_hash,email_verified,security_version,status FROM users WHERE lower(email)=lower(:email)"),
        {"email": body.email},
    )).mappings().first()
    password_ok = await verify_password_async(body.password, row["password_hash"] if row else None)
    if not row or row["status"] != "active" or not password_ok:
        raise HTTPException(401, "Неверная почта или пароль")
    if not row["email_verified"]:
        raise HTTPException(403, "Подтвердите email кодом из письма")
    token = make_token(row["id"], row["security_version"])
    set_session_cookie(response, token)
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "token": token}

@router.get("/auth/me")
async def me(user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    status = await user_status(db, user_id)
    return {
        "id": status["id"],
        "email": status["email"],
        "name": status["name"],
        "display_name": status["display_name"],
        "is_new_user": status["is_new_user"],
        "onboarding_completed": status["onboarding_completed"],
        "graph_initialized": status["graph_initialized"],
        "preferences": status["preferences"],
    }

@router.patch("/profile")
async def update_profile(body: ProfileUpdate, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        text("UPDATE users SET name=:name WHERE id=:uid RETURNING id,email,name"),
        {"uid": user_id, "name": body.name.strip()},
    )).mappings().one()
    await db.commit()
    return {**dict(row), "id": str(row["id"])}

@router.patch("/settings/preferences")
async def update_preferences(body: UserPreferences, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    preferences = body.model_dump()
    await db.execute(
        text("UPDATE users SET preferences=CAST(:preferences AS jsonb) WHERE id=:uid"),
        {"uid": user_id, "preferences": json.dumps(preferences, ensure_ascii=False)},
    )
    await db.commit()
    return preferences

@router.delete("/account", status_code=204)
async def delete_account(body: DeleteAccountRequest, response: Response, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)):
    password_hash = await db.scalar(text("SELECT password_hash FROM users WHERE id=:uid"), {"uid": user_id})
    if not password_hash or not await verify_password_async(body.password, password_hash):
        raise HTTPException(401, "Повторная авторизация не пройдена")
    is_owner = await db.scalar(text("""SELECT EXISTS(SELECT 1 FROM admin_user_roles ur
      JOIN admin_roles r ON r.id=ur.role_id WHERE ur.user_id=:uid AND r.slug='owner' AND ur.revoked_at IS NULL)"""), {"uid": user_id})
    if is_owner:
        raise HTTPException(403, "Аккаунт Owner нельзя удалить")
    await db.execute(text("DELETE FROM users WHERE id=:uid"), {"uid": user_id})
    await db.commit()
    clear_session_cookie(response)

@router.post("/auth/logout", status_code=204)
async def logout(response: Response):
    clear_session_cookie(response)
