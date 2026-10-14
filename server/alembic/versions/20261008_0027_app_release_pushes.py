"""Durable app-scoped release notifications and retryable device jobs."""
from alembic import op

revision = "20261008_0027"
down_revision = "20261003_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE app_releases (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      app_id varchar(16) NOT NULL CHECK(app_id IN ('notes','lurg')),
      version_code integer NOT NULL CHECK(version_code > 0), version_name varchar(40) NOT NULL,
      sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
      package_name varchar(80) NOT NULL, apk_url varchar(300) NOT NULL, manifest_url varchar(300) NOT NULL,
      published_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(app_id,version_code), UNIQUE(app_id,sha256))""")
    op.execute("""CREATE TABLE app_release_deliveries (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), release_id uuid NOT NULL REFERENCES app_releases(id) ON DELETE CASCADE,
      device_token_id uuid NOT NULL REFERENCES app_device_tokens(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      token_hash bytea NOT NULL, status varchar(16) NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','sending','sent','failed','skipped')),
      attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
      lease_token uuid, lease_until timestamptz, error_code varchar(40), delivered_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(release_id,token_hash))""")
    op.execute("CREATE INDEX app_release_delivery_ready_idx ON app_release_deliveries(next_attempt_at,created_at) WHERE status IN ('pending','sending')")


def downgrade() -> None:
    op.execute("DROP TABLE app_release_deliveries")
    op.execute("DROP TABLE app_releases")
