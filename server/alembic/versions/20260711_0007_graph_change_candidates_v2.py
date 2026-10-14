"""Structured graph change candidates"""
from alembic import op

revision = "20260711_0007"
down_revision = "20260711_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS operation varchar(20)")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS proposed_nodes jsonb NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS proposed_edges jsonb NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS explanation text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS expires_at timestamptz")


def downgrade():
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS expires_at")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS explanation")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS proposed_edges")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS proposed_nodes")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS operation")
