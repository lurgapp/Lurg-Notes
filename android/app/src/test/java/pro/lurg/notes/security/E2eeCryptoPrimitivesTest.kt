package pro.lurg.notes.security

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test
import java.security.KeyPairGenerator
import java.security.SecureRandom
import java.security.spec.MGF1ParameterSpec
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.OAEPParameterSpec
import javax.crypto.spec.PSource
import javax.crypto.spec.SecretKeySpec
import kotlin.experimental.xor

/** Platform-primitive contracts used by E2eeV2Crypto; no Android Keystore mock. */
class E2eeCryptoPrimitivesTest {
  private val random = SecureRandom()
  private val aad = "lurg-notes:v2:owner:note".toByteArray()

  @Test fun encryptDecryptAndFreshNonce() {
    val key = ByteArray(32).also(random::nextBytes)
    val first = encrypt(key, "E2EE TEST A".toByteArray())
    val second = encrypt(key, "E2EE TEST A".toByteArray())
    assertNotEquals(first.first.joinToString(), second.first.joinToString())
    assertArrayEquals("E2EE TEST A".toByteArray(), decrypt(key, first.first, first.second))
  }

  @Test(expected = AEADBadTagException::class) fun wrongKeyRejected() {
    val first = encrypt(ByteArray(32).also(random::nextBytes), "note".toByteArray())
    decrypt(ByteArray(32).also(random::nextBytes), first.first, first.second)
  }

  @Test(expected = AEADBadTagException::class) fun corruptedCiphertextOrAuthenticationTagRejected() {
    val key = ByteArray(32).also(random::nextBytes)
    val encrypted = encrypt(key, "note".toByteArray())
    encrypted.second[encrypted.second.lastIndex] = (encrypted.second.last() xor 1)
    decrypt(key, encrypted.first, encrypted.second)
  }

  @Test fun rsaOaepWrappingIsRecipientAndContextBound() {
    val pairA = rsa(); val pairB = rsa(); val key = ByteArray(32).also(random::nextBytes)
    val label = "lurg-notes:v2:umk:owner:device-b".toByteArray()
    val wrapped = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding").run { init(Cipher.ENCRYPT_MODE, pairB.public, oaep(label)); doFinal(key) }
    val clear = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding").run { init(Cipher.DECRYPT_MODE, pairB.private, oaep(label)); doFinal(wrapped) }
    assertArrayEquals(key, clear)
    runCatching { Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding").run { init(Cipher.DECRYPT_MODE, pairA.private, oaep(label)); doFinal(wrapped) } }.onSuccess { throw AssertionError("wrong device decrypted UMK") }
    runCatching { Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding").run { init(Cipher.DECRYPT_MODE, pairB.private, oaep("wrong".toByteArray())); doFinal(wrapped) } }.onSuccess { throw AssertionError("wrong OAEP context decrypted UMK") }
  }

  @Test fun hmacTokenDoesNotExposePlaintextDigest() {
    val umk = ByteArray(32).also(random::nextBytes)
    val token = Mac.getInstance("HmacSHA256").run { init(SecretKeySpec(umk, "HmacSHA256")); doFinal("same".toByteArray()) }
    assertNotEquals(java.security.MessageDigest.getInstance("SHA-256").digest("same".toByteArray()).joinToString(), token.joinToString())
  }

  private fun encrypt(key: ByteArray, plain: ByteArray): Pair<ByteArray, ByteArray> {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES")); updateAAD(aad) }
    return cipher.iv to cipher.doFinal(plain)
  }
  private fun decrypt(key: ByteArray, nonce: ByteArray, encrypted: ByteArray): ByteArray = Cipher.getInstance("AES/GCM/NoPadding").run {
    init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce)); updateAAD(aad); doFinal(encrypted)
  }
  private fun rsa() = KeyPairGenerator.getInstance("RSA").apply { initialize(3072) }.generateKeyPair()
  private fun oaep(label: ByteArray) = OAEPParameterSpec("SHA-256", "MGF1", MGF1ParameterSpec.SHA256, PSource.PSpecified(label))
}
