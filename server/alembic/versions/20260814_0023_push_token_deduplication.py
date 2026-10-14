"""Guarantee one active FCM target per Lurg app installation.

Revision ID: 20260814_0023
Revises: 20260814_0022
"""
from alembic import op


revision = "20260814_0023"
down_revision = "20260814_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the newest registration and preserve older rows as inactive audit
    # history.  Never delete a token row while campaign deliveries may refer to
    # it.
    op.execute("""
        WITH duplicates AS (
          SELECT id,
                 row_number() OVER (
                   PARTITION BY app_id, push_provider, push_token
                   ORDER BY updated_at DESC, id DESC
                 ) AS position
          FROM app_device_tokens
          WHERE status = 'active'
        )
        UPDATE app_device_tokens token
        SET status = 'inactive', updated_at = now(), deactivated_at = now()
        FROM duplicates
        WHERE token.id = duplicates.id AND duplicates.position > 1
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS app_device_tokens_active_token_key
        ON app_device_tokens(app_id, push_provider, push_token)
        WHERE status = 'active'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app_device_tokens_active_token_key")
