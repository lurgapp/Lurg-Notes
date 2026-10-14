package pro.lurg.notes.api

import android.content.Context
import android.provider.Settings
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit
import pro.lurg.notes.BuildConfig

class PushRegistrationClient(context: Context) {
  private val appContext = context.applicationContext
  private val auth = AuthClient(context)
  private val client = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(16, TimeUnit.SECONDS).build()

  suspend fun registerCurrentToken() {
    val token = FirebaseMessaging.getInstance().token.await().takeIf { it.isNotBlank() } ?: return
    register(token)
  }

  suspend fun register(token: String) = withContext(Dispatchers.IO) {
    val accessToken = auth.token() ?: return@withContext
    val body = JSONObject()
      .put("provider", "fcm")
      .put("platform", "android")
      .put("device_id", deviceId())
      .put("token", token)
      .put("app_version_name", BuildConfig.VERSION_NAME)
      .put("app_version_code", BuildConfig.VERSION_CODE)
      .put("build_identifier", "lurg-notes-android")
      .toString().toRequestBody("application/json; charset=utf-8".toMediaType())
    val request = Request.Builder().url("${AuthClient.API_URL}/app/devices/push-token")
      .header("Authorization", "Bearer $accessToken").post(body).build()
    client.newCall(request).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("Не удалось зарегистрировать уведомления")
    }
  }

  suspend fun deactivate() = withContext(Dispatchers.IO) {
    val accessToken = auth.token() ?: return@withContext
    val request = Request.Builder().url("${AuthClient.API_URL}/app/devices/${deviceId()}/push-token?app_id=notes")
      .header("Authorization", "Bearer $accessToken").delete().build()
    runCatching { client.newBuilder().callTimeout(2, TimeUnit.SECONDS).build().newCall(request).execute().close() }
  }

  private fun deviceId(): String {
    val prefs = appContext.getSharedPreferences("lurg_notes_push", Context.MODE_PRIVATE)
    return prefs.getString("device_id", null) ?: UUID.randomUUID().toString().also {
      prefs.edit().putString("device_id", it).apply()
    }
  }
}
