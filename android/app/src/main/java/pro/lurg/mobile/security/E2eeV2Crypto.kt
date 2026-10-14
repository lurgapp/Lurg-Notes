package pro.lurg.notes.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.nio.charset.StandardCharsets
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.SecureRandom
import java.security.spec.MGF1ParameterSpec
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.Mac
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.OAEPParameterSpec
import javax.crypto.spec.PSource

data class E2eeEnvelope(val nonce: String, val ciphertext: String)
data class RecoveryMaterial(val secret: String, val salt: String, val envelope: E2eeEnvelope, val authVerifier: String)

/**
 * v2 key hierarchy. The UMK only exists as bytes while cryptographic work is
 * in progress; persisted UMK bytes are AES-GCM wrapped by a non-exportable
 * Android Keystore key. RSA private keys never leave AndroidKeyStore.
 */
class E2eeV2Crypto(private val context: Context, private val boundOwner: String? = SecureTokenStore(context).ownerId()) {
  private val store = SecureTokenStore(context)
  private val random = SecureRandom()
  private fun getProtectedValue(name: String) = store.getProtectedValue(name, boundOwner)
  private fun setProtectedValue(name: String, value: String) = store.setProtectedValue(name, value, boundOwner)
  private fun removeProtectedValue(name: String) = store.removeProtectedValue(name, boundOwner)

  fun deviceId(): String = synchronized(deviceIdLock) {
    getProtectedValue("device_id") ?: UUID.randomUUID().toString().also { setProtectedValue("device_id", it) }
  }

