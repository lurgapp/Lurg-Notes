"""Allow Graph candidates produced by Lurg Notes.

Notes candidates deliberately reuse graph_change_candidates and the Graph commit
layer.  The nullable chat references distinguish product provenance without
creating fake chat history for a note.
"""

from alembic import op


revision = "20260714_0010"
down_revision = "20260714_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE graph_change_candidates ALTER COLUMN thread_id DROP NOT NULL")
    op.execute("ALTER TABLE graph_change_candidates ALTER COLUMN message_id DROP NOT NULL")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS source_type varchar(40) NOT NULL DEFAULT 'chat'")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS source_id text")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS source_metadata jsonb NOT NULL DEFAULT '{}'")
    op.execute("CREATE INDEX IF NOT EXISTS graph_change_candidates_source_idx ON graph_change_candidates(user_id, source_type, source_id, status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS graph_change_candidates_source_idx")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS source_metadata")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS source_id")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS source_type")
