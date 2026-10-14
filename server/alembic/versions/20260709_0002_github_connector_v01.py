"""github connector v0.1 storage constraints

Revision ID: 20260709_0002
Revises: 20260709_0001
Create Date: 2026-07-09
"""

from alembic import op

revision = "20260709_0002"
down_revision = "20260709_0001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS external_connections_user_provider_uidx ON external_connections(user_id, provider)")
    op.execute("CREATE INDEX IF NOT EXISTS connector_raw_events_provider_external_idx ON connector_raw_events(provider, external_record_id)")

def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS connector_raw_events_provider_external_idx")
    op.execute("DROP INDEX IF EXISTS external_connections_user_provider_uidx")
