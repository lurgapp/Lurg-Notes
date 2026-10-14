package pro.lurg.notes.update

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.encodeToString
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean

/** A self-update session is created only after the app's verified-APK Yes consent. */
class NativeApkInstaller(context: Context, private val updater: NativeApkUpdater) {
    private val context = context.applicationContext
    private val prefs = this.context.getSharedPreferences(updater.config.appId + "_native_apk_update", Context.MODE_PRIVATE)
    private val platform = this.context.packageManager.packageInstaller
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutex = Mutex()
    private val queued = AtomicBoolean(false)
    private val foreground = AtomicBoolean(false)
    private var recommittedSession: Int? = null

    init { recover() }

    fun setForeground(active: Boolean) {
        foreground.set(active)
        if (!active && prefs.getString(PHASE, null) == PENDING) notifyConfirmation()
        if (active) recover()
    }

    fun requestApprovedInstall(requireUserAction: Boolean = false) {
        if (!queued.compareAndSet(false, true)) return
        scope.launch {
            try { mutex.withLock { stageApproved(requireUserAction) } }
            finally { queued.set(false) }
        }
    }

    private suspend fun stageApproved(requireUserAction: Boolean) {
        var created: Int? = null
        try {
            if (prefs.getInt(SESSION, -1) >= 0) {
                recoverLocked()
                if (prefs.getInt(SESSION, -1) >= 0 || !prefs.getBoolean("install_requested", false)) return
            }
            val release = updater.state.value.release ?: return
            if (release.packageName != context.packageName || !updater.state.value.installRequested) return
            val file = updater.installationFile() ?: return
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                setAppPackageName(release.packageName)
                setSize(release.sizeBytes)
                if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(if (requireUserAction)
                    PackageInstaller.SessionParams.USER_ACTION_REQUIRED else PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                if (Build.VERSION.SDK_INT >= 33) setPackageSource(PackageInstaller.PACKAGE_SOURCE_DOWNLOADED_FILE)
            }
            val nonce = UUID.randomUUID().toString()
            val id = updater.withInstallerLock {
                val current = updater.state.value
                if (!prefs.getBoolean("install_requested", false) || current.release != release || current.readyFile != file.name) null
                else platform.createSession(params).also { sessionId ->
                    created = sessionId
                    prefs.edit().putInt(SESSION, sessionId).putString(NONCE, nonce)
                        .putString(RELEASE, NativeUpdatePolicy.json.encodeToString(release))
                        .putString(PHASE, WRITING).putBoolean(AUTHORIZED, true)
                        .putBoolean("install_requested", false).putBoolean("waiting_permission", false).commit()
                    publish(true, false, "Подготавливаем установку обновления…")
                }
            } ?: return
            platform.openSession(id).use { session ->
                session.openWrite("base.apk", 0, release.sizeBytes).use { out ->
                    file.inputStream().use { input ->
                        val buffer = ByteArray(64 * 1024)
                        var written = 0L
                        while (true) {
                            if (!prefs.getBoolean(AUTHORIZED, false)) throw UpdateSecurityException("Установка отменена")
                            val count = input.read(buffer)
                            if (count < 0) break
                            written += count
                            if (written > release.sizeBytes) throw UpdateSecurityException("APK изменился перед установкой")
                            out.write(buffer, 0, count)
                        }
                        if (written != release.sizeBytes) throw UpdateSecurityException("APK изменился перед установкой")
                    }
                    session.fsync(out)
                }
                // Hash the actual staged bytes, not just the source cache file.
                val digest = MessageDigest.getInstance("SHA-256")
                session.openRead("base.apk").use { input ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) { val count = input.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) }
                }
                if (digest.digest().joinToString("") { "%02x".format(it) } != release.sha256)
                    throw UpdateSecurityException("APK изменился перед установкой")
                updater.withInstallerLock {
                    if (!matches(id, nonce) || !prefs.getBoolean(AUTHORIZED, false)) throw UpdateSecurityException("Установка отменена")
                    prefs.edit().putString(PHASE, COMMITTED).commit()
                    publish(true, false, "Android устанавливает обновление…")
                    session.commit(statusCallback(id, nonce).intentSender)
                }
            }
        } catch (_: Exception) {
            created?.let { runCatching { platform.abandonSession(it) } }
            fail("Установка не завершена. Повторите из настроек.", created)
        }
    }

    private fun statusCallback(id: Int, nonce: String): PendingIntent {
        val callback = Intent(context, NativeInstallStatusReceiver::class.java)
            .setAction(context.packageName + STATUS_ACTION).setPackage(context.packageName)
            .setData(Uri.parse("lurg-update://install/" + id + "/" + nonce))
            .putExtra(NONCE, nonce).putExtra(PackageInstaller.EXTRA_SESSION_ID, id)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        return PendingIntent.getBroadcast(context, id, callback, flags)
    }

    /** Called only by the explicit, non-exported status receiver. */
    fun onStatus(intent: Intent) = updater.withInstallerLock {
        val id = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1)
        val nonce = intent.getStringExtra(NONCE)
        if (intent.action != context.packageName + STATUS_ACTION || nonce == null || !matches(id, nonce)) return@withInstallerLock
        if (!prefs.getBoolean(AUTHORIZED, false)) return@withInstallerLock
        val release = readRelease() ?: return@withInstallerLock
        val packageName = intent.getStringExtra(PackageInstaller.EXTRA_PACKAGE_NAME)
        if (packageName != null && packageName != release.packageName) return@withInstallerLock
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, Int.MIN_VALUE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val supplied = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                val confirmation = supplied?.let { trustedConfirmation(it) }
                if (confirmation == null) { fail("Не удалось открыть подтверждение Android.", id); return@withInstallerLock }
                prefs.edit().putString(CONFIRMATION, confirmation.toUri(Intent.URI_INTENT_SCHEME)).putString(PHASE, PENDING).commit()
                publish(true, true, "Подтвердите установку в Android")
                if (!foreground.get()) notifyConfirmation()
            }
            PackageInstaller.STATUS_SUCCESS -> {
                if (installedCode() >= release.versionCode) complete(release)
                else fail("Android не подтвердил обновление версии. Повторите из настроек.", id)
            }
            Int.MIN_VALUE -> Unit
            else -> fail("Установка отменена или не завершена. Повторите из настроек.", id)
        }
    }

    /** Foreground Activity consumes this once; background receiver never launches an Activity. */
    fun takeConfirmationIntent(): Intent? = updater.withInstallerLock {
        if (!prefs.getBoolean(AUTHORIZED, false) || prefs.getString(PHASE, null) != PENDING) return@withInstallerLock null
        val intent = savedConfirmation() ?: return@withInstallerLock null
        prefs.edit().putString(PHASE, USER_ACTION_LAUNCHED).commit()
        publish(true, true, "Подтвердите установку в системном окне Android")
        cancelNotification()
        intent
    }

    fun continueConfirmation() = updater.withInstallerLock {
        if (!prefs.getBoolean(AUTHORIZED, false) || savedConfirmation() == null) return@withInstallerLock
        prefs.edit().putString(PHASE, PENDING).commit()
        publish(true, true, "Подтвердите установку в Android")
    }

    fun userActionActivityReturned() = recover()

    fun cancelAfterNo(sessionId: Int) {
        if (sessionId < 0) return
        scope.launch { mutex.withLock {
            updater.withInstallerLock {
                if (prefs.getBoolean(AUTHORIZED, false) || prefs.getInt(SESSION, -1) != sessionId) return@withInstallerLock
                val id = prefs.getInt(SESSION, -1)
                if (id >= 0) runCatching { platform.abandonSession(id) }
                clearTracking(clearRequest = false)
                publish(false, false, "Обновление можно установить из настроек")
            }
        } }
    }

    fun recover() { scope.launch { mutex.withLock { recoverLocked() } } }

    private fun recoverLocked() = updater.withInstallerLock {
        val id = prefs.getInt(SESSION, -1)
        if (id < 0) return@withInstallerLock
        val release = readRelease()
        if (release != null && installedCode() >= release.versionCode) { complete(release); return@withInstallerLock }
        if (release == null || !prefs.getBoolean(AUTHORIZED, false)) {
            runCatching { platform.abandonSession(id) }; clearTracking(clearRequest = false); publish(false, false, "Обновление можно установить из настроек"); return@withInstallerLock
        }
        val info = platform.getSessionInfo(id)
        if (info == null || info.installerPackageName != context.packageName || info.appPackageName != context.packageName) {
            fail("Установка не завершена. Повторите из настроек.", id); return@withInstallerLock
        }
        val phase = prefs.getString(PHASE, null)
        val sealed = if (Build.VERSION.SDK_INT >= 26) info.isSealed else phase != WRITING
        if (!sealed) {
            runCatching { platform.abandonSession(id) }
            clearTracking()
            updater.restoreApprovedInstallation(release)
        } else {
            val pending = phase == PENDING || phase == USER_ACTION_LAUNCHED
            publish(true, pending, if (pending) "Подтвердите установку в Android" else "Android устанавливает обновление…")
            // Reuse this sealed session after an interruption; never stage a second copy.
            if (!pending && recommittedSession != id && !info.isActive) {
                recommittedSession = id
                val nonce = prefs.getString(NONCE, null) ?: return@withInstallerLock
                runCatching { platform.openSession(id).use { it.commit(statusCallback(id, nonce).intentSender) } }
            }
        }
    }

    private fun trustedConfirmation(original: Intent): Intent? = runCatching {
        val resolved = context.packageManager.resolveActivity(original, 0)?.activityInfo ?: return@runCatching null
        if (resolved.applicationInfo.flags and (ApplicationInfo.FLAG_SYSTEM or ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) == 0) return@runCatching null
        Intent(original).setComponent(ComponentName(resolved.packageName, resolved.name)).apply {
            flags = flags and Intent.FLAG_ACTIVITY_NEW_TASK.inv()
        }
    }.getOrNull()

    private fun savedConfirmation(): Intent? = runCatching {
        prefs.getString(CONFIRMATION, null)?.let { trustedConfirmation(Intent.parseUri(it, Intent.URI_INTENT_SCHEME)) }
    }.getOrNull()

    private fun matches(id: Int, nonce: String) = id >= 0 && prefs.getInt(SESSION, -1) == id && prefs.getString(NONCE, null) == nonce
    private fun readRelease(): ApkRelease? = runCatching { prefs.getString(RELEASE, null)?.let { NativeUpdatePolicy.parse(it, updater.config) } }.getOrNull()
    private fun installedCode(): Long = runCatching {
        @Suppress("DEPRECATION") val info = context.packageManager.getPackageInfo(context.packageName, 0)
        if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
    }.getOrDefault(-1)

    private fun complete(release: ApkRelease) {
        clearTracking()
        prefs.edit().putInt("last_installed_version", release.versionCode).putString("last_installed_sha256", release.sha256)
            .remove("available").remove("approved").remove("ready_file").remove("work_id").commit()
        updater.installerCompleted()
        cancelNotification()
    }

    private fun fail(message: String, expectedId: Int? = null) = updater.withInstallerLock {
        if (expectedId != null && prefs.getInt(SESSION, -1) != expectedId) return@withInstallerLock
        val id = prefs.getInt(SESSION, -1)
        if (id >= 0) runCatching { platform.abandonSession(id) }
        clearTracking()
        publish(false, false, "Установить обновление из настроек", message)
        cancelNotification()
    }

    private fun clearTracking(clearRequest: Boolean = true) {
        val edit = prefs.edit().remove(SESSION).remove(NONCE).remove(RELEASE).remove(PHASE).remove(CONFIRMATION).putBoolean(AUTHORIZED, false)
        if (clearRequest) edit.putBoolean("install_requested", false).putBoolean("waiting_permission", false)
        edit.commit()
    }

    private fun publish(installing: Boolean, pending: Boolean, status: String, error: String? = null) =
        updater.installerStateChanged(installing, pending, status, error)

    private fun notifyConfirmation() {
        if (!prefs.getBoolean(AUTHORIZED, false)) return
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) return
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel = updater.config.updateChannelId
        if (Build.VERSION.SDK_INT >= 26) manager.createNotificationChannel(NotificationChannel(channel, "Обновления", NotificationManager.IMPORTANCE_DEFAULT))
        val intent = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: return
        val pending = PendingIntent.getActivity(context, 7109, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val icon = context.applicationInfo.icon
        manager.notify(7109, NotificationCompat.Builder(context, channel).setSmallIcon(icon)
            .setContentTitle("Подтвердить обновление").setContentText("Откройте приложение и подтвердите установку в Android.")
            .setContentIntent(pending).setAutoCancel(true).build())
    }

    private fun cancelNotification() = (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(7109)
    fun close() = scope.cancel()

    companion object {
        const val SESSION = "installer_session_id"
        const val NONCE = "installer_nonce"
        const val RELEASE = "installer_release"
        const val PHASE = "installer_phase"
        const val AUTHORIZED = "installer_authorized"
        const val CONFIRMATION = "installer_confirmation"
        const val STATUS_ACTION = ".APK_INSTALL_STATUS"
        private const val WRITING = "writing"
        private const val COMMITTED = "committed"
        private const val PENDING = "pending_user_action"
        private const val USER_ACTION_LAUNCHED = "user_action_launched"
    }
}

class NativeInstallStatusReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val host = context.applicationContext as? NativeUpdateHost ?: return
        host.nativeApkUpdater.installer.onStatus(intent)
    }
}
