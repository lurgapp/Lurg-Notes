"""operations center telemetry and read-only controls

Revision ID: 20260715_0015
Revises: 20260715_0014
"""
from alembic import op
revision="20260715_0015"; down_revision="20260715_0014"; branch_labels=None; depends_on=None
def upgrade() -> None:
    op.execute("""CREATE TABLE operations_service_snapshots (service varchar(40) PRIMARY KEY, status varchar(20) NOT NULL CHECK(status IN ('healthy','warning','critical','offline')), version varchar(80), uptime_seconds bigint, response_ms integer, cpu_percent numeric(6,2), memory_bytes bigint, error_count integer NOT NULL DEFAULT 0, checked_at timestamptz NOT NULL, payload jsonb NOT NULL DEFAULT '{}')""")
    op.execute("""CREATE TABLE operations_events (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, occurred_at timestamptz NOT NULL DEFAULT now(), service varchar(40) NOT NULL, level varchar(10) NOT NULL CHECK(level IN ('error','warning','info','debug')), message text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}')""")
    op.execute("CREATE INDEX operations_events_feed_idx ON operations_events(service,level,occurred_at DESC)")
    op.execute("""CREATE TABLE operations_incidents (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), service varchar(40) NOT NULL, severity varchar(20) NOT NULL CHECK(severity IN ('warning','critical')), status varchar(20) NOT NULL CHECK(status IN ('open','resolved')), description text NOT NULL, detected_at timestamptz NOT NULL, resolved_at timestamptz, metadata jsonb NOT NULL DEFAULT '{}')""")
    op.execute("CREATE INDEX operations_incidents_recent_idx ON operations_incidents(status,detected_at DESC)")
    op.execute("CREATE TABLE operations_controls (key varchar(50) PRIMARY KEY, enabled boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now(), updated_by uuid REFERENCES users(id) ON DELETE SET NULL)")
    op.execute("INSERT INTO operations_controls(key) VALUES ('maintenance_mode'),('push_paused') ON CONFLICT DO NOTHING")
    op.execute("INSERT INTO admin_permissions(slug,description) VALUES ('operations.read','Read operations summary'),('operations.deep.read','Read infrastructure telemetry'),('operations.control','Control approved operations safeguards') ON CONFLICT(slug) DO NOTHING")
    op.execute("""INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r JOIN admin_permissions p ON p.slug='operations.read' WHERE r.slug IN ('owner','admin','developer','support') ON CONFLICT DO NOTHING""")
    op.execute("""INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r JOIN admin_permissions p ON p.slug='operations.deep.read' WHERE r.slug IN ('owner','admin','developer') ON CONFLICT DO NOTHING""")
    op.execute("""INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r JOIN admin_permissions p ON p.slug='operations.control' WHERE r.slug='owner' ON CONFLICT DO NOTHING""")
def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operations_controls, operations_incidents, operations_events, operations_service_snapshots")
