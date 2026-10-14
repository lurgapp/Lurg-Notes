"""AI runtime v0.2 summaries and confirmed graph changes"""
from alembic import op

revision = "20260711_0005"
down_revision = "20260710_0004"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS conversation_summary text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS summarized_message_count integer NOT NULL DEFAULT 0")
    op.execute("""CREATE TABLE IF NOT EXISTS graph_change_candidates (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      thread_id uuid NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE, message_id uuid NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
      action varchar(20) NOT NULL, fact_text text NOT NULL, confidence numeric(4,3) NOT NULL DEFAULT .95,
      status varchar(20) NOT NULL DEFAULT 'pending', created_at timestamptz NOT NULL DEFAULT now(), confirmed_at timestamptz
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS graph_change_candidates_owner_idx ON graph_change_candidates(user_id,status,created_at DESC)")
    op.execute("""CREATE TABLE IF NOT EXISTS ai_usage_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      thread_id uuid REFERENCES chat_threads(id) ON DELETE SET NULL,
      message_id uuid REFERENCES chat_messages(id) ON DELETE SET NULL,
      provider varchar(80) NOT NULL,
      model varchar(160) NOT NULL,
      mode varchar(30) NOT NULL,
      input_tokens integer,
      output_tokens integer,
      reasoning_tokens integer NOT NULL DEFAULT 0,
      latency_ms integer,
      first_token_latency_ms integer,
      cache_hit boolean NOT NULL DEFAULT false,
      cost numeric(12,8),
      cost_estimated boolean NOT NULL DEFAULT true,
      metadata jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ai_usage_events_user_created_idx ON ai_usage_events(user_id,created_at DESC)")

def downgrade():
    op.execute("DROP TABLE IF EXISTS ai_usage_events")
    op.execute("DROP TABLE IF EXISTS graph_change_candidates")
    op.execute("ALTER TABLE chat_threads DROP COLUMN IF EXISTS summarized_message_count")
    op.execute("ALTER TABLE chat_threads DROP COLUMN IF EXISTS conversation_summary")
