"""Account-access helpers shared by Lurg ID and the administrative API."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def release_expired_blocks(db: AsyncSession) -> int:
    """Restore accounts whose explicit administrative block period has ended.

    The condition includes the restriction row, so a manually unblocked account
    can never be changed by this cleanup path.
    """
    result = await db.execute(text("""
        UPDATE users AS u
        SET status='active', security_version=security_version+1
        FROM admin_account_restrictions AS r
        WHERE r.user_id=u.id
          AND r.status='blocked'
          AND r.expires_at IS NOT NULL
          AND r.expires_at <= now()
          AND u.status='blocked'
        RETURNING r.user_id
    """))
    user_ids = [row[0] for row in result.all()]
    if not user_ids:
        return 0
    await db.execute(text("""
        UPDATE admin_account_restrictions
        SET status='expired', released_at=now(), release_reason='Срок блокировки истёк'
        WHERE user_id = ANY(CAST(:user_ids AS uuid[])) AND status='blocked'
    """), {"user_ids": user_ids})
    await db.execute(text("""
        UPDATE admin_sessions SET revoked_at=now()
        WHERE user_id = ANY(CAST(:user_ids AS uuid[])) AND revoked_at IS NULL
    """), {"user_ids": user_ids})
    await db.execute(text("""
        UPDATE auth_refresh_tokens SET revoked_at=now()
        WHERE user_id = ANY(CAST(:user_ids AS uuid[])) AND revoked_at IS NULL
    """), {"user_ids": user_ids})
    await db.commit()
    return len(user_ids)
