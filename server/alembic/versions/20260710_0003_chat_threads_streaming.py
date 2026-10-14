"""chat threads and streaming history

Revision ID: 20260710_0003
Revises: 20260709_0002
Create Date: 2026-07-10
"""

from alembic import op

revision = "20260710_0003"
down_revision = "20260709_0002"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
      CREATE TABLE IF NOT EXISTS chat_threads (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title text NOT NULL DEFAULT 'Новый чат',
        pinned boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        last_message_at timestamptz,
        deleted_at timestamptz
      )
    """)
    op.execute("""
      CREATE TABLE IF NOT EXISTS chat_messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        thread_id uuid NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
        role varchar(20) NOT NULL CHECK (role IN ('user','assistant','system')),
        content text NOT NULL,
        sources text[] NOT NULL DEFAULT '{}',
        metadata jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS chat_threads_user_recent_idx ON chat_threads(user_id, pinned DESC, last_message_at DESC, created_at DESC) WHERE deleted_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS chat_messages_thread_idx ON chat_messages(thread_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS chat_messages_user_idx ON chat_messages(user_id, created_at DESC)")

def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chat_messages_user_idx")
    op.execute("DROP INDEX IF EXISTS chat_messages_thread_idx")
    op.execute("DROP INDEX IF EXISTS chat_threads_user_recent_idx")
    op.execute("DROP TABLE IF EXISTS chat_messages")
    op.execute("DROP TABLE IF EXISTS chat_threads")
