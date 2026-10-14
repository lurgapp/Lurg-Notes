package pro.lurg.notes.api

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import pro.lurg.notes.data.NoteEntity
import pro.lurg.notes.data.NoteBlockEntity
import pro.lurg.notes.security.E2eeEnvelope
import pro.lurg.notes.security.SecureTokenStore
import java.util.concurrent.TimeUnit

class EncryptedNotesSyncClient(private val context: Context) {
  private val tokens = SecureTokenStore(context)
  private val devices = E2eeDeviceClient(context)
  private var validatedOwner: String? = null
  private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()

  suspend fun upload(ownerId: String, note: NoteEntity, blocks: List<NoteBlockEntity>) = withContext(Dispatchers.IO) {
    val token = ensureOwner(ownerId)
    val serializedBlocks = org.json.JSONArray().apply {
      blocks.forEach { block -> put(JSONObject()
        .put("id", block.id).put("parent_id", block.parentId).put("position", block.position)
        .put("type", block.type).put("content", JSONObject(block.contentJson)).put("attrs", JSONObject(block.attrsJson))
        .put("origin", block.origin).put("revision", block.revision).put("updated_at", block.updatedAt)) }
    }
    val content = JSONObject().put("title", note.title).put("markdown", note.markdown).put("blocks", serializedBlocks).put("updated_at", note.updatedAt).toString()
    val crypto = pro.lurg.notes.security.E2eeV2Crypto(context, ownerId)
    val plain = content.toByteArray(Charsets.UTF_8)
    val encrypted = crypto.encryptDocument(ownerId, note.id, plain)
    val payload = JSONObject().put("format_version", 2).put("algorithm", "AES-256-GCM").put("key_version", 2).put("nonce", encrypted.nonce)
      .put("ciphertext", encrypted.ciphertext).put("content_hash", crypto.contentToken(plain)).put("document_revision", note.revision).put("base_document_revision", note.serverRevision).toString()
    plain.fill(0)
    val response = request(ownerId, "/notes/e2ee/documents/${note.id}", token, "PUT", payload)
    if (response.first == 410) throw NotesSyncRemoteDeletedException(note.id)
    if (response.first == 409) throw NotesSyncConflictException(note.id)
    if (response.first !in 200..299) throw IllegalStateException(syncError(response.first))
  }

  /** Restores encrypted cloud copies only after the authenticated owner has been resolved. */
  suspend fun downloadAll(ownerId: String): List<RecoveredDocument> = withContext(Dispatchers.IO) {
    val token = ensureOwner(ownerId)
    val response = request(ownerId, "/notes/e2ee/documents", token, "GET")
    if (response.first !in 200..299) throw IllegalStateException(syncError(response.first))
    val documents = JSONObject(response.second).optJSONArray("documents") ?: return@withContext emptyList()
    decodeDocuments(ownerId, documents)
  }

  /** Fetches the server winner after a rejected stale write. The caller must
   * preserve its local edit before replacing the local original. */
  suspend fun downloadOne(ownerId: String, noteId: String): RecoveredDocument? = withContext(Dispatchers.IO) {
    val token = ensureOwner(ownerId)
    val response = request(ownerId, "/notes/e2ee/documents/$noteId", token, "GET")
    if (response.first == 404 || response.first == 410) return@withContext null
    if (response.first !in 200..299) throw IllegalStateException(syncError(response.first))
    val envelope = JSONObject(response.second).put("note_id", noteId)
    decodeDocuments(ownerId, org.json.JSONArray().put(envelope)).firstOrNull()
  }

  suspend fun downloadTombstones(ownerId: String): List<RemoteTombstone> = withContext(Dispatchers.IO) {
    // No notes are restored until the device has been explicitly trusted.
    val token = ensureOwner(ownerId)
    val response = request(ownerId, "/notes/e2ee/tombstones", token, "GET")
    if (response.first !in 200..299) throw IllegalStateException(syncError(response.first))
    val tombstones = JSONObject(response.second).optJSONArray("tombstones") ?: return@withContext emptyList()
    buildList {
      for (index in 0 until tombstones.length()) {
        val item = tombstones.optJSONObject(index) ?: continue
        val id = item.optString("note_id").takeIf { it.isNotBlank() } ?: continue
        val deletedAt = item.optString("deleted_at").takeIf { it.isNotBlank() } ?: continue
        add(RemoteTombstone(id, item.optInt("document_revision", 1).coerceAtLeast(1), deletedAt))
      }
    }
  }

  suspend fun downloadSyncPage(ownerId: String, cursor: String): NotesSyncPage = withContext(Dispatchers.IO) {
    val token = ensureOwner(ownerId)
    val response = request(ownerId, "/notes/e2ee/sync?cursor=${java.net.URLEncoder.encode(cursor, "UTF-8")}&limit=100", token, "GET")
    if (response.first !in 200..299) throw IllegalStateException(syncError(response.first))
    val value = JSONObject(response.second)
    val documents = decodeDocuments(ownerId, value.optJSONArray("documents") ?: org.json.JSONArray())
    val tombstones = value.optJSONArray("tombstones") ?: org.json.JSONArray()
    val deleted = buildList { for (i in 0 until tombstones.length()) {
      val item = tombstones.getJSONObject(i)
      add(RemoteTombstone(item.getString("note_id"), item.optInt("document_revision", 1), item.getString("deleted_at")))
    } }
    NotesSyncPage(documents, deleted, value.get("next_cursor").toString(), value.optBoolean("has_more"))
  }

