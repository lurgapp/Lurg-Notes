"""support center read-only diagnostics

Revision ID: 20260715_0016
Revises: 20260715_0015
"""
from alembic import op
revision="20260715_0016"; down_revision="20260715_0015"; branch_labels=None; depends_on=None
def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS users_email_lower_idx ON users(lower(email))")
    op.execute("CREATE INDEX IF NOT EXISTS users_name_search_idx ON users USING gin(to_tsvector('simple',name))")
    op.execute("CREATE INDEX IF NOT EXISTS admin_sessions_active_user_idx ON admin_sessions(user_id,last_seen_at DESC) WHERE revoked_at IS NULL")
    op.execute("INSERT INTO admin_permissions(slug,description) VALUES ('support.diagnostics.read','Read safe user diagnostics') ON CONFLICT(slug) DO NOTHING")
    op.execute("""INSERT INTO admin_role_permissions(role_id,permission_id) SELECT r.id,p.id FROM admin_roles r JOIN admin_permissions p ON p.slug='support.diagnostics.read' WHERE r.slug IN ('owner','admin','developer','support') ON CONFLICT DO NOTHING""")
def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS users_email_lower_idx, users_name_search_idx, admin_sessions_active_user_idx")
