"""app device push tokens

Revision ID: 20260713_0008
Revises: 20260711_0007
Create Date: 2026-07-13
"""

from alembic import op


revision = "20260713_0008"
down_revision = "20260711_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_device_tokens (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          device_id text NOT NULL,
          platform varchar(30) NOT NULL,
          push_provider varchar(40) NOT NULL,
          push_token text NOT NULL,
          app_version_name varchar(40),
          app_version_code integer,
          build_identifier text,
          status varchar(20) NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          deactivated_at timestamptz,
          UNIQUE(user_id, device_id, push_provider)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS app_device_tokens_user_status_idx ON app_device_tokens(user_id,status,updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app_device_tokens_user_status_idx")
    op.execute("DROP TABLE IF EXISTS app_device_tokens")
