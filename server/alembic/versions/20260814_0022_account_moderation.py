"""Account moderation lifecycle with auditable temporary blocks.

Revision ID: 20260814_0022
Revises: 20260814_0021
"""
from alembic import op

revision = "20260814_0022"
down_revision = "20260814_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS admin_account_restrictions (
          user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          status varchar(20) NOT NULL CHECK (status IN ('blocked','unblocked','expired')),
          reason text NOT NULL,
          blocked_by uuid REFERENCES users(id) ON DELETE SET NULL,
          blocked_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz,
          released_by uuid REFERENCES users(id) ON DELETE SET NULL,
          released_at timestamptz,
          release_reason text,
          CHECK (expires_at IS NULL OR expires_at > blocked_at)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS admin_account_restrictions_active_expiry_idx
        ON admin_account_restrictions(status,expires_at) WHERE status='blocked'
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_account_restrictions")
