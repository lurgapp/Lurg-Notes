"""graph intelligence and connector foundation

Revision ID: 20260709_0001
Revises:
Create Date: 2026-07-09
"""

from alembic import op

revision = "20260709_0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS provenance jsonb NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS confidence numeric(4,3) NOT NULL DEFAULT 0.500")
    op.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS lifecycle_status varchar(30) NOT NULL DEFAULT 'active'")
    op.execute("ALTER TABLE nodes ADD CONSTRAINT nodes_confidence_range CHECK (confidence >= 0 AND confidence <= 1) NOT VALID")
    op.execute("ALTER TABLE nodes ADD CONSTRAINT nodes_version_positive CHECK (version > 0) NOT VALID")
    op.execute("ALTER TABLE nodes VALIDATE CONSTRAINT nodes_confidence_range")
    op.execute("ALTER TABLE nodes VALIDATE CONSTRAINT nodes_version_positive")

    op.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS provenance jsonb NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS confidence numeric(4,3) NOT NULL DEFAULT 0.500")
    op.execute("ALTER TABLE edges ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE edges ADD CONSTRAINT edges_confidence_range CHECK (confidence >= 0 AND confidence <= 1) NOT VALID")
    op.execute("ALTER TABLE edges ADD CONSTRAINT edges_version_positive CHECK (version > 0) NOT VALID")
    op.execute("ALTER TABLE edges VALIDATE CONSTRAINT edges_confidence_range")
    op.execute("ALTER TABLE edges VALIDATE CONSTRAINT edges_version_positive")

    op.execute("ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS provider varchar(80) NOT NULL DEFAULT 'unknown'")
    op.execute("ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS purpose varchar(80) NOT NULL DEFAULT 'RETRIEVAL_DOCUMENT'")
    op.execute("ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()")

    op.execute("""
      CREATE TABLE IF NOT EXISTS graph_fact_versions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        fact_kind varchar(20) NOT NULL CHECK (fact_kind IN ('node','edge')),
        fact_id uuid NOT NULL,
        version integer NOT NULL CHECK (version > 0),
        confidence numeric(4,3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        lifecycle_status varchar(30) NOT NULL DEFAULT 'active',
        provenance jsonb NOT NULL DEFAULT '{}',
        payload jsonb NOT NULL DEFAULT '{}',
        change_reason varchar(80) NOT NULL DEFAULT 'ingest',
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(fact_kind, fact_id, version)
      )
    """)

    op.execute("""
      CREATE TABLE IF NOT EXISTS external_connections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider varchar(80) NOT NULL,
        provider_account_id text,
        status varchar(30) NOT NULL DEFAULT 'active',
        scopes text[] NOT NULL DEFAULT '{}',
        access_token_encrypted bytea,
        refresh_token_encrypted bytea,
        token_expires_at timestamptz,
        metadata jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        disconnected_at timestamptz
      )
    """)

    op.execute("""
      CREATE TABLE IF NOT EXISTS connector_raw_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        connection_id uuid REFERENCES external_connections(id) ON DELETE SET NULL,
        provider varchar(80) NOT NULL,
        connector varchar(120) NOT NULL,
        external_record_id text,
        idempotency_key text NOT NULL,
        source_type varchar(80) NOT NULL,
        observed_at timestamptz,
        fetched_at timestamptz NOT NULL DEFAULT now(),
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        raw_payload jsonb NOT NULL DEFAULT '{}',
        normalized_payload jsonb NOT NULL DEFAULT '{}',
        processing_status varchar(30) NOT NULL DEFAULT 'pending',
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(user_id, provider, idempotency_key)
      )
    """)

    op.execute("""
      CREATE TABLE IF NOT EXISTS connector_sync_jobs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        connection_id uuid REFERENCES external_connections(id) ON DELETE CASCADE,
        provider varchar(80) NOT NULL,
        status varchar(30) NOT NULL DEFAULT 'pending',
        cursor_value text,
        started_at timestamptz,
        finished_at timestamptz,
        error_message text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
      )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS graph_fact_versions_user_fact_idx ON graph_fact_versions(user_id, fact_kind, fact_id, version DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS external_connections_user_idx ON external_connections(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS connector_raw_events_user_idx ON connector_raw_events(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS connector_raw_events_connection_idx ON connector_raw_events(connection_id)")
    op.execute("CREATE INDEX IF NOT EXISTS connector_sync_jobs_user_idx ON connector_sync_jobs(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS connector_sync_jobs_connection_idx ON connector_sync_jobs(connection_id)")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_sync_jobs")
    op.execute("DROP TABLE IF EXISTS connector_raw_events")
    op.execute("DROP TABLE IF EXISTS external_connections")
    op.execute("DROP TABLE IF EXISTS graph_fact_versions")
    op.execute("DROP INDEX IF EXISTS connector_sync_jobs_connection_idx")
    op.execute("DROP INDEX IF EXISTS connector_sync_jobs_user_idx")
    op.execute("DROP INDEX IF EXISTS connector_raw_events_connection_idx")
    op.execute("DROP INDEX IF EXISTS connector_raw_events_user_idx")
    op.execute("DROP INDEX IF EXISTS external_connections_user_idx")
    op.execute("DROP INDEX IF EXISTS graph_fact_versions_user_fact_idx")
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS purpose")
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS provider")
    op.execute("ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_version_positive")
    op.execute("ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_confidence_range")
    op.execute("ALTER TABLE edges DROP COLUMN IF EXISTS version")
    op.execute("ALTER TABLE edges DROP COLUMN IF EXISTS confidence")
    op.execute("ALTER TABLE edges DROP COLUMN IF EXISTS provenance")
    op.execute("ALTER TABLE nodes DROP CONSTRAINT IF EXISTS nodes_version_positive")
    op.execute("ALTER TABLE nodes DROP CONSTRAINT IF EXISTS nodes_confidence_range")
    op.execute("ALTER TABLE nodes DROP COLUMN IF EXISTS lifecycle_status")
    op.execute("ALTER TABLE nodes DROP COLUMN IF EXISTS version")
    op.execute("ALTER TABLE nodes DROP COLUMN IF EXISTS confidence")
    op.execute("ALTER TABLE nodes DROP COLUMN IF EXISTS provenance")
