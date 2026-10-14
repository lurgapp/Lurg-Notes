package pro.lurg.notes.api

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import pro.lurg.notes.security.SecureTokenStore
import java.util.UUID
import java.util.concurrent.TimeUnit

class NotesAiClient(context: Context, private val client: OkHttpClient = defaultClient()) {
  private val tokens = SecureTokenStore(context)

  suspend fun runAction(
    ownerId: String,
    noteId: String,
    action: String,
    documentText: String,
    selectedText: String,
    userRequest: String,
    revision: Int
  ): AiResult = withContext(Dispatchers.IO) {
    val token = tokens.getTokenForOwner(ownerId)
    val payload = JSONObject()
      .put("selected_text", selectedText)
      .put("document_text", documentText)
      .put("user_request", userRequest)
      .put("locale", "ru")
    val body = JSONObject()
      .put("service", "lurg_notes")
      .put("action", action)
      .put("request_id", UUID.randomUUID().toString())
      .put("document_revision", revision)
      .put("selected_block_ids", org.json.JSONArray())
      .put("payload", payload)
      .toString()
      .toRequestBody(JSON)
    val first = executeAction(ownerId, noteId, token, body, action)
    if (first.code != 404) return@withContext first.result()
    createRemoteNote(ownerId, noteId, token)
    executeAction(ownerId, noteId, token, body, action).result()
  }

  private fun executeAction(ownerId: String, noteId: String, token: String, body: RequestBody, action: String): AiHttpResult {
    requireCurrentSession(ownerId, token)
    val request = Request.Builder()
      .url("$API_URL/notes/${noteId}/ai/actions")
      .addHeader("Authorization", "Bearer $token")
      .addHeader("Content-Type", "application/json")
      .post(body)
      .build()
    client.newCall(request).execute().use { response ->
      val text = response.body?.string().orEmpty()
      requireCurrentSession(ownerId, token)
      return AiHttpResult(response.code, text, action)
    }
  }

  private fun AiHttpResult.result(): AiResult {
    if (code !in 200..299) throw IllegalStateException(readError(code))
    val json = JSONObject(text)
    val result = json.optJSONObject("result")
    return AiResult(
      action = json.optString("action", action),
      content = result?.optString("content").orEmpty(),
      requestId = json.optString("request_id")
    )
  }

  private fun createRemoteNote(ownerId: String, noteId: String, token: String) {
    requireCurrentSession(ownerId, token)
    val body = JSONObject()
      .put("client_note_id", noteId)
      .put("title", "Encrypted note")
      .toString()
      .toRequestBody(JSON)
    val request = Request.Builder()
      .url("$API_URL/notes")
      .addHeader("Authorization", "Bearer $token")
      .addHeader("Content-Type", "application/json")
      .post(body)
      .build()
    client.newCall(request).execute().use { response ->
      requireCurrentSession(ownerId, token)
      if (response.isSuccessful || response.code == 409) return
      throw IllegalStateException(readError(response.code))
    }
  }

  private fun requireCurrentSession(ownerId: String, token: String) {
    check(tokens.isCurrentSession(ownerId, token)) { "Сессия владельца изменилась" }
  }

  private fun readError(code: Int): String = when (code) {
    401 -> "Сессия Lurg ID истекла. Войдите снова, чтобы использовать AI."
    403 -> "Для этого действия AI нет доступа."
    408, 504 -> "AI не успел ответить. Текст заметки не изменён — повторите попытку."
    429 -> "AI временно занят. Текст заметки не изменён — повторите чуть позже."
    in 500..599 -> "Сервис AI временно недоступен. Текст заметки не изменён — повторите позже."
    else -> "Не удалось выполнить действие AI. Текст заметки не изменён."
  }

  private companion object {
    fun defaultClient() = OkHttpClient.Builder()
      .connectTimeout(15, TimeUnit.SECONDS)
      .readTimeout(90, TimeUnit.SECONDS)
      .writeTimeout(30, TimeUnit.SECONDS)
      .build()
    val API_URL = pro.lurg.notes.BuildConfig.API_BASE_URL
    val JSON = "application/json; charset=utf-8".toMediaType()
  }
}

data class AiResult(
  val action: String,
  val content: String,
  val requestId: String
)

private data class AiHttpResult(
  val code: Int,
  val text: String,
  val action: String
)
