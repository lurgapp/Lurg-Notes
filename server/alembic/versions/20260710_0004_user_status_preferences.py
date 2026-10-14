"""user status and preferences

Revision ID: 20260710_0004
Revises: 20260710_0003
Create Date: 2026-07-10
"""

from alembic import op

revision = "20260710_0004"
down_revision = "20260710_0003"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed boolean NOT NULL DEFAULT false")
    op.execute("""ALTER TABLE users ADD COLUMN IF NOT EXISTS preferences jsonb NOT NULL
      DEFAULT jsonb_build_object('theme','dark','language','ru','notifications_enabled',true)""")

def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS preferences")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS onboarding_completed")
