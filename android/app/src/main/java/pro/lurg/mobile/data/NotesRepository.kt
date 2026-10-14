package pro.lurg.notes.data

import android.content.Context
import androidx.room.withTransaction
import kotlinx.coroutines.flow.Flow
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

class NotesRepository(
  private val context: Context,
  private val dao: NotesDao,
  private val database: LurgNotesDatabase
) {
  fun observeNotes(ownerId: String): Flow<List<NoteEntity>> = dao.observeNotes(ownerId)

  suspend fun importLegacyIfNeeded(): ImportResult {
    return LegacyNotesImporter(context, dao).importIfNeeded()
  }

  suspend fun note(id: String, ownerId: String): NoteEntity? = dao.note(id, ownerId)

  /**
   * Converts historical Markdown exactly once, then the editor reads blocks
   * directly. Markdown remains a compatibility projection for encrypted
   * backups and older application versions.
   */
  suspend fun document(note: NoteEntity): List<DocumentBlock> {
    val existing = dao.blocks(note.id)
    val existingBlocks = existing.map { it.toDocumentBlock().normalizeLegacyType() }
    // Native v1 had a note_blocks table too, but it stored Markdown lines as
    // paragraph blocks. Treat those rows as legacy rather than mistaking them
    // for the v2 document source of truth.
    val reparseLegacyRows = existingBlocks.any { it.isLegacyMarkdownRow() }
    val base = if (existing.isNotEmpty() && !reparseLegacyRows) existingBlocks else markdownToBlocks(note.markdown)
    // v1 stored the title beside Markdown. Preserve it as real document
    // content when the old body did not already contain it.
    val migrated = if (note.title.isNotBlank() && base.none { it.text.trim().equals(note.title.trim(), ignoreCase = true) }) {
      listOf(DocumentBlock(UUID.randomUUID().toString(), "500.0000000000", "heading1", note.title)) + base
    } else base
    if (existing.isNotEmpty() && !reparseLegacyRows && migrated == base) return base
    saveDocument(note.id, requireNotNull(note.ownerId), migrated, createOperation = false)
    return migrated
  }

  suspend fun createNote(ownerId: String): NoteEntity {
    val now = Instant.now().toString()
    val note = NoteEntity(
      id = UUID.randomUUID().toString(),
      ownerId = ownerId,
      title = "",
      markdown = "",
      revision = 1,
      createdAt = now,
      updatedAt = now,
      syncState = "local",
      dirty = true
    )
    database.withTransaction {
      dao.upsertNote(note)
      dao.upsertBlocks(
        listOf(
        NoteBlockEntity(
          noteId = note.id,
          id = UUID.randomUUID().toString(),
          parentId = null,
          position = "1000.0000000000",
          type = "paragraph",
          contentJson = """{"text":""}""",
          attrsJson = "{}",
          origin = "user",
          revision = 1,
          updatedAt = now
        )
        )
      )
    }
    return note
  }

  suspend fun saveMarkdown(noteId: String, ownerId: String, markdown: String) {
    database.withTransaction {
      val current = dao.note(noteId, ownerId) ?: return@withTransaction
      val now = Instant.now().toString()
      val title = titleFromMarkdown(markdown)
      val next = current.copy(
        title = title,
        markdown = markdown,
        revision = current.revision + 1,
        updatedAt = now,
        syncState = if (current.syncState == "local") "local" else "dirty",
        dirty = true
      )
      dao.upsertNote(next)
      dao.upsertOperation(
        NoteOperationEntity(
          noteId = noteId,
          clientOperationId = UUID.randomUUID().toString(),
          baseRevision = current.revision,
          operationType = "update_note",
          payloadJson = """{"title":${json(title)},"markdown":${json(markdown)}}""",
          createdAt = now
        )
      )
    }
  }

  /** Local-only, transactional autosave. No network request is on this path. */
  suspend fun saveDocument(
    noteId: String,
    ownerId: String,
    blocks: List<DocumentBlock>,
    createOperation: Boolean = false
  ) {
    val safeBlocks = blocks.ifEmpty { listOf(newParagraph(position = "1000.0000000000")) }
    val now = Instant.now().toString()
    // Room's transaction keeps the projection, blocks and queue in one durable
    // commit. A force-stop after this call therefore cannot split a document.
    database.withTransaction {
      val current = dao.note(noteId, ownerId) ?: return@withTransaction
      val markdown = blocksToMarkdown(safeBlocks)
      val title = titleFromBlocks(safeBlocks).ifBlank { titleFromMarkdown(markdown) }
      val existing = dao.blocks(noteId).associateBy { it.id }
      val unchanged = current.markdown == markdown && current.title == title && existing.size == safeBlocks.size && safeBlocks.all { block ->
        existing[block.id]?.let { it.copy(revision = 0, updatedAt = null) == block.toEntity(noteId, 0, now).copy(updatedAt = null) } == true
      }
      if (unchanged) return@withTransaction
      val next = current.copy(
        title = title,
        markdown = markdown,
        revision = current.revision + 1,
        updatedAt = now,
        syncState = if (current.syncState == "local") "local" else "dirty",
        dirty = true
      )
      dao.upsertNote(next)
      val ids = safeBlocks.map { it.id }.toSet()
      existing.keys.filterNot { it in ids }.forEach { dao.deleteBlock(noteId, it) }
      val entities = safeBlocks.map { it.toEntity(noteId, next.revision, now) }
      dao.upsertBlocks(entities.filter { block -> existing[block.id]?.let { it.copy(revision = 0, updatedAt = null) != block.copy(revision = 0, updatedAt = null) } ?: true })
      if (createOperation) entities.forEach { block ->
        dao.upsertOperation(NoteOperationEntity(
          noteId = noteId, clientOperationId = UUID.randomUUID().toString(),
          baseRevision = current.revision, operationType = "upsert_block",
          payloadJson = JSONObject().put("block", block.toWireJson()).toString(), createdAt = now
        ))
      }
    }
  }

  fun newParagraph(position: String, text: String = ""): DocumentBlock = DocumentBlock(
    id = UUID.randomUUID().toString(), position = position, type = "paragraph", text = text
  )

  suspend fun deleteNote(noteId: String, ownerId: String) {
    database.withTransaction {
      val note = dao.note(noteId, ownerId) ?: return@withTransaction
      dao.deleteBlocks(noteId)
      dao.clearOperations(noteId)
      dao.deleteNote(noteId, ownerId)
      dao.upsertDeletedNote(DeletedNoteEntity(noteId, ownerId, Instant.now().toString(), note.serverRevision))
    }
  }

  suspend fun clearLocalData() {
    dao.clearAllOperations()
    dao.clearAllDeletedNotes()
    dao.clearAllCryptoMigrations()
    dao.clearAllBlocks()
    dao.clearAllNotes()
    context.getSharedPreferences("lurg_native_migration", Context.MODE_PRIVATE).edit().clear().apply()
  }

  private fun json(value: String): String {
    return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\""
  }

  private fun titleFromMarkdown(markdown: String): String {
    val lines = markdown.lines()
    var index = 0
    while (index < lines.size) {
      val first = lines[index].trim()
      when {
        first.isBlank() -> Unit
        first.startsWith("|") && index + 1 < lines.size && lines[index + 1].contains("---") -> return "Таблица"
        first.startsWith("```") -> return "Код"
        first.startsWith("![") -> return "Изображение"
        first.startsWith("[Видео]") -> return "Видео"
        first.startsWith("[PDF]") -> return "PDF"
        else -> {
          val title = first
            .replace(Regex("^#{1,6}\\s+"), "")
            .replace(Regex("^\\[[ xX]\\]\\s*"), "")
            .replace(Regex("^[-*]\\s+\\[[ xX]\\]\\s+"), "")
            .replace(Regex("^[-*]\\s+"), "")
            .replace(Regex("^\\d+\\.\\s+"), "")
            .replace(Regex("^>\\s+"), "")
            .replace(Regex("!\\[(.*?)]\\((.*?)\\)"), "$1")
            .replace(Regex("\\[(.*?)]\\((.*?)\\)"), "$1")
            .replace("**", "")
            .replace("__", "")
            .replace("~~", "")
            .replace("`", "")
            .trim()
          if (title.isNotBlank()) return title.take(80)
        }
      }
      index += 1
    }
    return ""
  }

  private fun titleFromBlocks(blocks: List<DocumentBlock>): String = blocks
    .asSequence()
    .filter { it.type != "image" && it.type != "divider" }
    .map { it.text.trim() }
    .firstOrNull { it.isNotBlank() }
    ?.take(80)
    .orEmpty()

  companion object {
    fun markdownToBlocks(markdown: String): List<DocumentBlock> {
    val lines = markdown.lines()
    val result = mutableListOf<DocumentBlock>()
    var index = 0
    fun add(type: String, text: String = "", checked: Boolean = false, uri: String? = null) {
      result += DocumentBlock(UUID.randomUUID().toString(), "${(result.size + 1) * 1000}.0000000000", type, text, checked, uri)
    }
    while (index < lines.size) {
      val line = lines[index]
      val trimmed = line.trim()
      when {
        trimmed.startsWith("|") && index + 1 < lines.size && lines[index + 1].contains("---") -> {
          val table = mutableListOf<String>()
          while (index < lines.size && lines[index].trim().startsWith("|")) table += lines[index++]
          add("table", table.joinToString("\n")); index -= 1
        }
        trimmed.startsWith("```") -> {
          val code = mutableListOf<String>(); index += 1
          while (index < lines.size && !lines[index].trim().startsWith("```")) code += lines[index++]
          add("code", code.joinToString("\n"))
        }
        trimmed == "---" || trimmed == "***" -> add("divider")
        trimmed.startsWith("![") && trimmed.contains("](") -> add("image", uri = trimmed.substringAfter("](").substringBeforeLast(")"))
        trimmed.startsWith("[PDF]") -> add("file", uri = trimmed.substringAfter("](").substringBeforeLast(")"))
        trimmed.startsWith("### ") -> add("heading3", trimmed.removePrefix("### "))
        trimmed.startsWith("## ") -> add("heading2", trimmed.removePrefix("## "))
        trimmed.startsWith("# ") -> add("heading1", trimmed.removePrefix("# "))
        trimmed.startsWith("> ") -> add("quote", trimmed.removePrefix("> "))
        trimmed.startsWith("- [x] ", true) -> add("checklist", trimmed.drop(6), true)
        trimmed.startsWith("- [ ] ") -> add("checklist", trimmed.drop(6), false)
        trimmed.startsWith("- ") || trimmed.startsWith("* ") -> add("bullet", trimmed.drop(2))
        Regex("^\\d+\\.\\s+").containsMatchIn(trimmed) -> add("numbered", trimmed.substringAfter(". "))
        trimmed.isNotBlank() -> add("paragraph", line)
      }
      index += 1
    }
    return result.ifEmpty { listOf(DocumentBlock(UUID.randomUUID().toString(), "1000.0000000000", "paragraph")) }
  }


    fun blocksToMarkdown(blocks: List<DocumentBlock>): String {
      var number = 0
      return blocks.joinToString("\n") { block ->
      if (block.type == "numbered") number++ else number = 0
      when (block.type) {
        "heading1" -> "# ${block.text}"
        "heading2" -> "## ${block.text}"
        "heading3" -> "### ${block.text}"
        "bullet" -> "- ${block.text}"
        "numbered" -> "$number. ${block.text}"
        "checklist" -> "- [${if (block.checked) "x" else " "}] ${block.text}"
        "quote" -> block.text.lineSequence().joinToString("\n") { "> $it" }
        "code" -> "```\n${block.text}\n```"
        "divider" -> "---"
        "image" -> "![Изображение](${block.uri.orEmpty()})"
        "file" -> "[PDF](${block.uri.orEmpty()})"
        else -> block.text
      }
    }
    }
  }
}

