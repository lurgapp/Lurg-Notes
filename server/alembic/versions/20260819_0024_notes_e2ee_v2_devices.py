"""Device-bound Lurg Notes E2EE v2 envelopes and enrolment state."""
from alembic import op


revision = "20260819_0024"
down_revision = "20260814_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS note_e2ee_devices (
      id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      name varchar(120) NOT NULL,
      public_key text NOT NULL,
      public_key_fingerprint varchar(128) NOT NULL,
      credential_hash varchar(128) NOT NULL,
      status varchar(16) NOT NULL CHECK(status IN ('pending','active','revoked')),
      wrapped_umk text,
      wrap_algorithm varchar(64),
      wrap_context varchar(300),
      approved_by uuid REFERENCES note_e2ee_devices(id),
      approved_at timestamptz,
      revoked_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(user_id, public_key_fingerprint)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS note_e2ee_devices_user_status_idx ON note_e2ee_devices(user_id,status)")
    op.execute("""CREATE TABLE IF NOT EXISTS note_e2ee_recovery (
      user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      recovery_salt text NOT NULL,
      nonce text NOT NULL,
      ciphertext text NOT NULL,
      recovery_auth_verifier text NOT NULL,
      version integer NOT NULL DEFAULT 1,
      updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    # Keep v1 rows in note_encrypted_documents intact until every client has
    # acknowledged a v2 envelope. The pair is intentionally versioned.
    op.execute("""CREATE TABLE IF NOT EXISTS note_e2ee_documents (
      note_id uuid NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      format_version integer NOT NULL,
      key_version integer NOT NULL,
      algorithm varchar(40) NOT NULL,
      nonce text NOT NULL,
      ciphertext text NOT NULL,
      content_hash text NOT NULL,
      document_revision integer NOT NULL,
      updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(note_id,format_version)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS note_e2ee_documents_owner_idx ON note_e2ee_documents(user_id,format_version,updated_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS note_e2ee_documents")
    op.execute("DROP TABLE IF EXISTS note_e2ee_recovery")
    op.execute("DROP TABLE IF EXISTS note_e2ee_devices")
