package pro.lurg.notes.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class NotesSessionSnapshot(val ownerId: String, val accessToken: String) {
  override fun toString(): String = "NotesSessionSnapshot(ownerId=$ownerId)"
}

class SecureTokenStore(private val context: Context) {
  private val prefs = context.getSharedPreferences("lurg_secure_session", Context.MODE_PRIVATE)

  fun getToken(): String? = synchronized(sessionLock) { getValue("token") }

  /** Owner and token must come from the same durable session commit. */
  fun sessionSnapshot(): NotesSessionSnapshot? = synchronized(sessionLock) {
    val owner = ownerId()?.takeIf(String::isNotBlank) ?: return@synchronized null
    getValue("token")?.takeIf(String::isNotBlank)?.let { NotesSessionSnapshot(owner, it) }
  }

  fun getTokenForOwner(ownerId: String): String = synchronized(sessionLock) {
    check(ownerId() == ownerId) { "Сессия владельца изменилась" }
    requireNotNull(getValue("token")) { "Нет сессии Lurg ID" }
  }

  fun isCurrentSession(ownerId: String, token: String): Boolean = synchronized(sessionLock) {
    ownerId() == ownerId && getValue("token") == token
  }

  fun setToken(token: String) {
    synchronized(sessionLock) { setValue("token", token) }
  }

  fun getRefreshToken(): String? = synchronized(sessionLock) { getValue("refresh_token") }

  fun setSession(accessToken: String, refreshToken: String, ownerId: String? = null) = synchronized(sessionLock) {
    val owner = requireNotNull(ownerId ?: ownerId()) { "Сессия не содержит владельца" }
    require(owner.isNotBlank())
    val access = encryptValue(accessToken)
    val refresh = encryptValue(refreshToken)
    // One durable preferences commit also protects a process death during login.
    prefs.edit().putString("token_cipher", access.first).putString("token_iv", access.second)
      .putString("refresh_token_cipher", refresh.first).putString("refresh_token_iv", refresh.second)
      .putString("active_e2ee_owner", owner).commit()
  }

  /** Keystore-encrypted small E2EE metadata; never use this for note plaintext. */
  fun getProtectedValue(name: String, ownerId: String? = ownerId()): String? = ownerPrefix(ownerId)?.let { getValue("${it}_$name") }

  fun setProtectedValue(name: String, value: String, ownerId: String? = ownerId()) {
    setValue("${requireNotNull(ownerPrefix(ownerId)) { "E2EE owner is unavailable" }}_$name", value)
  }

  fun removeProtectedValue(name: String, ownerId: String? = ownerId()) {
    val prefix = ownerPrefix(ownerId) ?: return
    prefs.edit().remove("${prefix}_${name}_cipher").remove("${prefix}_${name}_iv").apply()
  }

  /** Preserve this installation's trusted device and UMK across ordinary logout. */
  fun bindLegacyOwnerIfMissing(ownerId: String) = synchronized(sessionLock) {
    if (ownerId() == null) bindOwner(ownerId, migrateLegacy = true)
  }

  fun bindOwner(ownerId: String, migrateLegacy: Boolean = false) = synchronized(sessionLock) {
    require(ownerId.isNotBlank())
    val prefix = "owner_${ownerId.replace(Regex("[^A-Za-z0-9-]"), "_")}_e2ee"
    val edit = prefs.edit()
    // The caller binds the previously cached owner before a new login. Old
    // unscoped encrypted metadata is moved once, never assigned to a new account.
    if (migrateLegacy) prefs.all.filterKeys { it.startsWith("e2ee_") }.forEach { (key, value) ->
      if (value is String) edit.putString(prefix + key.removePrefix("e2ee"), value).remove(key)
    }
    edit.putString("active_e2ee_owner", ownerId).commit()
  }

  private fun ownerPrefix(ownerId: String?): String? = ownerId?.let {
    "owner_${it.replace(Regex("[^A-Za-z0-9-]"), "_")}_e2ee"
  }
  fun ownerId(): String? = prefs.getString("active_e2ee_owner", null)

  private fun getValue(prefix: String): String? {
    val cipherText = prefs.getString("${prefix}_cipher", null) ?: return null
    val iv = prefs.getString("${prefix}_iv", null) ?: return null
    return runCatching {
      val cipher = Cipher.getInstance(TRANSFORMATION)
      cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, android.util.Base64.decode(iv, 0)))
      String(cipher.doFinal(android.util.Base64.decode(cipherText, 0)))
    }.getOrNull()
  }

  private fun setValue(prefix: String, value: String) {
    val encrypted = encryptValue(value)
    prefs.edit().putString("${prefix}_cipher", encrypted.first).putString("${prefix}_iv", encrypted.second).apply()
  }

  private fun encryptValue(value: String): Pair<String, String> {
    val cipher = Cipher.getInstance(TRANSFORMATION)
    cipher.init(Cipher.ENCRYPT_MODE, key())
    val encrypted = cipher.doFinal(value.toByteArray())
    return android.util.Base64.encodeToString(encrypted, 0) to android.util.Base64.encodeToString(cipher.iv, 0)
  }

  fun clear() = synchronized(sessionLock) {
    prefs.edit().remove("token_cipher").remove("token_iv")
      .remove("refresh_token_cipher").remove("refresh_token_iv")
      .remove("active_e2ee_owner").commit()
  }

  private fun key(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey?.let { return it }
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    generator.init(
      KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
        .setRandomizedEncryptionRequired(true)
        .build()
    )
    return generator.generateKey()
  }

  private companion object {
    val sessionLock = Any()
    const val KEY_ALIAS = "lurg_notes_session_key"
    const val TRANSFORMATION = "AES/GCM/NoPadding"
  }
}
