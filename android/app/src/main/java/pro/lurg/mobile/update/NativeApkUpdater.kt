package pro.lurg.notes.update

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.workDataOf
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.cancel
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

data class NativeUpdateState(
    val checking: Boolean = false,
    val release: ApkRelease? = null,
    val prompt: Boolean = false,
    val downloading: Boolean = false,
    val progress: Int = 0,
    val readyFile: String? = null,
    val installRequested: Boolean = false,
    val waitingPermission: Boolean = false,
    val installing: Boolean = false,
    val pendingConfirmation: Boolean = false,
    val installerEvent: Int = 0,
    val error: String? = null,
    val status: String = "Проверить обновления",
)

/** No account tokens are needed or sent. Consent, download and installation survive app restarts. */
class NativeApkUpdater(context: Context, val config: NativeUpdateConfig, private val http: OkHttpClient = defaultHttp()) {
    private val context = context.applicationContext
    private val prefs = this.context.getSharedPreferences("${config.appId}_native_apk_update", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val checkLock = Mutex()
    private val checkRunning = AtomicBoolean(false)
    private val promptPending = AtomicBoolean(false)
    private val approveLock = Mutex()
    private val consentLock = Any()
    private val manager = WorkManager.getInstance(this.context)
    private val workName = "${config.appId}_verified_apk_download"
    private val restored = readRelease("available")
    private val mutableState = MutableStateFlow(NativeUpdateState(
        release = restored,
        readyFile = prefs.getString("ready_file", null).takeIf { restored != null },
        installRequested = restored != null && prefs.getBoolean("install_requested", false),
        waitingPermission = restored != null && prefs.getBoolean("waiting_permission", false),
        installing = prefs.getInt(NativeApkInstaller.SESSION, -1) >= 0 && prefs.getBoolean(NativeApkInstaller.AUTHORIZED, false),
        status = if (restored != null) "Доступно обновление ${restored.versionName}" else "Проверить обновления",
    ))
    val state: StateFlow<NativeUpdateState> = mutableState
    val installer = NativeApkInstaller(this.context, this)

    init {
        if (restored == null) prefs.edit().remove("ready_file").remove("install_requested").remove("waiting_permission").remove("approved").remove("work_id").commit()
        // Consent is committed before the WorkManager transaction. Recover that
        // tiny crash window without retrying a completed/failed validation job.
        scope.launch {
            approveLock.withLock {
                if (prefs.getBoolean("install_requested", false) && prefs.getString("ready_file", null) == null) {
                    val work = manager.getWorkInfosForUniqueWork(workName).get()
                    val trackedId = prefs.getString("work_id", null)
                    if (prefs.getBoolean("install_requested", false) && work.none { !it.state.isFinished } && work.none { it.id.toString() == trackedId }) {
                        readRelease("approved")?.let { enqueueApprovedRelease(it) }
                    }
                }
            }
        }
        scope.launch {
            manager.getWorkInfosForUniqueWorkFlow(workName).collect { work ->
                val tracked = work.firstOrNull { it.id.toString() == prefs.getString("work_id", null) }
                val active = tracked?.takeIf { !it.state.isFinished } ?: work.firstOrNull { !it.state.isFinished }
                if (active != null) {
                    mutableState.update { it.copy(downloading = true, progress = active.progress.getInt(NativeUpdateWorker.PROGRESS, 0),
                        status = if (active.state == WorkInfo.State.RUNNING) "Скачиваем обновление…" else "Ожидаем сеть для загрузки…", error = null) }
                } else {
                    val last = tracked
                    when (last?.state) {
                        WorkInfo.State.SUCCEEDED -> {
                            synchronized(consentLock) {
                            if (last.id.toString() != prefs.getString("work_id", null)) return@synchronized
                            val approved = readRelease("approved")
                            val filename = last.outputData.getString(NativeUpdateWorker.FILE)
                            if (approved != null && filename == approved.filename && last.outputData.getInt(NativeUpdateWorker.VERSION, 0) == approved.versionCode) {
                                prefs.edit().putString("available", NativeUpdatePolicy.json.encodeToString(approved)).putString("ready_file", filename).commit()
                                mutableState.update { it.copy(release = approved, downloading = false, readyFile = filename, progress = 100,
                                    installRequested = prefs.getBoolean("install_requested", false), status = if (it.installing) it.status else "Обновление скачано и проверено", error = null) }
                            } else mutableState.update { it.copy(downloading = false) }
                            }
                        }
                        WorkInfo.State.FAILED, WorkInfo.State.CANCELLED -> {
                            synchronized(consentLock) {
                            if (last.id.toString() != prefs.getString("work_id", null)) return@synchronized
                            prefs.edit().putBoolean("install_requested", false).putBoolean("waiting_permission", false).commit()
                            mutableState.update { it.copy(downloading = false, installRequested = false, readyFile = null,
                                waitingPermission = false, error = last.outputData.getString(NativeUpdateWorker.ERROR) ?: "Загрузка не завершена. Повторите.", status = "Повторить загрузку обновления") }
                            }
                        }
                        else -> mutableState.update { it.copy(downloading = false) }
                    }
                }
            }
        }
    }

    private fun readRelease(key: String): ApkRelease? = runCatching {
        prefs.getString(key, null)?.let { NativeUpdatePolicy.parse(it, config) }?.takeIf { it.versionCode > config.installedCode }
    }.getOrNull()

    fun checkForUpdate(prompt: Boolean = false, notifiedVersion: Int? = null) {
        if (notifiedVersion != null && notifiedVersion <= config.installedCode) return
        if (prompt) promptPending.set(true)
        if (!checkRunning.compareAndSet(false, true)) return
        scope.launch {
            try {
            checkLock.withLock {
                mutableState.update { it.copy(checking = true, error = null) }
                try {
                    val release = http.newCall(Request.Builder().url(config.manifestUrl).header("Cache-Control", "no-cache").get().build()).execute().use { response ->
                        if (!response.isSuccessful) throw IllegalStateException("Не удалось проверить обновления")
                        val body = response.body ?: throw IllegalStateException("Пустой манифест обновления")
                        val bytes = body.byteStream().use { input ->
                            val output = ByteArrayOutputStream()
                            val buffer = ByteArray(4096)
                            while (output.size() <= NativeUpdatePolicy.MAX_MANIFEST_BYTES) {
                                val count = input.read(buffer, 0, minOf(buffer.size, NativeUpdatePolicy.MAX_MANIFEST_BYTES + 1 - output.size()))
                                if (count < 0) break
                                output.write(buffer, 0, count)
                            }
                            output.toByteArray()
                        }
                        if (bytes.size > NativeUpdatePolicy.MAX_MANIFEST_BYTES) throw UpdateSecurityException("Манифест обновления слишком большой")
                        NativeUpdatePolicy.parse(bytes.toString(Charsets.UTF_8), config)
                    }
                    val known = mutableState.value.release
                    if (known != null && (release.versionCode < known.versionCode || release.versionCode == known.versionCode && release.sha256 != known.sha256))
                        throw UpdateSecurityException("Манифест пытается заменить уже известную версию")
                    if (release.versionCode <= config.installedCode) {
                        promptPending.set(false)
                        mutableState.update { it.copy(checking = false, status = "Установлена актуальная версия", prompt = false) }
                    } else {
                        // An already approved download owns its immutable release until it finishes.
                        synchronized(consentLock) {
                        val current = mutableState.value
                        if (!current.downloading && !current.installRequested && !current.waitingPermission && !current.installing) {
                            val changed = current.release?.versionCode != release.versionCode
                            val edit = prefs.edit().putString("available", NativeUpdatePolicy.json.encodeToString(release))
                            if (changed) edit.remove("ready_file").remove("approved").remove("work_id")
                            edit.commit()
                            mutableState.update { it.copy(release = release, readyFile = if (changed) null else it.readyFile) }
                        }
                        val offerRequested = promptPending.getAndSet(false)
                        mutableState.update { it.copy(checking = false, prompt = (offerRequested || it.prompt) && !it.downloading && !it.installRequested && !it.installing,
                            status = if (it.downloading || it.readyFile != null) it.status else "Доступно обновление ${release.versionName}") }
                        }
                    }
                } catch (_: Exception) {
                    promptPending.set(false)
                    mutableState.update { it.copy(checking = false, error = "Не удалось проверить обновления. Повторите при подключении к сети.") }
                }
            }
            } finally {
                checkRunning.set(false)
                if (promptPending.get()) checkForUpdate(prompt = true)
            }
        }
    }

    fun onPushTap(version: Int? = null) = checkForUpdate(prompt = true, notifiedVersion = version)
    fun defer() {
        var cancelledSession = -1
        synchronized(consentLock) {
        cancelledSession = prefs.getInt(NativeApkInstaller.SESSION, -1)
        promptPending.set(false)
        prefs.edit().putInt("deferred_version", mutableState.value.release?.versionCode ?: 0)
            .putBoolean("install_requested", false).putBoolean("waiting_permission", false).putBoolean(NativeApkInstaller.AUTHORIZED, false).commit()
        mutableState.update { it.copy(prompt = false, installRequested = false, waitingPermission = false,
            status = if (it.readyFile != null) "Обновление готово — установите его из настроек" else it.status) }
        }
        installer.cancelAfterNo(cancelledSession)
    }

    fun approve() {
        mutableState.update { it.copy(prompt = false) }
        scope.launch {
            approveLock.withLock {
                val active = manager.getWorkInfosForUniqueWork(workName).get().any { !it.state.isFinished }
                val pending = synchronized(consentLock) {
                val current = mutableState.value
                val release = current.release ?: return@synchronized null
                if (current.pendingConfirmation) { installer.continueConfirmation(); return@synchronized null }
                if (current.installing) return@synchronized null
                if (current.readyFile != null) { requestInstallation(); return@synchronized null }
                if (current.downloading || active) return@synchronized null
                // Build before consent and commit its ID with approved metadata,
                // so a previous terminal observer cannot clear the new consent.
                val work = downloadWork(release)
                prefs.edit().putString("approved", NativeUpdatePolicy.json.encodeToString(release))
                    .putString("work_id", work.id.toString())
                    .putBoolean("install_requested", true).putBoolean("waiting_permission", false).remove("ready_file").commit()
                mutableState.update { it.copy(downloading = true, progress = 0, installRequested = true, error = null, waitingPermission = false, status = "Ожидаем загрузку обновления…") }
                release to work
                }
                if (pending != null) enqueueApprovedRelease(pending.first, pending.second)
            }
        }
    }

    private fun downloadWork(release: ApkRelease) = OneTimeWorkRequestBuilder<NativeUpdateWorker>()
        .setInputData(workDataOf(NativeUpdateWorker.RELEASE to NativeUpdatePolicy.workerPayload(release)))
        .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
        .setBackoffCriteria(BackoffPolicy.LINEAR, 15, TimeUnit.SECONDS).build()

    private fun enqueueApprovedRelease(release: ApkRelease, work: androidx.work.OneTimeWorkRequest = downloadWork(release)) {
        try {
            synchronized(consentLock) {
            if (!prefs.getBoolean("install_requested", false)) return
            prefs.edit().putString("work_id", work.id.toString()).commit()
            mutableState.update { it.copy(release = release, downloading = true, progress = 0, installRequested = true, error = null, status = "Ожидаем загрузку обновления…") }
            }
            manager.enqueueUniqueWork(workName, ExistingWorkPolicy.KEEP, work).result.get()
        } catch (_: Exception) {
            prefs.edit().putBoolean("install_requested", false).commit()
            mutableState.update { it.copy(downloading = false, installRequested = false, error = "Не удалось запланировать загрузку. Повторите из настроек.") }
        }
    }

    fun requestInstallation() {
        synchronized(consentLock) {
        prefs.edit().putBoolean("install_requested", true).commit()
        mutableState.update { it.copy(installRequested = true, prompt = false, error = null) }
        }
    }

    fun installationPermissionDeferred(): Boolean = synchronized(consentLock) {
        if (!prefs.getBoolean("install_requested", false)) return@synchronized false
        prefs.edit().putBoolean("waiting_permission", true).putBoolean("install_requested", false).commit()
        mutableState.update { it.copy(waitingPermission = true, installRequested = false, status = "Разрешите установку обновлений в настройках Android") }
        true
    }

    fun resumeAfterPermission() {
        synchronized(consentLock) {
        if (mutableState.value.waitingPermission && (android.os.Build.VERSION.SDK_INT < 26 || context.packageManager.canRequestPackageInstalls())) {
            prefs.edit().putBoolean("waiting_permission", false).putBoolean("install_requested", true).commit()
            mutableState.update { it.copy(waitingPermission = false, installRequested = true, error = null) }
        }
        }
    }

    suspend fun installationFile(): File? = withContext(Dispatchers.IO) {
        val current = mutableState.value
        val release = current.release ?: return@withContext null
        val filename = current.readyFile ?: return@withContext null
        try {
            check(filename == release.filename)
            val file = File(File(context.cacheDir, "updates"), filename)
            verifyDownloadedApk(context, file, release, config)
            file
        } catch (_: Exception) {
            prefs.edit().remove("ready_file").putBoolean("install_requested", false).commit()
            mutableState.update { it.copy(readyFile = null, installRequested = false, error = "Проверка APK не пройдена. Скачайте обновление повторно.", status = "Повторить загрузку обновления") }
            null
        }
    }

    fun installationLaunched(): Boolean = synchronized(consentLock) {
        if (!prefs.getBoolean("install_requested", false)) return@synchronized false
        prefs.edit().putBoolean("install_requested", false).putBoolean("waiting_permission", false).commit()
        mutableState.update { it.copy(installRequested = false, waitingPermission = false, status = "Подтвердите установку в системном окне Android") }
        true
    }

    fun installationLaunchFailed() {
        prefs.edit().putBoolean("install_requested", false).commit()
        mutableState.update { it.copy(installRequested = false, error = "Не удалось открыть установщик Android. Повторите из настроек.") }
    }

    internal fun <T> withInstallerLock(block: () -> T): T = synchronized(consentLock, block)

    internal fun installerStateChanged(installing: Boolean, pending: Boolean, status: String, error: String? = null) {
        mutableState.update { it.copy(installing = installing, pendingConfirmation = pending,
            installRequested = prefs.getBoolean("install_requested", false), waitingPermission = prefs.getBoolean("waiting_permission", false),
            installerEvent = it.installerEvent + 1, prompt = false, status = status, error = error) }
    }

    internal fun restoreApprovedInstallation(release: ApkRelease) {
        prefs.edit().putString("available", NativeUpdatePolicy.json.encodeToString(release))
            .putString("approved", NativeUpdatePolicy.json.encodeToString(release)).putString("ready_file", release.filename)
            .putBoolean("install_requested", true).commit()
        mutableState.update { it.copy(release = release, readyFile = release.filename, installing = false,
            pendingConfirmation = false, installRequested = true, status = "Продолжаем установку обновления…") }
    }

    internal fun installerCompleted() {
        mutableState.update { it.copy(release = null, readyFile = null, installing = false, pendingConfirmation = false,
            installRequested = false, waitingPermission = false, prompt = false, status = "Обновление установлено", error = null) }
    }

    fun close() { installer.close(); scope.cancel() }

    private companion object {
        fun defaultHttp() = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
            .connectTimeout(10, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).callTimeout(25, TimeUnit.SECONDS).build()
    }
}
