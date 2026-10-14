"""Persist AI usage telemetry"""
from alembic import op

revision = "20260711_0006"
down_revision = "20260711_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS operation varchar(20)")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS proposed_nodes jsonb NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS proposed_edges jsonb NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS explanation text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE graph_change_candidates ADD COLUMN IF NOT EXISTS expires_at timestamptz")
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
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS expires_at")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS explanation")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS proposed_edges")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS proposed_nodes")
    op.execute("ALTER TABLE graph_change_candidates DROP COLUMN IF EXISTS operation")
