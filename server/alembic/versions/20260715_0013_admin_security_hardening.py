"""admin security hardening

Revision ID: 20260715_0013
Revises: 20260715_0012
"""
from alembic import op

revision = "20260715_0013"
down_revision = "20260715_0012"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""CREATE OR REPLACE FUNCTION admin_enforce_single_owner() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NEW.revoked_at IS NULL AND EXISTS(SELECT 1 FROM admin_roles WHERE id=NEW.role_id AND slug='owner')
         AND EXISTS(SELECT 1 FROM admin_user_roles ur JOIN admin_roles r ON r.id=ur.role_id WHERE r.slug='owner' AND ur.revoked_at IS NULL AND ur.user_id<>NEW.user_id) THEN
        RAISE EXCEPTION 'exactly one active Owner is allowed';
      END IF; RETURN NEW; END; $$""")
    op.execute("DROP TRIGGER IF EXISTS admin_single_owner_guard ON admin_user_roles")
    op.execute("CREATE TRIGGER admin_single_owner_guard BEFORE INSERT OR UPDATE OF role_id,revoked_at ON admin_user_roles FOR EACH ROW EXECUTE FUNCTION admin_enforce_single_owner()")
    op.execute("""CREATE OR REPLACE FUNCTION admin_prevent_owner_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF EXISTS(SELECT 1 FROM admin_user_roles ur JOIN admin_roles r ON r.id=ur.role_id WHERE ur.user_id=OLD.id AND r.slug='owner' AND ur.revoked_at IS NULL) THEN
        RAISE EXCEPTION 'Owner account cannot be deleted';
      END IF; RETURN OLD; END; $$""")
    op.execute("DROP TRIGGER IF EXISTS admin_owner_delete_guard ON users")
    op.execute("CREATE TRIGGER admin_owner_delete_guard BEFORE DELETE ON users FOR EACH ROW EXECUTE FUNCTION admin_prevent_owner_delete()")
    op.execute("CREATE INDEX IF NOT EXISTS admin_session_token_active_idx ON admin_sessions(token_hash) WHERE revoked_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS admin_invitation_code_active_idx ON admin_invitations(code_hash) WHERE used_at IS NULL AND revoked_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS admin_challenge_nonce_active_idx ON admin_login_challenges(nonce_hash) WHERE consumed_at IS NULL")
    op.execute("CREATE TABLE IF NOT EXISTS admin_rate_limits (scope varchar(80) NOT NULL, subject_hash bytea NOT NULL, window_started_at timestamptz NOT NULL, attempts integer NOT NULL DEFAULT 0, blocked_until timestamptz, PRIMARY KEY(scope,subject_hash))")
    op.execute("""CREATE TABLE IF NOT EXISTS admin_push_deliveries (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), campaign_id uuid NOT NULL REFERENCES admin_push_campaigns(id) ON DELETE CASCADE,
      device_token_id uuid NOT NULL REFERENCES app_device_tokens(id) ON DELETE CASCADE, status varchar(20) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed')),
      error_code varchar(100), delivered_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(campaign_id,device_token_id))""")
    op.execute("CREATE INDEX IF NOT EXISTS admin_push_delivery_pending_idx ON admin_push_deliveries(status,created_at) WHERE status='pending'")
    op.execute("ALTER TABLE admin_login_challenges ADD COLUMN IF NOT EXISTS recovery_mode boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE admin_enrollments ADD COLUMN IF NOT EXISTS last_totp_counter bigint")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_push_deliveries")
    op.execute("DROP TABLE IF EXISTS admin_rate_limits")
    op.execute("DROP TRIGGER IF EXISTS admin_single_owner_guard ON admin_user_roles")
    op.execute("DROP TRIGGER IF EXISTS admin_owner_delete_guard ON users")
    op.execute("DROP FUNCTION IF EXISTS admin_enforce_single_owner()")
    op.execute("DROP FUNCTION IF EXISTS admin_prevent_owner_delete()")
    op.execute("DROP INDEX IF EXISTS admin_session_token_active_idx, admin_invitation_code_active_idx, admin_challenge_nonce_active_idx")
