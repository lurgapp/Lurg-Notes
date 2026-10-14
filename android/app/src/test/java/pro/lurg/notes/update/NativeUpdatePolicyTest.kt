package pro.lurg.notes.update

import org.junit.Assert.*
import org.junit.Test
import kotlinx.serialization.encodeToString

class NativeUpdatePolicyTest {
    private val hash = "a".repeat(64)
    private val signer = "053fe44fabe00e67309a277918606968f6225cf1c5a1eac31448f8c620b373c0"
    private val config = NativeUpdateConfig("notes", "pro.lurg.notes", "https://notes.lurg.pro/update.json", "notes.lurg.pro", "/download/", "lurg-notes", signer, 21, "0.5.3", minSdk = 23)
    private val release get() = ApkRelease("android", config.packageName, 22, "0.5.4", "https://notes.lurg.pro/download/lurg-notes-22-$hash.apk", hash, 2_000_000, signer)
    private fun rejected(value: ApkRelease) {
        try { NativeUpdatePolicy.validate(value, config); fail("Unsafe update accepted") } catch (_: UpdateSecurityException) { }
    }
    @Test fun immutableReleaseAcceptsOnlyMatchingIdentityAndDigestPath() {
        assertEquals(release, NativeUpdatePolicy.parse(NativeUpdatePolicy.json.encodeToString(release), config))
    }
    @Test fun wrongProductIsRejected() {
        rejected(release.copy(packageName = "pro.lurg.mobile")); rejected(release.copy(platform = "ios"))
    }
    @Test fun signerCannotBeChangedByManifest() { rejected(release.copy(signingCertificateSha256 = "b".repeat(64))) }
    @Test fun malformedDigestAndSizeAreRejected() {
        rejected(release.copy(sha256 = "missing")); rejected(release.copy(sizeBytes = 0)); rejected(release.copy(sizeBytes = NativeUpdatePolicy.MAX_APK_BYTES + 1))
    }
    @Test fun plaintextForeignHostCredentialsAndRedirectTargetsAreRejected() {
        listOf("http://notes.lurg.pro/download/lurg-notes-22-$hash.apk", "https://evil.example/app/lurg-notes-22-$hash.apk", "https://notes.lurg.pro.evil.example/app/lurg-notes-22-$hash.apk", "https://user@notes.lurg.pro/app/lurg-notes-22-$hash.apk", "https://notes.lurg.pro:8443/app/lurg-notes-22-$hash.apk")
            .forEach { rejected(release.copy(apkUrl = it)) }
    }
    @Test fun versionHashPathQueryFragmentAndTraversalCannotBeSubstituted() {
        listOf("https://notes.lurg.pro/download/lurg-notes-23-$hash.apk", "https://notes.lurg.pro/download/lurg-notes-22-${"b".repeat(64)}.apk", "https://notes.lurg.pro/download/latest.apk", "https://notes.lurg.pro/download/lurg-notes-22-$hash.apk?q=x", "https://notes.lurg.pro/download/lurg-notes-22-$hash.apk#x", "https://notes.lurg.pro/download/../app/lurg-notes-22-$hash.apk")
            .forEach { rejected(release.copy(apkUrl = it)) }
    }
    @Test fun missingSafetyMetadataAndOversizedManifestAreRejected() {
        try { NativeUpdatePolicy.parse("{\"version_code\":11}", config); fail("Incomplete manifest accepted") } catch (_: Exception) { }
        try { NativeUpdatePolicy.parse(" ".repeat(NativeUpdatePolicy.MAX_MANIFEST_BYTES + 1), config); fail("Oversized manifest accepted") } catch (_: UpdateSecurityException) { }
    }
    @Test fun descriptionsAndVersionFieldsAreBounded() {
        rejected(release.copy(versionCode = 0)); rejected(release.copy(versionName = "")); rejected(release.copy(changelog = List(31) { "item" })); rejected(release.copy(title = "x".repeat(201)))
    }
    @Test fun largeValidDescriptionDoesNotOverflowWorkManagerPayload() {
        val described = release.copy(title = "t".repeat(200), changelog = List(30) { "x".repeat(1000) })
        NativeUpdatePolicy.validate(described, config)
        assertTrue(NativeUpdatePolicy.json.encodeToString(described).toByteArray().size > 10_240)
        val payload = NativeUpdatePolicy.workerPayload(described)
        assertTrue(payload.toByteArray().size < 1024)
        val parsed = NativeUpdatePolicy.parse(payload, config)
        assertEquals(described.copy(title = "", changelog = emptyList()), parsed)
    }
    @Test fun updatePushMustBelongToThisProduct() {
        val payload = mapOf("type" to "app_update", "app_id" to "notes", "package_name" to "pro.lurg.notes")
        assertTrue(NativeUpdatePolicy.isUpdatePush(payload, config))
        assertFalse(NativeUpdatePolicy.isUpdatePush(payload + ("app_id" to "lurg"), config))
        assertFalse(NativeUpdatePolicy.isUpdatePush(payload + ("package_name" to "pro.lurg.mobile"), config))
        assertFalse(NativeUpdatePolicy.isUpdatePush(payload - "app_id", config))
        assertFalse(NativeUpdatePolicy.isUpdatePush(payload + ("type" to "other"), config))
    }
}
