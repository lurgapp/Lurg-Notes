package pro.lurg.notes.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class EncryptedNoteEnvelope(val nonce: String, val ciphertext: String)

/** Device-bound AES key. The cloud receives only an authenticated ciphertext envelope. */
class NotesCrypto(context: Context) {
  private val alias = "lurg_notes_document_key_v1"
  fun encrypt(ownerId: String, noteId: String, plainText: String): EncryptedNoteEnvelope {
    val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
    cipher.updateAAD("$ownerId:$noteId".toByteArray())
    return EncryptedNoteEnvelope(Base64.encodeToString(cipher.iv, Base64.NO_WRAP), Base64.encodeToString(cipher.doFinal(plainText.toByteArray()), Base64.NO_WRAP))
  }
  fun decrypt(ownerId: String, noteId: String, envelope: EncryptedNoteEnvelope): String {
    val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(envelope.nonce, Base64.NO_WRAP))) }
    cipher.updateAAD("$ownerId:$noteId".toByteArray())
    return String(cipher.doFinal(Base64.decode(envelope.ciphertext, Base64.NO_WRAP)))
  }
  private fun key(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.secretKey?.let { return it }
    return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
      init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
        .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build())
    }.generateKey()
  }
  private companion object { const val TRANSFORMATION = "AES/GCM/NoPadding" }
}
