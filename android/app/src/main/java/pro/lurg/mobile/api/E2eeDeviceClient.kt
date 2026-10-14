package pro.lurg.notes.api

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import pro.lurg.notes.security.E2eeV2Crypto
import pro.lurg.notes.security.RecoveryMaterial
import pro.lurg.notes.security.SecureTokenStore
import java.util.concurrent.TimeUnit

data class E2eeDeviceStatus(val id: String, val name: String, val fingerprint: String, val status: String, val publicKey: String)
class E2eeSetupRequiredException : IllegalStateException("Настройте recovery secret перед E2EE синхронизацией.")
class E2eeEnrollmentPendingException : IllegalStateException("Это устройство ожидает одобрения доверенным устройством.")

/** Device registration and opaque UMK/recovery transport. Never logs requests. */
class E2eeDeviceClient(private val context: Context) {
  private val tokens = SecureTokenStore(context)
  private val crypto: E2eeV2Crypto get() = E2eeV2Crypto(context)
  private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()

  suspend fun ensureActive(ownerId: String) = withContext(Dispatchers.IO) { registrationLock.withLock {
    val crypto = E2eeV2Crypto(context, ownerId)
    check(tokens.ownerId() == ownerId) { "Сессия владельца изменилась" }
    val credential = tokens.getProtectedValue("device_credential", ownerId)
    if (credential == null) {
      val response = accountRequest(ownerId, "/notes/e2ee/devices", "POST", JSONObject()
        .put("device_id", crypto.deviceId()).put("name", android.os.Build.MODEL.take(120)).put("public_key", crypto.publicKey()).toString())
      require(response.first in 200..299) { "Не удалось зарегистрировать устройство E2EE (${response.first})" }
      val body = JSONObject(response.second)
      check(tokens.ownerId() == ownerId) { "Сессия владельца изменилась" }
      tokens.setProtectedValue("device_credential", body.getString("device_credential"), ownerId)
      val status = body.getJSONObject("device").getString("status")
      if (status == "pending") throw E2eeEnrollmentPendingException()
      if (!crypto.hasUmk()) throw E2eeSetupRequiredException()
      return@withLock
    }
    val me = deviceRequest(ownerId, "/notes/e2ee/devices/me", credential)
    if (me.first !in 200..299) throw IllegalStateException("E2EE устройство недоступно (${me.first})")
    val status = JSONObject(me.second).getJSONObject("device").getString("status")
    if (status == "pending") throw E2eeEnrollmentPendingException()
    if (!crypto.hasUmk()) {
      val envelope = deviceRequest(ownerId, "/notes/e2ee/key-envelope", credential)
      // The first trusted device has no RSA-wrapped copy: it must create its
      // UMK/recovery material explicitly. An approved secondary device always
      // has an envelope, so 409 cannot be mistaken for a pending approval.
      if (envelope.first == 409) throw E2eeSetupRequiredException()
      if (envelope.first !in 200..299) throw E2eeEnrollmentPendingException()
      val value = JSONObject(envelope.second)
      crypto.unwrapAndSaveUmk(value.getString("wrapped_umk"), ownerId, crypto.deviceId())
    }
  } }

  /** First-device setup. Present and persist the returned secret outside the app before relying on recovery. */
  suspend fun bootstrapInitial(ownerId: String): RecoveryMaterial = withContext(Dispatchers.IO) { registrationLock.withLock {
    val crypto = E2eeV2Crypto(context, ownerId)
    check(tokens.ownerId() == ownerId) { "Сессия владельца изменилась" }
    ensureRegisteredInitial(ownerId)
    if (crypto.hasUmk()) throw IllegalStateException("UMK уже настроен")
    val umk = crypto.createUmk()
    try {
      val recovery = crypto.createRecovery(ownerId, umk)
      val response = deviceRequest(ownerId, "/notes/e2ee/recovery", requireCredential(ownerId), "PUT", JSONObject()
        .put("recovery_salt", recovery.salt).put("nonce", recovery.envelope.nonce)
        .put("ciphertext", recovery.envelope.ciphertext).put("recovery_auth_verifier", recovery.authVerifier).toString())
      require(response.first in 200..299) { "Не удалось сохранить recovery envelope (${response.first})" }
      check(tokens.ownerId() == ownerId) { "Сессия владельца изменилась" }
      crypto.saveUmk(umk)
      recovery
    } finally { umk.fill(0) }
  } }

  suspend fun pendingDevices(): List<E2eeDeviceStatus> = withContext(Dispatchers.IO) {
    val ownerId = requireNotNull(tokens.ownerId())
    val response = deviceRequest(ownerId, "/notes/e2ee/devices", requireCredential(ownerId))
    require(response.first in 200..299) { "Не удалось загрузить устройства" }
    val devices = JSONObject(response.second).getJSONArray("devices")
    buildList { for (i in 0 until devices.length()) { val item = devices.getJSONObject(i); add(E2eeDeviceStatus(item.getString("id"), item.optString("name", "Устройство"), item.optString("public_key_fingerprint"), item.getString("status"), item.optString("public_key"))) } }
  }

