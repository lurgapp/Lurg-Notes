package pro.lurg.notes.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class NotesRelease(val versionCode: Int, val versionName: String, val apkUrl: String, val notes: String)

class NotesUpdateClient {
  private val client = OkHttpClient.Builder().connectTimeout(8, TimeUnit.SECONDS).readTimeout(12, TimeUnit.SECONDS).build()

  suspend fun latest(): NotesRelease = withContext(Dispatchers.IO) {
    val request = Request.Builder().url("https://notes.lurg.pro/update.json").get().build()
    client.newCall(request).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("Не удалось проверить обновления")
      val json = JSONObject(response.body?.string().orEmpty())
      NotesRelease(json.getInt("version_code"), json.getString("version_name"), json.getString("apk_url"), json.optString("notes"))
    }
  }
}
