"""register Lurg Notes with the central identity provider

Revision ID: 20260809_0020
Revises: 20260804_0019
"""
from alembic import op

revision = "20260809_0020"
down_revision = "20260804_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("INSERT INTO auth_applications(slug,display_name,redirect_uris) VALUES ('lurg_notes','Lurg Notes','[\"pro.lurg.notes://auth/callback\"]'::jsonb) ON CONFLICT (slug) DO UPDATE SET display_name=EXCLUDED.display_name, redirect_uris=EXCLUDED.redirect_uris, active=true")


def downgrade() -> None:
    op.execute("DELETE FROM auth_applications WHERE slug='lurg_notes'")
