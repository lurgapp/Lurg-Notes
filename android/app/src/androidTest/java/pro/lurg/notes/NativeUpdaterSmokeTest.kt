package pro.lurg.notes

import android.content.Context
import androidx.core.content.FileProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.encodeToString
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Assume
import org.junit.Test
import org.junit.runner.RunWith
import pro.lurg.notes.update.*
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Run only on our isolated emulator; fixture and all request/response data are synthetic. */
@RunWith(AndroidJUnit4::class)
class NativeUpdaterSmokeTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private val pin = "053fe44fabe00e67309a277918606968f6225cf1c5a1eac31448f8c620b373c0"
    private fun config(id: String) = NativeUpdateConfig(id, "pro.lurg.notes", "https://notes.lurg.pro/update.json", "notes.lurg.pro", "/download/", "lurg-notes", pin, 21, "0.5.3", minSdk = 23)
    private val future get() = ApkRelease("android", "pro.lurg.notes", 22, "0.5.4", "https://notes.lurg.pro/download/lurg-notes-22-${"a".repeat(64)}.apk", "a".repeat(64), 2_000_000, pin)

    @Test fun pushTapIsCoalescedAndNoRetainsUpdateAcrossRestart(): Unit = runBlocking {
        val cfg = config("notes_updater_smoke")
        val prefs = context.getSharedPreferences("${cfg.appId}_native_apk_update", Context.MODE_PRIVATE)
        prefs.edit().clear().commit()
        val started = CountDownLatch(1); val release = CountDownLatch(1)
        val requests = AtomicInteger()
        val http = OkHttpClient.Builder().addInterceptor { chain ->
            assertEquals(cfg.manifestUrl, chain.request().url.toString())
            assertNull(chain.request().header("Authorization"))
            requests.incrementAndGet(); started.countDown()
            check(release.await(10, TimeUnit.SECONDS))
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .body(NativeUpdatePolicy.json.encodeToString(future).toResponseBody("application/json".toMediaType())).build()
        }.build()
        var updater: NativeApkUpdater? = NativeApkUpdater(context, cfg, http)
        try {
            updater!!.checkForUpdate()
            assertTrue(started.await(10, TimeUnit.SECONDS))
            repeat(20) { updater!!.onPushTap(22) }
            release.countDown()
            withTimeout(10_000) { while (!updater!!.state.value.prompt) delay(50) }
            assertEquals(1, requests.get())
            assertEquals(22, updater!!.state.value.release!!.versionCode)
            updater!!.defer()
            assertFalse(updater!!.state.value.prompt)
            assertNotNull(updater!!.state.value.release)
            assertFalse(updater!!.installationPermissionDeferred())
            assertFalse(updater!!.state.value.waitingPermission)
            assertFalse(updater!!.state.value.installRequested)
            updater!!.close()
            updater = NativeApkUpdater(context, cfg, http)
            assertEquals(22, updater!!.state.value.release!!.versionCode)
            assertFalse(updater!!.state.value.prompt)
            assertFalse(updater!!.state.value.waitingPermission)
            updater!!.onPushTap(21) // obsolete notification must not fetch or prompt
            delay(100)
            assertEquals(1, requests.get())
            assertFalse(updater!!.state.value.prompt)
        } finally { release.countDown(); updater?.close(); prefs.edit().clear().commit() }
    }

    @Test fun signedApkMetadataAndFileProviderAreVerifiedBeforeInstallation() {
        val fixture = File(context.filesDir, "notes-updater-fixture.apk")
        Assume.assumeTrue("Signed release fixture explicitly provisioned into isolated emulator", fixture.isFile)
        val info = context.packageManager.getPackageArchiveInfo(fixture.absolutePath, 0)!!
        val code = if (android.os.Build.VERSION.SDK_INT >= 28) info.longVersionCode.toInt() else @Suppress("DEPRECATION") info.versionCode
        val cfg = config("notes_apk_smoke").copy(installedCode = code - 1)
        val hash = MessageDigest.getInstance("SHA-256").digest(fixture.readBytes()).joinToString("") { "%02x".format(it) }
        val metadata = ApkRelease("android", cfg.packageName, code, info.versionName!!, "https://notes.lurg.pro/download/lurg-notes-$code-$hash.apk", hash, fixture.length(), pin)
        val directory = File(context.cacheDir, "updates").apply { mkdirs() }
        val apk = File(directory, metadata.filename)
        val partial = File(directory, metadata.filename + ".part")
        fixture.copyTo(apk, overwrite = true)
        try {
            fixture.copyTo(partial, overwrite = true)
            // The worker verifies its staging file before the atomic final rename.
            verifyDownloadedApk(context, partial, metadata, cfg)
            verifyDownloadedApk(context, apk, metadata, cfg)
            val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", apk)
            assertEquals("content", uri.scheme)
            context.contentResolver.openInputStream(uri)!!.use { assertEquals(apk.length(), it.readBytes().size.toLong()) }
            try {
                FileProvider.getUriForFile(context, "${context.packageName}.files", fixture)
                fail("FileProvider exposed an unrelated app-private file")
            } catch (_: IllegalArgumentException) { }
            val wrong = metadata.copy(versionCode = code + 1, apkUrl = "https://notes.lurg.pro/download/lurg-notes-${code + 1}-$hash.apk")
            try { verifyDownloadedApk(context, apk, wrong, cfg); fail("Wrong package version accepted") } catch (_: UpdateSecurityException) { }
            apk.appendBytes(byteArrayOf(1))
            try { verifyDownloadedApk(context, apk, metadata, cfg); fail("Changed APK accepted") } catch (_: UpdateSecurityException) { }
        } finally { apk.delete(); partial.delete() }
    }

    @Test fun noThenImmediateNewYesMustNotLoseNewConsent(): Unit = runBlocking {
        val cfg = config("notes_reconsent_smoke")
        val prefs = context.getSharedPreferences("${cfg.appId}_native_apk_update", Context.MODE_PRIVATE)
        prefs.edit().clear().putString("available", NativeUpdatePolicy.json.encodeToString(future)).commit()
        val updater = NativeApkUpdater(context, cfg)
        try {
            repeat(12) {
                updater.defer()
                updater.requestInstallation()
                delay(50)
                // An asynchronous cleanup belonging to No must not clear this new Yes.
                assertTrue(updater.state.value.installRequested)
                assertTrue(prefs.getBoolean("install_requested", false))
                assertEquals(-1, prefs.getInt(NativeApkInstaller.SESSION, -1))
            }
        } finally { updater.close(); prefs.edit().clear().commit() }
    }

    @Test fun yesOpensOnlyVerifiedReadyFileAndPermissionDeferralSurvivesRestart(): Unit = runBlocking {
        val fixture = File(context.filesDir, "notes-updater-fixture.apk")
        Assume.assumeTrue(fixture.isFile)
        val info = context.packageManager.getPackageArchiveInfo(fixture.absolutePath, 0)!!
        val code = if (android.os.Build.VERSION.SDK_INT >= 28) info.longVersionCode.toInt() else @Suppress("DEPRECATION") info.versionCode
        val cfg = config("notes_yes_smoke").copy(installedCode = code - 1)
        val hash = MessageDigest.getInstance("SHA-256").digest(fixture.readBytes()).joinToString("") { "%02x".format(it) }
        val metadata = ApkRelease("android", cfg.packageName, code, info.versionName!!, "https://notes.lurg.pro/download/lurg-notes-$code-$hash.apk", hash, fixture.length(), pin)
        val directory = File(context.cacheDir, "updates").apply { mkdirs() }
        val apk = File(directory, metadata.filename); fixture.copyTo(apk, overwrite = true)
        val prefs = context.getSharedPreferences("${cfg.appId}_native_apk_update", Context.MODE_PRIVATE)
        prefs.edit().clear().putString("available", NativeUpdatePolicy.json.encodeToString(metadata))
            .putString("approved", NativeUpdatePolicy.json.encodeToString(metadata)).putString("ready_file", apk.name).commit()
        var updater: NativeApkUpdater? = NativeApkUpdater(context, cfg)
        try {
            updater!!.approve()
            withTimeout(10000) { while (!updater!!.state.value.installRequested) delay(50) }
            assertEquals(apk.absolutePath, updater!!.installationFile()!!.absolutePath)
            updater!!.installationPermissionDeferred()
            updater!!.close()
            updater = NativeApkUpdater(context, cfg)
            assertTrue(updater!!.state.value.waitingPermission)
            assertFalse(updater!!.state.value.installRequested)
            assertEquals(apk.name, updater!!.state.value.readyFile)
            // The manual settings action uses the same verified artifact.
            updater!!.requestInstallation()
            assertNotNull(updater!!.installationFile())
            // Bytes changed after download may never reach the installer.
            apk.appendBytes(byteArrayOf(1))
            assertNull(updater!!.installationFile())
            assertFalse(updater!!.state.value.installRequested)
        } finally { updater?.close(); apk.delete(); prefs.edit().clear().commit() }
    }
}
