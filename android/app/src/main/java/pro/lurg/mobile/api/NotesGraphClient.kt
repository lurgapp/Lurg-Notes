package pro.lurg.notes.api

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import pro.lurg.notes.security.SecureTokenStore
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

data class NotesGraphCandidate(
  val id: String,
  val factText: String,
  val title: String,
  val explanation: String,
  val confidence: Double,
  val sourceExcerpt: String,
  val changes: List<String>
)

class NotesGraphClient(context: Context, private val client: OkHttpClient = defaultClient()) {
  private val tokens = SecureTokenStore(context)

  suspend fun extract(ownerId: String, noteId: String, title: String, markdown: String, revision: Int): List<NotesGraphCandidate> = withContext(Dispatchers.IO) {
    val token = tokens.getTokenForOwner(ownerId)
    // Candidates are owner-scoped on the backend. Ensure an offline-created note
    // exists remotely before asking the Graph workflow to inspect it.
    ensureRemoteNote(ownerId, noteId, token)
    val body = JSONObject()
      .put("document_text", markdown)
      .put("note_title", title)
      .put("document_revision", revision)
      .put("content_hash", sha256(markdown))
      .put("ai_analysis_consent", true)
      .toString().toRequestBody(JSON)
    val response = execute(ownerId, "/notes/$noteId/graph-candidates", token, body)
    val candidates = response.optJSONArray("candidates") ?: JSONArray()
    buildList {
      for (index in 0 until candidates.length()) {
        val item = candidates.optJSONObject(index) ?: continue
        val changes = item.optJSONArray("changes") ?: JSONArray()
        add(NotesGraphCandidate(
          id = item.getString("candidate_id"),
          factText = item.optString("fact_text"), title = item.optString("title", "Добавить в Lurg Graph"),
          explanation = item.optString("explanation"), confidence = item.optDouble("confidence", 0.0),
          sourceExcerpt = item.optString("source_excerpt"),
          changes = buildList { for (i in 0 until changes.length()) changes.optJSONObject(i)?.let { add("${it.optString("type")}: ${it.optString("value")}") } }
        ))
      }
    }
  }

  private fun ensureRemoteNote(ownerId: String, noteId: String, token: String) {
    requireCurrentSession(ownerId, token)
    val request = Request.Builder().url("$API_URL/notes")
      .addHeader("Authorization", "Bearer $token")
      .addHeader("Content-Type", "application/json")
      .post(JSONObject().put("client_note_id", noteId).put("title", "Encrypted note").toString().toRequestBody(JSON))
      .build()
    client.newCall(request).execute().use { response ->
      requireCurrentSession(ownerId, token)
      // 409 means the note was created by encrypted sync or a prior Graph action.
      if (response.isSuccessful || response.code == 409) return
      throw IllegalStateException(readError(response.code))
    }
  }

  suspend fun confirm(ownerId: String, candidateId: String) = postGraphChange(ownerId, candidateId, "confirm")
  suspend fun reject(ownerId: String, candidateId: String) = postGraphChange(ownerId, candidateId, "reject")

  private suspend fun postGraphChange(ownerId: String, id: String, action: String) = withContext(Dispatchers.IO) {
    val token = tokens.getTokenForOwner(ownerId)
    execute(ownerId, "/graph/changes/$id/$action", token, "{}".toRequestBody(JSON))
  }

  private fun execute(ownerId: String, path: String, token: String, body: okhttp3.RequestBody): JSONObject {
    requireCurrentSession(ownerId, token)
    val request = Request.Builder().url("$API_URL$path").addHeader("Authorization", "Bearer $token").post(body).build()
    client.newCall(request).execute().use { response ->
      val text = response.body?.string().orEmpty()
      requireCurrentSession(ownerId, token)
      if (!response.isSuccessful) throw IllegalStateException(readError(response.code))
      return JSONObject(text)
    }
  }

  private fun requireCurrentSession(ownerId: String, token: String) {
    check(tokens.isCurrentSession(ownerId, token)) { "Сессия владельца изменилась" }
  }

  private fun readError(code: Int): String = when (code) {
    401 -> "Сессия Lurg ID истекла. Войдите снова, чтобы работать с Graph."
    403 -> "Нет доступа к Lurg Graph."
    408, 504 -> "Graph не успел обработать заметку. Изменения не были внесены — повторите попытку."
    429 -> "Graph временно занят. Изменения не были внесены — повторите чуть позже."
    in 500..599 -> "Сервис Lurg Graph временно недоступен. Изменения не были внесены."
    else -> "Не удалось обработать заметку для Lurg Graph. Изменения не были внесены."
  }

  private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }

  private companion object {
    fun defaultClient() = OkHttpClient.Builder()
      .connectTimeout(15, TimeUnit.SECONDS).readTimeout(45, TimeUnit.SECONDS).writeTimeout(30, TimeUnit.SECONDS).build()
    val API_URL = pro.lurg.notes.BuildConfig.API_BASE_URL
    val JSON = "application/json; charset=utf-8".toMediaType()
  }
}