private fun NoteBlockEntity.toDocumentBlock(): DocumentBlock {
  val content = runCatching { JSONObject(contentJson) }.getOrDefault(JSONObject())
  val attrs = runCatching { JSONObject(attrsJson) }.getOrDefault(JSONObject())
  return DocumentBlock(id, position, type, content.optString("text"), content.optBoolean("checked"),
    content.optString("uri").takeIf { it.isNotBlank() }, attrs.optInt("width").takeIf { it > 0 }, attrs.optInt("height").takeIf { it > 0 }, origin)
}

private fun DocumentBlock.normalizeLegacyType(): DocumentBlock = when (type) {
  "heading" -> copy(type = "heading2")
  "attachment" -> copy(type = "file")
  else -> this
}

private fun DocumentBlock.isLegacyMarkdownRow(): Boolean {
  if (type != "paragraph") return false
  val value = text.trimStart()
  return value.startsWith("|") || value.startsWith("```") || value.startsWith("# ") ||
    value.startsWith("## ") || value.startsWith("### ") || value.startsWith("> ") ||
    value.startsWith("- [") || value.startsWith("![") || value.startsWith("[PDF")
}

private fun DocumentBlock.toEntity(noteId: String, revision: Int, now: String): NoteBlockEntity = NoteBlockEntity(
  noteId, id, null, position, type,
  JSONObject().put("text", text).put("checked", checked).apply { uri?.let { put("uri", it) } }.toString(),
  JSONObject().apply { width?.let { put("width", it) }; height?.let { put("height", it) } }.toString(),
  origin, revision, now
)

private fun NoteBlockEntity.toWireJson(): JSONObject = JSONObject().put("id", id).put("parent_id", parentId)
  .put("position", position).put("type", type).put("content", JSONObject(contentJson))
  .put("attrs", JSONObject(attrsJson)).put("origin", origin).put("revision", revision)
