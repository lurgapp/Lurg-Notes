"""one-time bootstrap enrollment codes for the existing Owner

Revision ID: 20260715_0017
Revises: 20260715_0016
"""
from alembic import op

revision = "20260715_0017"
down_revision = "20260715_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE admin_owner_bootstrap_codes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        code_hash bytea NOT NULL UNIQUE,
        expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        used_at timestamptz,
        revoked_at timestamptz,
        CHECK (expires_at > created_at)
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX admin_owner_bootstrap_one_active_idx
      ON admin_owner_bootstrap_codes(user_id)
      WHERE used_at IS NULL AND revoked_at IS NULL
    """)
    op.execute("""
      ALTER TABLE admin_login_challenges
      ADD COLUMN bootstrap_code_id uuid REFERENCES admin_owner_bootstrap_codes(id) ON DELETE RESTRICT
    """)
    op.execute("""
      CREATE INDEX admin_login_challenges_bootstrap_idx
      ON admin_login_challenges(bootstrap_code_id)
      WHERE consumed_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS admin_login_challenges_bootstrap_idx")
    op.execute("ALTER TABLE admin_login_challenges DROP COLUMN IF EXISTS bootstrap_code_id")
    op.execute("DROP TABLE IF EXISTS admin_owner_bootstrap_codes")
