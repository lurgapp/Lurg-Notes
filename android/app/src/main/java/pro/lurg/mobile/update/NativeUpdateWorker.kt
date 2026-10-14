package pro.lurg.notes.update

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import com.android.apksig.ApkVerifier

interface NativeUpdateHost { val nativeApkUpdater: NativeApkUpdater }

class NativeUpdateWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val updater = (applicationContext as NativeUpdateHost).nativeApkUpdater
        val release = try {
            NativeUpdatePolicy.parse(requireNotNull(inputData.getString(RELEASE)), updater.config).also {
                if (it.versionCode <= updater.config.installedCode) throw UpdateSecurityException("Эта версия уже установлена")
            }
        } catch (_: Exception) { return@withContext Result.failure(workDataOf(ERROR to "Некорректное обновление. Проверьте обновления снова.")) }
        val directory = File(applicationContext.cacheDir, "updates").apply { mkdirs() }
        val final = File(directory, release.filename)
        val partial = File(directory, "${release.filename}.part")
        try {
            if (final.isFile) {
                verifyDownloadedApk(applicationContext, final, release, updater.config)
                return@withContext Result.success(workDataOf(FILE to final.name, VERSION to release.versionCode))
            }
            // Old partial files are never offered to Android's installer.
            directory.listFiles()?.filter { it.name.endsWith(".part") }?.forEach { it.delete() }
            if (directory.usableSpace < release.sizeBytes + 10L * 1024 * 1024) throw UpdateSecurityException("Недостаточно свободного места для обновления")
            val http = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
                .connectTimeout(15, TimeUnit.SECONDS).readTimeout(45, TimeUnit.SECONDS).callTimeout(10, TimeUnit.MINUTES).build()
            http.newCall(Request.Builder().url(release.apkUrl).get().build()).execute().use { response ->
                if (response.code !in 200..299) {
                    if (response.code >= 500 || response.code == 429) throw IOException("Сервис загрузки временно недоступен")
                    throw UpdateSecurityException("Не удалось получить проверенное обновление")
                }
                val body = response.body ?: throw IOException("Пустой ответ загрузки")
                if (body.contentLength() >= 0 && body.contentLength() != release.sizeBytes) throw UpdateSecurityException("Размер APK не совпадает с манифестом")
                var received = 0L
                var lastProgress = -1
                body.byteStream().use { input -> partial.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        if (isStopped) throw CancellationException("Update download stopped")
                        val count = input.read(buffer)
                        if (count < 0) break
                        received += count
                        if (received > release.sizeBytes) throw UpdateSecurityException("APK превышает ожидаемый размер")
                        output.write(buffer, 0, count)
                        val progress = (received * 100 / release.sizeBytes).toInt()
                        if (progress != lastProgress) { setProgress(workDataOf(PROGRESS to progress)); lastProgress = progress }
                    }
                    output.flush()
                    if (received != release.sizeBytes) throw UpdateSecurityException("APK загружен не полностью")
                } }
            }
            verifyDownloadedApk(applicationContext, partial, release, updater.config)
            if (!partial.renameTo(final)) throw IOException("Не удалось сохранить APK")
            Result.success(workDataOf(FILE to final.name, VERSION to release.versionCode))
        } catch (cancelled: CancellationException) { partial.delete(); throw cancelled
        } catch (error: UpdateSecurityException) { partial.delete(); final.delete(); Result.failure(workDataOf(ERROR to (error.message ?: "Проверка обновления не пройдена")))
        } catch (_: Exception) {
            partial.delete()
            if (runAttemptCount < 3) Result.retry() else Result.failure(workDataOf(ERROR to "Загрузка не завершена. Подключитесь к сети и повторите."))
        }
    }

    companion object {
        const val RELEASE = "release"
        const val FILE = "file"
        const val VERSION = "version"
        const val PROGRESS = "progress"
        const val ERROR = "error"
    }
}

/** Rechecked immediately before sharing a file, including after process restart. */
fun verifyDownloadedApk(context: Context, file: File, release: ApkRelease, config: NativeUpdateConfig) {
    verifyApkIntegrity(file, release, config)
    @Suppress("DEPRECATION")
    val archive = context.packageManager.getPackageArchiveInfo(file.absolutePath, 0)
        ?: throw UpdateSecurityException("APK не распознан Android")
    @Suppress("DEPRECATION")
    val version = if (android.os.Build.VERSION.SDK_INT >= 28) archive.longVersionCode else archive.versionCode.toLong()
    if (archive.packageName != config.packageName || version != release.versionCode.toLong() || archive.versionName != release.versionName || version <= config.installedCode)
        throw UpdateSecurityException("Пакет или версия APK не соответствует обновлению")
}

fun verifyApkIntegrity(file: File, release: ApkRelease, config: NativeUpdateConfig) {
    NativeUpdatePolicy.validate(release, config)
    if (!file.isFile || file.length() != release.sizeBytes) throw UpdateSecurityException("Размер APK не совпадает с манифестом")
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input -> val buffer = ByteArray(64 * 1024); while (true) { val count = input.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) } }
    val hash = digest.digest().joinToString("") { "%02x".format(it) }
    if (hash != release.sha256) throw UpdateSecurityException("Контрольная сумма APK не совпадает")
    val verification = try { ApkVerifier.Builder(file).setMinCheckedPlatformVersion(config.minSdk).build().verify() }
        catch (_: Exception) { throw UpdateSecurityException("APK повреждён или не подписан") }
    if (!verification.isVerified || verification.signerCertificates.size != 1) throw UpdateSecurityException("Криптографическая подпись APK не прошла проверку")
    val signer = MessageDigest.getInstance("SHA-256").digest(verification.signerCertificates.single().encoded).joinToString("") { "%02x".format(it) }
    if (signer != config.pinnedSignerSha256) throw UpdateSecurityException("APK подписан другим разработчиком")
}
