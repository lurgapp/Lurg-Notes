# Lurg Notes E2EE v2

## Threat model and non-goals

The service stores encrypted document envelopes, device public keys, wrapped
user-key envelopes, timestamps, revisions, sizes and deletion state.  It must
never receive a plaintext note, a plaintext User Master Key (UMK), or a device
private key. TLS protects transport; E2EE protects a database leak and a
malicious-but-honest storage service from note disclosure.

An authenticated attacker who creates a new device cannot decrypt existing
notes: an already trusted device has to approve and wrap the UMK for it. A
revoked device is prevented from making future sync/key-material requests, but
revocation cannot erase a UMK or plaintext it already obtained. Android
Keystore protects private keys and locally wrapped key material at rest (and
often in hardware); it does not defend against a compromised, unlocked process
that can legitimately ask the Keystore to decrypt.

## Recovery-secret extension

v2 additionally supports opt-in recovery with a user-held recovery secret. On
first setup the device generates a 32-byte random recovery secret and shows it
once for offline storage. It is never derived from the account password and is
never sent to the service. A recovery key is derived locally with HKDF-SHA-256
from that secret, the random recovery salt and the fixed info string
`lurg-notes:v2:recovery-wrap`; it AES-256-GCM encrypts the UMK with AAD
`lurg-notes:v2:recovery:<owner-id>`. The service stores only the salt, nonce
and ciphertext of this recovery envelope.

Recovery enrolment requires both a normal Lurg account session and a proof made
with a distinct HKDF-derived recovery authentication key. The service stores
only that derived verifier, not the recovery secret or any key that can decrypt
the UMK. This verifier can authorize creation of a device after a database
leak, but cannot recover notes, UMK, or a device private key. A recovered fresh
device locally decrypts the recovery envelope, then supplies an HMAC-SHA-256
proof bound to its public key and device id; the service activates that device. There is no
password-derived fallback, server-side plaintext recovery key, or hidden
backdoor. Rotating recovery material requires an already active device and
replaces both opaque recovery fields atomically.

## Key hierarchy and envelope format

* A UMK is 32 random bytes generated with `SecureRandom` on the first trusted
  device for an account.
* A document is encrypted locally with AES-256-GCM under the UMK. `Cipher`
  generates a fresh 96-bit nonce for every encryption. AAD is UTF-8
  `lurg-notes:v2:<owner-id>:<note-id>`.
* A server-visible content equality token is HMAC-SHA-256(UMK, plaintext), not
  a plaintext SHA-256 digest. This supports idempotent retries without making
  a known plaintext dictionary practical for the server.
* Each Android device creates an RSA-3072 key pair in Android Keystore. The
  public key is SPKI DER/Base64. UMK delivery uses RSA-OAEP with SHA-256 for
  both digest and MGF1, and is bound to the receiving owner/device by OAEP
  label/AAD-equivalent context.
* The UMK at rest on a device is AES-GCM-wrapped by a non-exportable local
  Android Keystore AES-256 key. Only nonce and ciphertext are in app-private
  preferences. Device private keys and local wrapping keys are never exported.
* `format_version=2` and `key_version=2` identify this format. Readers reject
  unknown versions and malformed Base64/nonces/tags; they never return partial
  plaintext.

RSA-OAEP key transport and Android Keystore are platform primitives, not a
custom cryptographic algorithm. The exact OAEP digest parameters are explicit
because Android providers otherwise have historical MGF1 defaults.

## Enrollment and revocation protocol

1. A freshly logged-in device generates its Keystore RSA key pair and submits
   only its public key, name and random device id.
2. If no active device exists it is the initial trusted device. Otherwise the
   server creates a pending enrollment and gives it no E2EE/sync access.
3. An active device displays the pending device name and public-key
   fingerprint. Explicit user approval wraps its UMK to that public key and
   uploads the opaque envelope. The server activates the new device only in
   that transaction.
4. The approved device fetches only its own wrapped UMK and decrypts it with
   its local Keystore private key.
5. Revocation marks the device revoked and invalidates its per-device sync
   credential. It cannot request future envelopes or sync. It cannot retract
   knowledge already on that device.

The first v2 implementation must require the device credential on every Notes
E2EE request, in addition to the Lurg access token. A bearer account token by
itself is therefore insufficient to turn an unapproved login into an E2EE
reader.

## Migration from v1

An existing v1 device is the only place that can decrypt its device-bound
envelopes. It first registers/bootstraps v2 locally, generates and stores the
UMK, decrypts each v1 local/cloud document locally, and writes a v2 envelope.
The original local Room document and v1 server envelope are retained until a
successful v2 upload is acknowledged. Progress is durable per note; a restart
retries safely. A v2 envelope at equal document revision supersedes a v1
envelope only when its format version is higher. Plaintext is never sent during
migration.

This document is the design constraint for implementation, not evidence that
the v2 protocol has already been implemented or audited.
