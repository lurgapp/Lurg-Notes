package pro.lurg.notes.data

import android.content.Context
import androidx.core.content.edit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.UUID

class LegacyNotesImporter(
  private val context: Context,
  private val dao: NotesDao
) {
  suspend fun importIfNeeded(): ImportResult = withContext(Dispatchers.IO) {
    val prefs = context.getSharedPreferences("lurg_native_migration", Context.MODE_PRIVATE)
    if (prefs.getBoolean("json_v1_imported", false)) return@withContext ImportResult.AlreadyDone

    val legacy = File(context.filesDir, "lurg-notes-store-v1.json")
    if (!legacy.exists()) {
      seedIfEmpty()
      prefs.edit { putBoolean("json_v1_imported", true) }
      return@withContext ImportResult.NoLegacyFile
    }

    val backup = File(context.filesDir, "lurg-notes-store-v1.json.bak")
    if (!backup.exists()) legacy.copyTo(backup, overwrite = false)

    val parsed = JSONObject(legacy.readText())
    val notes = parsed.optJSONObject("notes") ?: JSONObject()
    val blocks = parsed.optJSONObject("blocks") ?: JSONObject()
    val operations = parsed.optJSONObject("operations") ?: JSONObject()
    var imported = 0

    val ids = notes.keys()
    while (ids.hasNext()) {
      val noteId = ids.next()
      val noteJson = notes.optJSONObject(noteId) ?: continue
      val blockArray = blocks.optJSONArray(noteId)
      val markdown = buildString {
        if (blockArray != null) {
          for (i in 0 until blockArray.length()) {
            val block = blockArray.optJSONObject(i) ?: continue
            val type = block.optString("type", "paragraph")
            val content = block.optJSONObject("content")
            val text = content?.optString("text", "") ?: ""
            if (isNotEmpty()) append("\n")
            append(blockToMarkdown(type, text))
          }
        }
      }
      dao.upsertNote(
        NoteEntity(
          id = noteId,
          title = noteJson.optString("title", ""),
          markdown = markdown,
          revision = noteJson.optInt("revision", 1),
          createdAt = noteJson.optString("created_at", Instant.now().toString()),
          updatedAt = noteJson.optString("updated_at", Instant.now().toString()),
          syncState = noteJson.optString("sync_state", "local"),
          dirty = noteJson.optBoolean("dirty", true)
        )
      )
      val roomBlocks = mutableListOf<NoteBlockEntity>()
      if (blockArray != null) {
        for (i in 0 until blockArray.length()) {
          val block = blockArray.optJSONObject(i) ?: continue
          roomBlocks += NoteBlockEntity(
            noteId = noteId,
            id = block.optString("id", UUID.randomUUID().toString()),
            parentId = block.optString("parent_id").takeUnless { it == "null" || it.isBlank() },
            position = block.optString("position", "${(i + 1) * 1000}.0000000000"),
            type = block.optString("type", "paragraph"),
            contentJson = block.optJSONObject("content")?.toString() ?: "{}",
            attrsJson = block.optJSONObject("attrs")?.toString() ?: "{}",
            origin = block.optString("origin", "user"),
            revision = block.optInt("revision", 1),
            updatedAt = block.optString("updated_at").takeUnless { it == "null" || it.isBlank() }
          )
        }
      }
      dao.upsertBlocks(roomBlocks)

      val opArray = operations.optJSONArray(noteId)
      if (opArray != null) {
        for (i in 0 until opArray.length()) {
          val op = opArray.optJSONObject(i) ?: continue
          dao.upsertOperation(
            NoteOperationEntity(
              noteId = noteId,
              clientOperationId = op.optString("client_operation_id", UUID.randomUUID().toString()),
              baseRevision = op.optInt("base_revision", 1),
              operationType = op.optString("operation_type", "update_note"),
              payloadJson = op.optJSONObject("payload")?.toString() ?: "{}",
              createdAt = Instant.now().toString()
            )
          )
        }
      }
      imported += 1
    }
    prefs.edit { putBoolean("json_v1_imported", true) }
    ImportResult.Imported(imported)
  }

  private suspend fun seedIfEmpty() {
    if (dao.countNotes() > 0) return
    val now = Instant.now().toString()
    dao.upsertNote(
      NoteEntity(
        id = UUID.randomUUID().toString(),
        title = "Без названия",
        markdown = "",
        revision = 1,
        createdAt = now,
        updatedAt = now,
        syncState = "local",
        dirty = true
      )
    )
  }

  private fun blockToMarkdown(type: String, text: String): String = when (type) {
    "heading" -> "## $text"
    "checklist" -> "- [ ] $text"
    "quote" -> "> $text"
    "code" -> "```\n$text\n```"
    else -> text
  }
}

sealed interface ImportResult {
  data object AlreadyDone : ImportResult
  data object NoLegacyFile : ImportResult
  data class Imported(val count: Int) : ImportResult
}
