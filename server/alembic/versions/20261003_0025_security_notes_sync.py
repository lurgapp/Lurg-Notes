"""Atomic identity proofs and ordered encrypted Notes sync.

Revision ID: 20261003_0025
Revises: 20260819_0024
"""
from alembic import op

revision = "20261003_0025"
down_revision = "20260819_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing accounts keep their verified state; new identities require OTP.
    op.execute("ALTER TABLE users ALTER COLUMN email_verified SET DEFAULT false")
    op.execute("ALTER TABLE auth_refresh_tokens ADD COLUMN security_version integer")
    op.execute("UPDATE auth_refresh_tokens t SET security_version=u.security_version FROM users u WHERE u.id=t.user_id")
    op.execute("ALTER TABLE auth_refresh_tokens ALTER COLUMN security_version SET NOT NULL")
    # A legacy backend can still insert refresh rows during an emergency code
    # rollback. On rollforward, versions above1 safely require reauthentication.
    op.execute("ALTER TABLE auth_refresh_tokens ALTER COLUMN security_version SET DEFAULT 1")
    op.execute("""CREATE TABLE auth_reset_proofs (
      token_hash text PRIMARY KEY, email text NOT NULL, app_name text NOT NULL,
      expires_at timestamptz NOT NULL, consumed_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("CREATE INDEX auth_reset_proofs_expiry_idx ON auth_reset_proofs(expires_at)")
    op.execute("CREATE SEQUENCE notes_sync_sequence")
    op.execute("ALTER TABLE notes ADD COLUMN sync_sequence bigint NOT NULL DEFAULT 0")
    # Bring the shared row in line with the latest opaque envelope before CAS.
    op.execute("""UPDATE notes n SET revision=CASE WHEN n.deleted_at IS NULL THEN d.document_revision ELSE GREATEST(n.revision,d.document_revision+1) END
      FROM note_e2ee_documents d WHERE d.note_id=n.id AND d.format_version=2""")
    op.execute("UPDATE notes SET sync_sequence=nextval('notes_sync_sequence')")
    op.execute("CREATE INDEX notes_owner_sync_idx ON notes(user_id,sync_sequence)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS notes_owner_sync_idx")
    op.execute("ALTER TABLE notes DROP COLUMN sync_sequence")
    op.execute("DROP SEQUENCE notes_sync_sequence")
    op.execute("DROP TABLE auth_reset_proofs")
    op.execute("ALTER TABLE auth_refresh_tokens DROP COLUMN security_version")
    # Never re-enable implicit email verification on downgrade.
