"""scope device push tokens to a Lurg application

Revision ID: 20260814_0021
Revises: 20260809_0020
"""
from alembic import op


revision = "20260814_0021"
down_revision = "20260809_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app_device_tokens ADD COLUMN IF NOT EXISTS app_id varchar(30) NOT NULL DEFAULT 'notes'")
    op.execute("ALTER TABLE app_device_tokens DROP CONSTRAINT IF EXISTS app_device_tokens_user_id_device_id_push_provider_key")
    op.execute("ALTER TABLE app_device_tokens ADD CONSTRAINT app_device_tokens_user_device_provider_app_key UNIQUE(user_id, device_id, push_provider, app_id)")
    op.execute("CREATE INDEX IF NOT EXISTS app_device_tokens_app_status_idx ON app_device_tokens(app_id, status, updated_at DESC)")


def downgrade() -> None:
    op.execute("DELETE FROM app_device_tokens WHERE app_id <> 'notes'")
    op.execute("ALTER TABLE app_device_tokens DROP CONSTRAINT IF EXISTS app_device_tokens_user_device_provider_app_key")
    op.execute("ALTER TABLE app_device_tokens ADD CONSTRAINT app_device_tokens_user_id_device_id_push_provider_key UNIQUE(user_id, device_id, push_provider)")
    op.execute("ALTER TABLE app_device_tokens DROP COLUMN IF EXISTS app_id")
    op.execute("DROP INDEX IF EXISTS app_device_tokens_app_status_idx")
