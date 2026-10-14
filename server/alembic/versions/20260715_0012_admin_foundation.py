"""corporate admin identity, RBAC, immutable audit and delivery foundation

Revision ID: 20260715_0012
Revises: 20260714_0011
"""
from alembic import op

revision = "20260715_0012"
down_revision = "20260714_0011"
branch_labels = None
depends_on = None

DDL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS status varchar(20) NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS security_version integer NOT NULL DEFAULT 1;
ALTER TABLE users ADD CONSTRAINT users_status_valid CHECK (status IN ('active','blocked')) NOT VALID;
ALTER TABLE users VALIDATE CONSTRAINT users_status_valid;

CREATE TABLE IF NOT EXISTS admin_roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), slug varchar(50) UNIQUE NOT NULL,
  name varchar(100) NOT NULL, is_system boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS admin_permissions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), slug varchar(100) UNIQUE NOT NULL, description text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS admin_role_permissions (
  role_id uuid NOT NULL REFERENCES admin_roles(id) ON DELETE CASCADE, permission_id uuid NOT NULL REFERENCES admin_permissions(id) ON DELETE CASCADE,
  PRIMARY KEY(role_id, permission_id));
CREATE TABLE IF NOT EXISTS admin_user_roles (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, role_id uuid NOT NULL REFERENCES admin_roles(id) ON DELETE RESTRICT,
  granted_by uuid REFERENCES users(id) ON DELETE SET NULL, granted_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
  PRIMARY KEY(user_id, role_id));
CREATE TABLE IF NOT EXISTS admin_user_permissions (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, permission_id uuid NOT NULL REFERENCES admin_permissions(id) ON DELETE CASCADE,
  granted_by uuid REFERENCES users(id) ON DELETE SET NULL, granted_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz,
  PRIMARY KEY(user_id, permission_id));
CREATE TABLE IF NOT EXISTS admin_invitations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), invitee_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  invitee_name varchar(120) NOT NULL, role_id uuid NOT NULL REFERENCES admin_roles(id) ON DELETE RESTRICT,
  code_hash bytea NOT NULL UNIQUE, expires_at timestamptz NOT NULL, comment text, created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(), used_at timestamptz, revoked_at timestamptz, revoked_by uuid REFERENCES users(id) ON DELETE SET NULL,
  CHECK (expires_at > created_at));
CREATE TABLE IF NOT EXISTS admin_enrollments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), invitation_id uuid UNIQUE REFERENCES admin_invitations(id) ON DELETE RESTRICT,
  user_id uuid UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE, totp_secret_encrypted bytea NOT NULL,
  totp_enabled_at timestamptz NOT NULL, recovery_codes_hash jsonb NOT NULL DEFAULT '[]', recovery_codes_used_at jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS admin_login_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, nonce_hash bytea NOT NULL UNIQUE,
  purpose varchar(30) NOT NULL CHECK (purpose IN ('enrollment','login')), invitation_id uuid REFERENCES admin_invitations(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL, attempts smallint NOT NULL DEFAULT 0, consumed_at timestamptz, ip inet, user_agent_hash bytea, created_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE admin_login_challenges ADD COLUMN IF NOT EXISTS setup_secret_encrypted bytea;
CREATE TABLE IF NOT EXISTS admin_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, token_hash bytea NOT NULL UNIQUE,
  csrf_hash bytea NOT NULL, security_version integer NOT NULL, ip inet, user_agent_hash bytea, created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL, revoked_at timestamptz, replaced_by uuid, CHECK(expires_at > created_at));
CREATE TABLE IF NOT EXISTS admin_audit_log (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, occurred_at timestamptz NOT NULL DEFAULT now(), actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  action varchar(100) NOT NULL, target_type varchar(50) NOT NULL, target_id uuid, outcome varchar(20) NOT NULL, request_id uuid NOT NULL DEFAULT gen_random_uuid(), ip inet,
  user_agent_hash bytea, metadata jsonb NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS admin_push_campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT, title varchar(160) NOT NULL, body varchar(2000) NOT NULL,
  data jsonb NOT NULL DEFAULT '{}', audience jsonb NOT NULL, status varchar(20) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','queued','sending','completed','failed')),
  scheduled_at timestamptz, sent_at timestamptz, total_recipients integer NOT NULL DEFAULT 0, delivered_count integer NOT NULL DEFAULT 0, failed_count integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS admin_invitation_user_idx ON admin_invitations(invitee_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS admin_sessions_user_idx ON admin_sessions(user_id, expires_at DESC) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS admin_audit_actor_idx ON admin_audit_log(actor_user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS admin_audit_target_idx ON admin_audit_log(target_type, target_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS admin_login_challenges_user_idx ON admin_login_challenges(user_id, expires_at DESC);
INSERT INTO admin_roles(slug,name) VALUES ('owner','Owner'),('admin','Admin'),('developer','Developer'),('support','Support'),('moderator','Moderator'),('analyst','Analyst'),('employee','Employee') ON CONFLICT (slug) DO NOTHING;
INSERT INTO admin_permissions(slug,description) VALUES
 ('admin.all','Full administrative access'),('invitations.create','Create invitations'),('users.read','Read user data'),('users.manage','Manage user access'),('push.send','Send push notifications'),('audit.read','Read audit trail'),('roles.manage','Manage roles') ON CONFLICT(slug) DO NOTHING;
INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r CROSS JOIN admin_permissions p WHERE r.slug IN ('owner','admin') ON CONFLICT DO NOTHING;
INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r JOIN admin_permissions p ON p.slug IN ('users.read','push.send') WHERE r.slug='support' ON CONFLICT DO NOTHING;
INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r JOIN admin_permissions p ON p.slug IN ('users.read','audit.read') WHERE r.slug='analyst' ON CONFLICT DO NOTHING;
"""

def upgrade() -> None:
    # asyncpg permits one statement per prepared execution.
    for statement in DDL.split(";\n"):
        if statement.strip():
            op.execute(statement)
    op.execute("""CREATE OR REPLACE FUNCTION admin_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'admin audit log is append-only'; END; $$""")
    op.execute("DROP TRIGGER IF EXISTS admin_audit_no_mutation ON admin_audit_log")
    op.execute("CREATE TRIGGER admin_audit_no_mutation BEFORE UPDATE OR DELETE ON admin_audit_log FOR EACH ROW EXECUTE FUNCTION admin_audit_immutable()")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_push_campaigns, admin_audit_log, admin_sessions, admin_login_challenges, admin_enrollments, admin_invitations, admin_user_permissions, admin_user_roles, admin_role_permissions, admin_permissions, admin_roles CASCADE")
