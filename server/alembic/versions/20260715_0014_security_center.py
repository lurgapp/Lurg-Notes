"""security center, reauthentication and safe session metadata

Revision ID: 20260715_0014
Revises: 20260715_0013
"""
from alembic import op

revision="20260715_0014"
down_revision="20260715_0013"
branch_labels=None
depends_on=None

def upgrade() -> None:
    op.execute("ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS user_agent text, ADD COLUMN IF NOT EXISTS device_name varchar(160), ADD COLUMN IF NOT EXISTS browser varchar(80), ADD COLUMN IF NOT EXISTS operating_system varchar(80), ADD COLUMN IF NOT EXISTS country varchar(80), ADD COLUMN IF NOT EXISTS city varchar(120)")
    op.execute("ALTER TABLE admin_login_challenges ADD COLUMN IF NOT EXISTS reauth_action varchar(80)")
    op.execute("CREATE TABLE IF NOT EXISTS admin_trusted_devices (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, user_agent_hash bytea NOT NULL, label varchar(120), trusted_at timestamptz NOT NULL DEFAULT now(), trusted_by uuid REFERENCES users(id) ON DELETE SET NULL, UNIQUE(user_id,user_agent_hash))")
    op.execute("CREATE TABLE IF NOT EXISTS admin_reauth_grants (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, token_hash bytea NOT NULL UNIQUE, action varchar(80) NOT NULL, user_agent_hash bytea NOT NULL, ip inet, expires_at timestamptz NOT NULL, consumed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now())")
    op.execute("CREATE INDEX IF NOT EXISTS admin_reauth_active_idx ON admin_reauth_grants(token_hash,expires_at) WHERE consumed_at IS NULL")
    op.execute("INSERT INTO admin_permissions(slug,description) VALUES ('security.read','Read scoped security metadata'),('sessions.terminate','Terminate administrative sessions') ON CONFLICT(slug) DO NOTHING")
    op.execute("""INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r JOIN admin_permissions p ON p.slug='security.read' WHERE r.slug IN ('owner','admin','developer','support') ON CONFLICT DO NOTHING""")
    op.execute("""INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r JOIN admin_permissions p ON p.slug='sessions.terminate' WHERE r.slug='owner' ON CONFLICT DO NOTHING""")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_reauth_grants, admin_trusted_devices")
    op.execute("DROP INDEX IF EXISTS admin_reauth_active_idx")
    op.execute("ALTER TABLE admin_sessions DROP COLUMN IF EXISTS user_agent, DROP COLUMN IF EXISTS device_name, DROP COLUMN IF EXISTS browser, DROP COLUMN IF EXISTS operating_system, DROP COLUMN IF EXISTS country, DROP COLUMN IF EXISTS city")
