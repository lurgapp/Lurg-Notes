package pro.lurg.notes.update

import java.io.File
import java.security.MessageDigest
import org.junit.Assert.*
import org.junit.Assume
import org.junit.Test

class NativeApkIntegrityTest {
    private val signer = "053fe44fabe00e67309a277918606968f6225cf1c5a1eac31448f8c620b373c0"
    private fun config(pin: String = signer) = NativeUpdateConfig("notes", "pro.lurg.notes", "https://notes.lurg.pro/update.json", "notes.lurg.pro", "/download/", "lurg-notes", pin, 20, "0.5.2", minSdk = 23)
    private fun release(file: File, pin: String = signer): ApkRelease {
        val hash = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
        return ApkRelease("android", "pro.lurg.notes", 21, "0.5.3", "https://notes.lurg.pro/download/lurg-notes-21-$hash.apk", hash, file.length(), pin)
    }
    @Test fun corruptNonApkFailsEvenWithMatchingHashAndSize() {
        val file = File.createTempFile("unsigned-update", ".apk").apply { writeText("Synthetic unsigned APK bytes") }
        try { verifyApkIntegrity(file, release(file), config()); fail("Unsigned bytes accepted") } catch (_: UpdateSecurityException) { } finally { file.delete() }
    }
    @Test fun approvedProductionApkPassesCryptographicVerificationAndTamperingFails() {
        val fixture = File(System.getProperty("lurg.fixture.apk", ""))
        Assume.assumeTrue("Signed APK fixture provided explicitly by release verification", fixture.isFile)
        val metadata = release(fixture)
        verifyApkIntegrity(fixture, metadata, config())
        val tampered = File.createTempFile("tampered-update", ".apk")
        try {
            tampered.writeBytes(fixture.readBytes().also { it[it.size / 2] = (it[it.size / 2].toInt() xor 1).toByte() })
            try { verifyApkIntegrity(tampered, metadata, config()); fail("Changed APK accepted") } catch (_: UpdateSecurityException) { }
            // Recomputing a new digest still cannot bypass the actual APK signature.
            try { verifyApkIntegrity(tampered, release(tampered), config()); fail("Invalid signature accepted") } catch (_: UpdateSecurityException) { }
            try { verifyApkIntegrity(fixture, release(fixture, "b".repeat(64)), config("b".repeat(64))); fail("Foreign signer accepted") } catch (_: UpdateSecurityException) { }
        } finally { tampered.delete() }
    }
}
