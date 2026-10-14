"""self-service recovery-code rotation

Revision ID: 20260715_0018
Revises: 20260715_0017
"""
from alembic import op

revision = "20260715_0018"
down_revision = "20260715_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE admin_enrollments ADD COLUMN recovery_set_id uuid NOT NULL DEFAULT gen_random_uuid()")
    op.execute("ALTER TABLE admin_enrollments ADD COLUMN recovery_generated_at timestamptz NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE admin_enrollments ADD COLUMN recovery_last_used_at timestamptz")
    op.execute("ALTER TABLE admin_reauth_grants ADD COLUMN session_id uuid REFERENCES admin_sessions(id) ON DELETE CASCADE")
    op.execute("CREATE INDEX admin_reauth_session_active_idx ON admin_reauth_grants(session_id,expires_at) WHERE consumed_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS admin_reauth_session_active_idx")
    op.execute("ALTER TABLE admin_reauth_grants DROP COLUMN IF EXISTS session_id")
    op.execute("ALTER TABLE admin_enrollments DROP COLUMN IF EXISTS recovery_last_used_at")
    op.execute("ALTER TABLE admin_enrollments DROP COLUMN IF EXISTS recovery_generated_at")
    op.execute("ALTER TABLE admin_enrollments DROP COLUMN IF EXISTS recovery_set_id")
