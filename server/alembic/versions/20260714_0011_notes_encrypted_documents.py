"""Opaque E2E document envelopes for Lurg Notes sync."""
from alembic import op

revision = "20260714_0011"
down_revision = "20260714_0010"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS note_encrypted_documents (
      note_id uuid PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      algorithm varchar(40) NOT NULL,
      key_version integer NOT NULL,
      nonce text NOT NULL,
      ciphertext text NOT NULL,
      content_hash text NOT NULL,
      document_revision integer NOT NULL,
      updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS note_encrypted_documents_owner_idx ON note_encrypted_documents(user_id,updated_at DESC)")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS note_encrypted_documents")
