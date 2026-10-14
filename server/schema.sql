CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL UNIQUE,
  name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
  password_hash text NOT NULL,
  onboarding_completed boolean NOT NULL DEFAULT false,
  preferences jsonb NOT NULL DEFAULT jsonb_build_object('theme','dark','language','ru','notifications_enabled',true),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type varchar(40) NOT NULL,
  label text NOT NULL,
  properties jsonb NOT NULL DEFAULT '{}',
  source_text text,
  provenance jsonb NOT NULL DEFAULT '{}',
  confidence numeric(4,3) NOT NULL DEFAULT 0.500 CHECK (confidence >= 0 AND confidence <= 1),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  lifecycle_status varchar(30) NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, type, label)
);
CREATE TABLE IF NOT EXISTS edges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source_id uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  target_id uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  relation varchar(80) NOT NULL,
  properties jsonb NOT NULL DEFAULT '{}',
  provenance jsonb NOT NULL DEFAULT '{}',
  confidence numeric(4,3) NOT NULL DEFAULT 0.500 CHECK (confidence >= 0 AND confidence <= 1),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, source_id, target_id, relation),
  CHECK (source_id <> target_id)
);
CREATE TABLE IF NOT EXISTS embeddings (
  node_id uuid PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  embedding vector(3072) NOT NULL,
  model varchar(80) NOT NULL,
  provider varchar(80) NOT NULL DEFAULT 'unknown',
  purpose varchar(80) NOT NULL DEFAULT 'RETRIEVAL_DOCUMENT',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
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
);
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
);
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
);
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
);
CREATE TABLE IF NOT EXISTS chat_threads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL DEFAULT 'Новый чат',
  pinned boolean NOT NULL DEFAULT false,
  conversation_summary text NOT NULL DEFAULT '',
  summarized_message_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_message_at timestamptz,
  deleted_at timestamptz
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  thread_id uuid NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  role varchar(20) NOT NULL CHECK (role IN ('user','assistant','system')),
  content text NOT NULL,
  sources text[] NOT NULL DEFAULT '{}',
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS permissions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  grantee text NOT NULL,
  resource_type varchar(30) NOT NULL,
  resource_id uuid,
  scopes text[] NOT NULL DEFAULT '{}',
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, grantee, resource_type, resource_id)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  action varchar(80) NOT NULL,
  resource_type varchar(40) NOT NULL,
  resource_id uuid,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outbox_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  aggregate_type varchar(40) NOT NULL,
  aggregate_id uuid NOT NULL,
  event_type varchar(120) NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
CREATE TABLE IF NOT EXISTS graph_change_candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  thread_id uuid NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  message_id uuid NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
  action varchar(20) NOT NULL,
  operation varchar(20),
  fact_text text NOT NULL,
  proposed_nodes jsonb NOT NULL DEFAULT '[]',
  proposed_edges jsonb NOT NULL DEFAULT '[]',
  explanation text NOT NULL DEFAULT '',
  confidence numeric(4,3) NOT NULL DEFAULT .95,
  status varchar(20) NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now(),
  confirmed_at timestamptz,
  expires_at timestamptz
);
CREATE TABLE IF NOT EXISTS ai_usage_events (
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
);
CREATE TABLE IF NOT EXISTS app_device_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_id text NOT NULL,
  app_id varchar(30) NOT NULL DEFAULT 'notes',
  platform varchar(30) NOT NULL,
  push_provider varchar(40) NOT NULL,
  push_token text NOT NULL,
  app_version_name varchar(40),
  app_version_code integer,
  build_identifier text,
  status varchar(20) NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deactivated_at timestamptz,
  UNIQUE(user_id, device_id, push_provider, app_id)
);
CREATE INDEX IF NOT EXISTS nodes_user_idx ON nodes(user_id);
CREATE INDEX IF NOT EXISTS edges_user_idx ON edges(user_id);
CREATE INDEX IF NOT EXISTS embeddings_user_idx ON embeddings(user_id);
CREATE INDEX IF NOT EXISTS graph_fact_versions_user_fact_idx ON graph_fact_versions(user_id, fact_kind, fact_id, version DESC);
CREATE INDEX IF NOT EXISTS external_connections_user_idx ON external_connections(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS external_connections_user_provider_uidx ON external_connections(user_id, provider);
CREATE INDEX IF NOT EXISTS connector_raw_events_user_idx ON connector_raw_events(user_id);
CREATE INDEX IF NOT EXISTS connector_raw_events_connection_idx ON connector_raw_events(connection_id);
CREATE INDEX IF NOT EXISTS connector_raw_events_provider_external_idx ON connector_raw_events(provider, external_record_id);
CREATE INDEX IF NOT EXISTS connector_sync_jobs_user_idx ON connector_sync_jobs(user_id);
CREATE INDEX IF NOT EXISTS connector_sync_jobs_connection_idx ON connector_sync_jobs(connection_id);
CREATE INDEX IF NOT EXISTS chat_threads_user_recent_idx ON chat_threads(user_id, pinned DESC, last_message_at DESC, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS chat_messages_thread_idx ON chat_messages(thread_id, created_at);
CREATE INDEX IF NOT EXISTS chat_messages_user_idx ON chat_messages(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS graph_change_candidates_owner_idx ON graph_change_candidates(user_id,status,created_at DESC);
CREATE INDEX IF NOT EXISTS ai_usage_events_user_created_idx ON ai_usage_events(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS app_device_tokens_user_status_idx ON app_device_tokens(user_id,status,updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS app_device_tokens_active_token_key
  ON app_device_tokens(app_id, push_provider, push_token) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS audit_user_created_idx ON audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS outbox_unpublished_idx ON outbox_events(created_at) WHERE published_at IS NULL;
CREATE INDEX IF NOT EXISTS embeddings_halfvec_idx ON embeddings USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);
