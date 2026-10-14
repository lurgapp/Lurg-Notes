"""Lurg Notes foundation

Revision ID: 20260714_0009
Revises: 20260713_0008
Create Date: 2026-07-14
"""

from alembic import op


revision = "20260714_0009"
down_revision = "20260713_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          workspace_id uuid,
          title text NOT NULL DEFAULT '',
          document_schema_version integer NOT NULL DEFAULT 1,
          revision integer NOT NULL DEFAULT 1,
          sync_status varchar(20) NOT NULL DEFAULT 'synced',
          metadata jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          deleted_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS note_blocks (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          note_id uuid NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
          parent_id uuid,
          position numeric(20,10) NOT NULL,
          type varchar(40) NOT NULL,
          content jsonb NOT NULL DEFAULT '{}',
          attrs jsonb NOT NULL DEFAULT '{}',
          origin varchar(40) NOT NULL DEFAULT 'user',
          revision integer NOT NULL DEFAULT 1,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          deleted_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS note_operations (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          note_id uuid NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
          client_operation_id text NOT NULL,
          base_revision integer NOT NULL,
          resulting_revision integer,
          operation_type varchar(40) NOT NULL,
          payload jsonb NOT NULL DEFAULT '{}',
          status varchar(20) NOT NULL DEFAULT 'applied',
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(user_id, note_id, client_operation_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS notes_user_recent_idx ON notes(user_id, updated_at DESC, created_at DESC) WHERE deleted_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS note_blocks_note_position_idx ON note_blocks(note_id, position) WHERE deleted_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS note_blocks_user_note_idx ON note_blocks(user_id, note_id)")
    op.execute("CREATE INDEX IF NOT EXISTS note_operations_note_created_idx ON note_operations(note_id, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS note_operations_note_created_idx")
    op.execute("DROP INDEX IF EXISTS note_blocks_user_note_idx")
    op.execute("DROP INDEX IF EXISTS note_blocks_note_position_idx")
    op.execute("DROP INDEX IF EXISTS notes_user_recent_idx")
    op.execute("DROP TABLE IF EXISTS note_operations")
    op.execute("DROP TABLE IF EXISTS note_blocks")
    op.execute("DROP TABLE IF EXISTS notes")
