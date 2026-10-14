package pro.lurg.notes.update

import java.net.URI
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.encodeToString

data class NativeUpdateConfig(
    val appId: String,
    val packageName: String,
    val manifestUrl: String,
    val apkHost: String,
    val apkPathPrefix: String,
    val filePrefix: String,
    val pinnedSignerSha256: String,
    val installedCode: Int,
    val installedName: String,
    val minSdk: Int = 26,
    val updateChannelId: String = "lurg_general",
)

@Serializable
data class ApkRelease(
    val platform: String,
    @SerialName("package_name") val packageName: String,
    @SerialName("version_code") val versionCode: Int,
    @SerialName("version_name") val versionName: String,
    @SerialName("apk_url") val apkUrl: String,
    val sha256: String,
    @SerialName("size_bytes") val sizeBytes: Long,
    @SerialName("signing_certificate_sha256") val signingCertificateSha256: String,
    val title: String = "",
    val changelog: List<String> = emptyList(),
) {
    val filename: String get() = URI(apkUrl).path.substringAfterLast('/')
}

class UpdateSecurityException(message: String) : IllegalStateException(message)

/** Product identity comes from the installed app, never from notification data. */
object NativeUpdatePolicy {
    const val MAX_APK_BYTES = 100L * 1024 * 1024
    const val MAX_MANIFEST_BYTES = 64 * 1024
    val json = Json { ignoreUnknownKeys = true }
    private val hash = Regex("[a-f0-9]{64}")

    fun parse(raw: String, config: NativeUpdateConfig): ApkRelease {
        if (raw.toByteArray(Charsets.UTF_8).size > MAX_MANIFEST_BYTES) throw UpdateSecurityException("Манифест обновления слишком большой")
        val release = json.decodeFromString<ApkRelease>(raw)
        validate(release, config)
        return release
    }

    fun validate(release: ApkRelease, config: NativeUpdateConfig) {
        fun requireSafe(condition: Boolean, reason: String) { if (!condition) throw UpdateSecurityException(reason) }
        requireSafe(release.platform == "android" && release.packageName == config.packageName, "Обновление предназначено для другого приложения")
        requireSafe(release.versionCode > 0 && release.versionName.isNotBlank() && release.versionName.length <= 80, "Некорректная версия обновления")
        requireSafe(hash.matches(release.sha256), "Некорректная контрольная сумма обновления")
        requireSafe(release.signingCertificateSha256 == config.pinnedSignerSha256, "Подпись обновления не соответствует приложению")
        requireSafe(release.sizeBytes in 1..MAX_APK_BYTES, "Некорректный размер обновления")
        val url = try { URI(release.apkUrl) } catch (_: Exception) { throw UpdateSecurityException("Некорректная ссылка на обновление") }
        requireSafe(url.scheme == "https" && url.host == config.apkHost && url.port in listOf(-1, 443) && url.userInfo == null && url.rawQuery == null && url.rawFragment == null,
            "Недоверенная ссылка на обновление")
        val expectedPath = "${config.apkPathPrefix}${config.filePrefix}-${release.versionCode}-${release.sha256}.apk"
        requireSafe(url.rawPath == expectedPath, "Ссылка не соответствует версии и контрольной сумме")
        requireSafe(release.changelog.size <= 30 && release.changelog.all { it.length <= 1000 } && release.title.length <= 200, "Описание обновления слишком большое")
    }

    fun isUpdatePush(data: Map<String, String>, config: NativeUpdateConfig): Boolean =
        data["type"] == "app_update" && data["app_id"] == config.appId &&
            (data["package_name"] == null || data["package_name"] == config.packageName)

    /** WorkManager Data is limited to 10 KiB; descriptions stay in private preferences. */
    fun workerPayload(release: ApkRelease): String = json.encodeToString(release.copy(title = "", changelog = emptyList()))
}
