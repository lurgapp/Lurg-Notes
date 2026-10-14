"""shared auth.lurg.pro email identity provider

Revision ID: 20260804_0019
Revises: 20260715_0018
"""
from alembic import op

revision = "20260804_0019"
down_revision = "20260715_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing Lurg accounts predate verification and remain active.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified boolean NOT NULL DEFAULT true")
    op.execute("CREATE TABLE IF NOT EXISTS auth_email_otps (email text NOT NULL, purpose text NOT NULL, app_name text NOT NULL, code_digest text NOT NULL, attempts integer NOT NULL DEFAULT 0, expires_at timestamptz NOT NULL, resend_after timestamptz NOT NULL, metadata jsonb NOT NULL DEFAULT '{}'::jsonb, updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(email, purpose))")
    op.execute("CREATE TABLE IF NOT EXISTS auth_refresh_tokens (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, token_hash text NOT NULL UNIQUE, app_name text NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now())")
    op.execute("CREATE INDEX IF NOT EXISTS auth_refresh_tokens_user_idx ON auth_refresh_tokens(user_id)")
    op.execute("CREATE TABLE IF NOT EXISTS auth_applications (slug text PRIMARY KEY, display_name text NOT NULL, redirect_uris jsonb NOT NULL DEFAULT '[]'::jsonb, active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now())")
    op.execute("INSERT INTO auth_applications(slug,display_name) VALUES ('nearby','Nearby'),('graph','Graph') ON CONFLICT (slug) DO NOTHING")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_applications")
    op.execute("DROP TABLE IF EXISTS auth_refresh_tokens")
    op.execute("DROP TABLE IF EXISTS auth_email_otps")
    # Do not remove email_verified: a downgrade must not discard verification state.
