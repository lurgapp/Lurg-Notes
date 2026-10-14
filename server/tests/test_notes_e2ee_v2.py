import base64

import pytest
from pydantic import ValidationError

from app.modules.notes.e2ee import EncryptedDocument, RecoverySetupRequest


def encoded(size: int) -> str:
    return base64.b64encode(b"x" * size).decode()


def test_v2_envelope_rejects_unknown_version_and_non_96_bit_nonce():
    with pytest.raises(ValidationError):
        EncryptedDocument(format_version=1, key_version=2, algorithm="AES-256-GCM", nonce=encoded(12), ciphertext=encoded(32), content_hash=encoded(32), document_revision=1)
    with pytest.raises(ValidationError):
        EncryptedDocument(format_version=2, key_version=2, algorithm="AES-256-GCM", nonce=encoded(16), ciphertext=encoded(32), content_hash=encoded(32), document_revision=1)


def test_v2_envelope_rejects_malformed_payload_and_recovery_never_accepts_plaintext_fields():
    with pytest.raises(ValidationError):
        EncryptedDocument(format_version=2, key_version=2, algorithm="AES-256-GCM", nonce="not base64!", ciphertext=encoded(32), content_hash=encoded(32), document_revision=1)
    with pytest.raises(ValidationError):
        RecoverySetupRequest(recovery_salt="plain recovery secret", nonce=encoded(12), ciphertext=encoded(32), recovery_auth_verifier=encoded(32))