  private suspend fun ensureOwner(ownerId: String): String {
    val token = tokens.getTokenForOwner(ownerId)
    if (validatedOwner != ownerId) { devices.ensureActive(ownerId); validatedOwner = ownerId }
    check(tokens.isCurrentSession(ownerId, token)) { "Сессия владельца изменилась" }
    return token
  }

  private fun decodeDocuments(ownerId: String, documents: org.json.JSONArray): List<RecoveredDocument> {
    val crypto = pro.lurg.notes.security.E2eeV2Crypto(context, ownerId)
    return buildList {
      for (index in 0 until documents.length()) {
        val document = documents.optJSONObject(index) ?: continue
        if (document.optString("algorithm") != "AES-256-GCM" || document.optInt("format_version", 2) != 2 || document.optInt("key_version") != 2) continue
        val noteId = document.optString("note_id").takeIf { it.isNotBlank() } ?: continue
        val plain = crypto.decryptDocument(ownerId, noteId, E2eeEnvelope(
            nonce = document.getString("nonce"), ciphertext = document.getString("ciphertext")
          )).toString(Charsets.UTF_8)
        val value = JSONObject(plain)
        val note = NoteEntity(
          id = noteId,
          ownerId = ownerId,
          title = value.optString("title"),
          markdown = value.optString("markdown"),
          revision = document.optInt("document_revision", 1).coerceAtLeast(1),
          createdAt = document.optString("created_at", value.optString("updated_at")),
          updatedAt = document.optString("updated_at", value.optString("updated_at")),
          syncState = "synced",
          dirty = false,
          serverRevision = document.optInt("document_revision", 1).coerceAtLeast(1)
        )
        val blocks = value.optJSONArray("blocks")?.let { source -> buildList {
          for (i in 0 until source.length()) {
            val block = source.optJSONObject(i) ?: continue
            add(NoteBlockEntity(noteId, block.optString("id"), block.optString("parent_id").takeIf { it.isNotBlank() && it != "null" },
              block.optString("position", "${(i + 1) * 1000}.0000000000"), block.optString("type", "paragraph"),
              block.optJSONObject("content")?.toString() ?: "{}", block.optJSONObject("attrs")?.toString() ?: "{}",
              block.optString("origin", "user"), block.optInt("revision", note.revision), block.optString("updated_at").takeIf { it.isNotBlank() && it != "null" }))
          }
        }}.orEmpty()
        add(RecoveredDocument(note, blocks))
      }
    }
  }

  suspend fun delete(ownerId: String, noteId: String, baseRevision: Int = 0) = withContext(Dispatchers.IO) {
    val token = ensureOwner(ownerId)
    val response = request(ownerId, "/notes/e2ee/documents/$noteId/delete", token, "POST", JSONObject().put("base_document_revision", baseRevision).toString())
    if (response.first == 409) throw NotesSyncConflictException(noteId)
    // A local-only note has no cloud row; in either case the tombstone is now satisfied.
    if (response.first != 404 && response.first !in 200..299) throw IllegalStateException(syncError(response.first))
  }

  private fun request(ownerId: String, path: String, token: String, method: String, json: String? = null): Pair<Int, String> {
    check(tokens.isCurrentSession(ownerId, token)) { "Сессия владельца изменилась" }
    val builder = Request.Builder().url(pro.lurg.notes.BuildConfig.API_BASE_URL + path).addHeader("Authorization", "Bearer $token")
    val headers = devices.deviceHeaders(ownerId)
    builder.addHeader("X-Lurg-Notes-Device-Id", headers.first).addHeader("X-Lurg-Notes-Device-Credential", headers.second)
    when (method) {
      "GET" -> builder.get()
      "PUT" -> builder.put(requireNotNull(json).toRequestBody(JSON))
      "DELETE" -> builder.delete()
      else -> builder.post(requireNotNull(json).toRequestBody(JSON))
    }
    http.newCall(builder.build()).execute().use {
      val result = it.code to (it.body?.string().orEmpty())
      check(tokens.isCurrentSession(ownerId, token)) { "Сессия владельца изменилась" }
      return result
    }
  }
  private fun syncError(code: Int): String = when (code) {
    401 -> "Сессия Lurg ID истекла. Войдите снова и повторите синхронизацию."
    403 -> "Нет доступа к синхронизации этой заметки."
    409 -> "Заметка изменилась в другом месте. Откройте её снова и повторите синхронизацию."
    in 500..599 -> "Сервис синхронизации временно недоступен. Заметка сохранена на устройстве — повторите позже."
    else -> "Не удалось синхронизировать заметку. Она сохранена на устройстве — повторите позже."
  }

  private companion object { val JSON = "application/json; charset=utf-8".toMediaType() }
}

data class RecoveredDocument(val note: NoteEntity, val blocks: List<NoteBlockEntity>)
data class RemoteTombstone(val noteId: String, val revision: Int, val deletedAt: String)

/** A 409 is not retried blindly: the worker creates a local conflict copy. */
class NotesSyncConflictException(val noteId: String) : IllegalStateException("Заметка изменилась в другом месте")
class NotesSyncRemoteDeletedException(val noteId: String) : IllegalStateException("Заметка удалена на другом устройстве")

data class NotesSyncPage(val documents: List<RecoveredDocument>, val tombstones: List<RemoteTombstone>, val nextCursor: String, val hasMore: Boolean)