  suspend fun approve(device: E2eeDeviceStatus, ownerId: String) = withContext(Dispatchers.IO) {
    val crypto = E2eeV2Crypto(context, ownerId)
    check(tokens.ownerId() == ownerId) { "Сессия владельца изменилась" }
    require(device.status == "pending" && device.publicKey.isNotBlank())
    val wrapped = crypto.wrapUmkFor(device.publicKey, ownerId, device.id)
    val contextValue = "lurg-notes:v2:umk:$ownerId:${device.id}"
    val response = deviceRequest(ownerId, "/notes/e2ee/devices/${device.id}/approve", requireCredential(ownerId), "POST", JSONObject()
      .put("wrapped_umk", wrapped).put("wrap_algorithm", "RSA-OAEP-SHA-256").put("wrap_context", contextValue).toString())
    require(response.first in 200..299) { "Не удалось одобрить устройство (${response.first})" }
  }

  suspend fun revoke(deviceId: String) = withContext(Dispatchers.IO) {
    val ownerId = requireNotNull(tokens.ownerId())
    val response = deviceRequest(ownerId, "/notes/e2ee/devices/$deviceId/revoke", requireCredential(ownerId), "POST", "{}")
    require(response.first in 200..299) { "Не удалось отозвать устройство (${response.first})" }
  }

  /** Recovery needs the account session plus the offline secret; neither alone is sufficient. */
  suspend fun recover(ownerId: String, recoverySecret: String) = withContext(Dispatchers.IO) {
    val crypto = E2eeV2Crypto(context, ownerId)
    check(tokens.ownerId() == ownerId) { "Сессия владельца изменилась" }
    val recovery = accountRequest(ownerId, "/notes/e2ee/recovery", "GET", null)
    require(recovery.first in 200..299) { "Recovery material недоступен (${recovery.first})" }
    val envelope = JSONObject(recovery.second)
    val salt = envelope.getString("recovery_salt")
    val publicKey = crypto.publicKey()
    // Decrypt before activation so an incorrect secret never creates a usable device.
    crypto.recoverAndSaveUmk(recoverySecret, salt, pro.lurg.notes.security.E2eeEnvelope(envelope.getString("nonce"), envelope.getString("ciphertext")), ownerId)
    val proof = crypto.recoveryProof(recoverySecret, salt, ownerId, crypto.deviceId(), publicKey)
    val enrolled = accountRequest(ownerId, "/notes/e2ee/recovery/enroll", "POST", JSONObject().put("device_id", crypto.deviceId())
      .put("name", android.os.Build.MODEL.take(120)).put("public_key", publicKey).put("recovery_proof", proof).toString())
    require(enrolled.first in 200..299) { "Recovery enrollment отклонён (${enrolled.first})" }
    tokens.setProtectedValue("device_credential", JSONObject(enrolled.second).getString("device_credential"), ownerId)
  }

  fun deviceHeaders(ownerId: String): Pair<String, String> = E2eeV2Crypto(context, ownerId).deviceId() to requireCredential(ownerId)
  fun currentDeviceId(): String = crypto.deviceId()
  fun crypto(): E2eeV2Crypto = crypto

  private suspend fun ensureRegisteredInitial(ownerId: String) {
    val crypto = E2eeV2Crypto(context, ownerId)
    val credential = tokens.getProtectedValue("device_credential", ownerId)
    if (credential != null) return
    val response = accountRequest(ownerId, "/notes/e2ee/devices", "POST", JSONObject()
      .put("device_id", crypto.deviceId()).put("name", android.os.Build.MODEL.take(120)).put("public_key", crypto.publicKey()).toString())
    require(response.first in 200..299) { "Не удалось зарегистрировать устройство E2EE (${response.first})" }
    val body = JSONObject(response.second)
    check(tokens.ownerId() == ownerId) { "Сессия владельца изменилась" }
    tokens.setProtectedValue("device_credential", body.getString("device_credential"), ownerId)
    if (body.getJSONObject("device").getString("status") != "active") throw E2eeEnrollmentPendingException()
  }
  private fun requireCredential(ownerId: String) = requireNotNull(tokens.getProtectedValue("device_credential", ownerId)) { "Устройство E2EE не зарегистрировано" }
  private fun accountRequest(ownerId: String, path: String, method: String, json: String? = null): Pair<Int, String> = request(ownerId, path, method, json, null)
  private fun deviceRequest(ownerId: String, path: String, credential: String, method: String = "GET", json: String? = null): Pair<Int, String> = request(ownerId, path, method, json, credential)
  private fun request(ownerId: String, path: String, method: String, json: String?, credential: String?): Pair<Int, String> {
    val token = tokens.getTokenForOwner(ownerId)
    val builder = Request.Builder().url(pro.lurg.notes.BuildConfig.API_BASE_URL + path).addHeader("Authorization", "Bearer $token")
    if (credential != null) builder.addHeader("X-Lurg-Notes-Device-Id", E2eeV2Crypto(context, ownerId).deviceId()).addHeader("X-Lurg-Notes-Device-Credential", credential)
    when (method) { "GET" -> builder.get(); "PUT" -> builder.put(requireNotNull(json).toRequestBody(JSON)); else -> builder.post(requireNotNull(json).toRequestBody(JSON)) }
    http.newCall(builder.build()).execute().use {
      val value = it.code to it.body?.string().orEmpty()
      check(tokens.isCurrentSession(ownerId, token)) { "Сессия владельца изменилась" }
      return value
    }
  }
  private companion object {
    val JSON = "application/json; charset=utf-8".toMediaType()
    // Manual first-device setup and background sync use separate client instances.
    // Serialize the registration and recovery/key commit across those instances.
    val registrationLock = Mutex()
  }
}