  fun publicKey(): String {
    val alias = rsaAlias()
    val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
    if (!keyStore.containsAlias(alias)) {
      KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_RSA, ANDROID_KEYSTORE).apply {
        initialize(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_DECRYPT or KeyProperties.PURPOSE_ENCRYPT)
          .setKeySize(3072)
          .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP)
          .setDigests(KeyProperties.DIGEST_SHA256)
          .build())
      }.generateKeyPair()
    }
    return b64(keyStore.getCertificate(alias).publicKey.encoded)
  }

  fun hasUmk(): Boolean = getProtectedValue("umk_cipher") != null && getProtectedValue("umk_nonce") != null

  fun createUmk(): ByteArray = ByteArray(32).also(random::nextBytes)

  fun saveUmk(umk: ByteArray) {
    require(umk.size == 32) { "UMK must be 256 bits" }
    val cipher = Cipher.getInstance(AES_GCM).apply { init(Cipher.ENCRYPT_MODE, wrappingKey()) }
    cipher.updateAAD(umkAad())
    val encrypted = cipher.doFinal(umk)
    setProtectedValue("umk_cipher", b64(encrypted))
    setProtectedValue("umk_nonce", b64(cipher.iv))
  }

  fun withUmk(block: (ByteArray) -> Unit) {
    val encrypted = requireNotNull(getProtectedValue("umk_cipher")) { "UMK is unavailable" }
    val nonce = requireNotNull(getProtectedValue("umk_nonce")) { "UMK is unavailable" }
    val cipher = Cipher.getInstance(AES_GCM).apply {
      init(Cipher.DECRYPT_MODE, wrappingKey(), GCMParameterSpec(128, unb64(nonce)))
      updateAAD(umkAad())
    }
    val raw = cipher.doFinal(unb64(encrypted))
    try { block(raw) } finally { raw.fill(0) }
  }

  fun encryptDocument(ownerId: String, noteId: String, plain: ByteArray): E2eeEnvelope {
    var result: E2eeEnvelope? = null
    withUmk { umk ->
      val cipher = Cipher.getInstance(AES_GCM).apply { init(Cipher.ENCRYPT_MODE, javax.crypto.spec.SecretKeySpec(umk, "AES")) }
      cipher.updateAAD(documentAad(ownerId, noteId))
      result = E2eeEnvelope(b64(cipher.iv), b64(cipher.doFinal(plain)))
    }
    return requireNotNull(result)
  }

  fun decryptDocument(ownerId: String, noteId: String, envelope: E2eeEnvelope): ByteArray {
    var result: ByteArray? = null
    withUmk { umk ->
      val nonce = unb64(envelope.nonce)
      require(nonce.size == 12) { "Invalid v2 nonce" }
      val cipher = Cipher.getInstance(AES_GCM).apply {
        init(Cipher.DECRYPT_MODE, javax.crypto.spec.SecretKeySpec(umk, "AES"), GCMParameterSpec(128, nonce))
        updateAAD(documentAad(ownerId, noteId))
      }
      result = cipher.doFinal(unb64(envelope.ciphertext))
    }
    return requireNotNull(result)
  }

  fun contentToken(plain: ByteArray): String {
    var token: String? = null
    withUmk { umk -> token = b64(Mac.getInstance("HmacSHA256").run { init(javax.crypto.spec.SecretKeySpec(umk, "HmacSHA256")); doFinal(plain) }) }
    return requireNotNull(token)
  }

  fun wrapUmkFor(publicKeyB64: String, ownerId: String, recipientDeviceId: String): String {
    var wrapped: String? = null
    withUmk { umk ->
      val factory = java.security.KeyFactory.getInstance("RSA")
      val recipient = factory.generatePublic(java.security.spec.X509EncodedKeySpec(unb64(publicKeyB64)))
      val cipher = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding").apply {
        init(Cipher.ENCRYPT_MODE, recipient, oaep(contextFor(ownerId, recipientDeviceId)))
      }
      wrapped = b64(cipher.doFinal(umk))
    }
    return requireNotNull(wrapped)
  }

  fun unwrapAndSaveUmk(wrapped: String, ownerId: String, recipientDeviceId: String) {
    val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
    val privateKey = (keyStore.getEntry(rsaAlias(), null) as KeyStore.PrivateKeyEntry).privateKey
    val raw = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding").run {
      init(Cipher.DECRYPT_MODE, privateKey, oaep(contextFor(ownerId, recipientDeviceId)))
      doFinal(unb64(wrapped))
    }
    try { saveUmk(raw) } finally { raw.fill(0) }
  }

  fun createRecovery(ownerId: String, umk: ByteArray): RecoveryMaterial {
    val secret = ByteArray(32).also(random::nextBytes)
    val salt = ByteArray(16).also(random::nextBytes)
    val wrapKey = hkdf(secret, salt, "lurg-notes:v2:recovery-wrap".toByteArray(), 32)
    val authKey = hkdf(secret, salt, "lurg-notes:v2:recovery-auth".toByteArray(), 32)
    val cipher = Cipher.getInstance(AES_GCM).apply { init(Cipher.ENCRYPT_MODE, javax.crypto.spec.SecretKeySpec(wrapKey, "AES")) }
    cipher.updateAAD("lurg-notes:v2:recovery:$ownerId".toByteArray(StandardCharsets.UTF_8))
    val envelope = E2eeEnvelope(b64(cipher.iv), b64(cipher.doFinal(umk)))
    wrapKey.fill(0)
    return RecoveryMaterial(b64(secret), b64(salt), envelope, b64(authKey)).also { secret.fill(0); authKey.fill(0) }
  }

  fun recoveryProof(secretB64: String, saltB64: String, ownerId: String, deviceId: String, publicKey: String): String {
    val fingerprint = sha256(unb64(publicKey)).joinToString("") { "%02x".format(it) }
    val authKey = hkdf(unb64(secretB64), unb64(saltB64), "lurg-notes:v2:recovery-auth".toByteArray(), 32)
    val context = "lurg-notes:v2:recovery-enroll:$ownerId:$deviceId:$fingerprint".toByteArray(StandardCharsets.UTF_8)
    return b64(Mac.getInstance("HmacSHA256").run { init(javax.crypto.spec.SecretKeySpec(authKey, "HmacSHA256")); doFinal(context) }).also { authKey.fill(0) }
  }

  fun recoverAndSaveUmk(secretB64: String, saltB64: String, envelope: E2eeEnvelope, ownerId: String) {
    val secret = unb64(secretB64)
    val wrapKey = hkdf(secret, unb64(saltB64), "lurg-notes:v2:recovery-wrap".toByteArray(), 32)
    val raw = Cipher.getInstance(AES_GCM).run {
      init(Cipher.DECRYPT_MODE, javax.crypto.spec.SecretKeySpec(wrapKey, "AES"), GCMParameterSpec(128, unb64(envelope.nonce)))
      updateAAD("lurg-notes:v2:recovery:$ownerId".toByteArray(StandardCharsets.UTF_8))
      doFinal(unb64(envelope.ciphertext))
    }
    try { require(raw.size == 32); saveUmk(raw) } finally { secret.fill(0); wrapKey.fill(0); raw.fill(0) }
  }

  private fun documentAad(ownerId: String, noteId: String) = "lurg-notes:v2:$ownerId:$noteId".toByteArray(StandardCharsets.UTF_8)
  private fun umkAad() = "lurg-notes:v2:local-umk:${deviceId()}".toByteArray(StandardCharsets.UTF_8)
  private fun contextFor(ownerId: String, recipient: String) = "lurg-notes:v2:umk:$ownerId:$recipient".toByteArray(StandardCharsets.UTF_8)
  private fun rsaAlias() = "lurg_notes_v2_rsa_${deviceId()}"
  private fun wrappingKey(): SecretKey {
    val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
    val alias = "lurg_notes_v2_umk_${deviceId()}"
    (keyStore.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.secretKey?.let { return it }
    return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE).apply {
      init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
        .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setRandomizedEncryptionRequired(true).build())
    }.generateKey()
  }
  private fun oaep(label: ByteArray) = OAEPParameterSpec("SHA-256", "MGF1", MGF1ParameterSpec.SHA256, PSource.PSpecified(label))
  private fun hkdf(ikm: ByteArray, salt: ByteArray, info: ByteArray, length: Int): ByteArray {
    val prk = Mac.getInstance("HmacSHA256").run { init(javax.crypto.spec.SecretKeySpec(salt, "HmacSHA256")); doFinal(ikm) }
    val output = ByteArray(length); var previous = ByteArray(0); var offset = 0; var counter = 1
    while (offset < length) {
      previous = Mac.getInstance("HmacSHA256").run { init(javax.crypto.spec.SecretKeySpec(prk, "HmacSHA256")); update(previous); update(info); doFinal(byteArrayOf(counter++.toByte())) }
      val copied = minOf(previous.size, length - offset); System.arraycopy(previous, 0, output, offset, copied); offset += copied
    }
    prk.fill(0); previous.fill(0); return output
  }
  private fun sha256(data: ByteArray) = java.security.MessageDigest.getInstance("SHA-256").digest(data)
  private fun b64(value: ByteArray) = Base64.encodeToString(value, Base64.NO_WRAP)
  private fun unb64(value: String) = Base64.decode(value, Base64.NO_WRAP)
  private companion object {
    const val ANDROID_KEYSTORE = "AndroidKeyStore"
    const val AES_GCM = "AES/GCM/NoPadding"
    val deviceIdLock = Any()
  }
}
