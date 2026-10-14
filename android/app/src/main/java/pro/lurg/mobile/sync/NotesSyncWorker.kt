package pro.lurg.notes.sync

import android.content.Context
import android.util.Log
import androidx.room.withTransaction
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import pro.lurg.notes.data.LurgNotesDatabase
import pro.lurg.notes.api.AuthClient
import pro.lurg.notes.api.EncryptedNotesSyncClient
import pro.lurg.notes.api.NotesSyncConflictException
import pro.lurg.notes.api.NotesSyncRemoteDeletedException
import pro.lurg.notes.api.E2eeSetupRequiredException
import pro.lurg.notes.api.E2eeEnrollmentPendingException
import pro.lurg.notes.LurgNotesApplication
import kotlinx.coroutines.CancellationException
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import pro.lurg.notes.security.SecureTokenStore

class NotesSyncWorker(
  context: Context,
  params: WorkerParameters
) : CoroutineWorker(context, params) {
  override suspend fun doWork(): Result = syncLock.withLock { runSync() }

  private suspend fun runSync(): Result {
    val db = (applicationContext as LurgNotesApplication).database
    val status = (applicationContext as LurgNotesApplication).syncStatus
    return try {
      val owner = AuthClient(applicationContext).me() ?: return Result.success()
      val tokens = SecureTokenStore(applicationContext)
      val sync = EncryptedNotesSyncClient(applicationContext)
      val dao = db.notesDao()
      status.value = "Синхронизация…"
      dao.deletedNotes(owner.id).forEach { deleted ->
        try { sync.delete(owner.id, deleted.noteId, deleted.baseDocumentRevision) }
        catch (_: NotesSyncConflictException) {
          val winner = sync.downloadOne(owner.id, deleted.noteId) ?: throw IllegalStateException("Версия удаления недоступна")
          val id = UUID.nameUUIDFromBytes((deleted.noteId + deleted.deletedAt).toByteArray()).toString()
          db.withTransaction {
            dao.upsertNote(winner.note.copy(id = id, title = winner.note.title + " (конфликт удаления)", revision = 1, serverRevision = 0, dirty = true, syncState = "local"))
            dao.upsertBlocks(winner.blocks.map { it.copy(noteId = id, revision = 1) })
          }
          sync.delete(owner.id, deleted.noteId, winner.note.serverRevision)
        }
        dao.clearDeletedNote(deleted.noteId, owner.id)
      }
      var cursor = tokens.getProtectedValue("sync_cursor", owner.id) ?: "0"
      do {
        val page = sync.downloadSyncPage(owner.id, cursor)
        page.tombstones.forEach { applyRemoteTombstone(db, owner.id, it.noteId) }
        val pendingDeleteIds = dao.deletedNotes(owner.id).map { it.noteId }.toSet()
        for (remote in page.documents) {
          if (remote.note.id in pendingDeleteIds) continue
          db.withTransaction {
            val local = dao.note(remote.note.id, owner.id)
            if (local == null || !local.dirty) {
              if (local == null || remote.note.serverRevision >= local.serverRevision) {
                dao.upsertNote(remote.note); dao.deleteBlocks(remote.note.id); dao.upsertBlocks(remote.blocks)
              }
            } else if (sameContent(local, dao.blocks(local.id), remote)) {
              dao.markSynced(local.id, owner.id, local.revision, remote.note.serverRevision)
            } else if (remote.note.serverRevision > local.serverRevision) {
              preserveLocalCopy(dao, local, " (конфликт)")
              dao.upsertNote(remote.note); dao.deleteBlocks(remote.note.id); dao.upsertBlocks(remote.blocks)
            }
          }
        }
        check(tokens.ownerId() == owner.id) { "Владелец изменился" }
        check(!page.hasMore || page.nextCursor != cursor) { "Sync cursor did not advance" }
        cursor = page.nextCursor
        tokens.setProtectedValue("sync_cursor", cursor, owner.id)
      } while (page.hasMore)
      var batches = 0
      do {
        val dirty = dao.dirtyNotes(owner.id)
        if (dirty.isEmpty()) break
        for (note in dirty) {
          try {
            sync.upload(owner.id, note, dao.blocks(note.id))
            dao.markSynced(note.id, owner.id, note.revision)
          } catch (_: NotesSyncConflictException) { preserveConflictCopy(db, owner.id, note, sync)
          } catch (_: NotesSyncRemoteDeletedException) { preserveDeletedConflictCopy(db, owner.id, note) }
        }
        batches += 1
      } while (batches < 10)
      val pending = dao.dirtyNotes(owner.id, 1).isNotEmpty()
      status.value = if (pending) "Синхронизация продолжается…" else "Синхронизировано"
      if (pending) Result.retry() else Result.success()
    } catch (error: CancellationException) { throw error
    } catch (_: E2eeSetupRequiredException) {
      status.value = "Настройте E2EE в профиле — заметки сохранены локально"; Result.success()
    } catch (_: E2eeEnrollmentPendingException) {
      status.value = "Одобрите устройство — заметки сохранены локально"; Result.success()
    } catch (_: Exception) {
      status.value = "Нет синхронизации — локальные изменения сохранены"; Log.e(TAG, "Encrypted notes sync failed"); Result.retry()
    }
  }

  private fun sameContent(local: pro.lurg.notes.data.NoteEntity, blocks: List<pro.lurg.notes.data.NoteBlockEntity>, remote: pro.lurg.notes.api.RecoveredDocument): Boolean =
    local.title == remote.note.title && local.markdown == remote.note.markdown &&
      blocks.map { it.copy(revision = 0, updatedAt = null) } == remote.blocks.map { it.copy(revision = 0, updatedAt = null) }

  private suspend fun preserveLocalCopy(dao: pro.lurg.notes.data.NotesDao, source: pro.lurg.notes.data.NoteEntity, suffix: String) {
    val id = UUID.randomUUID().toString(); val now = Instant.now().toString()
    dao.upsertNote(source.copy(id = id, title = source.title.ifBlank { "Без названия" } + suffix, revision = 1, serverRevision = 0, createdAt = now, updatedAt = now, dirty = true, syncState = "local"))
    dao.upsertBlocks(dao.blocks(source.id).map { it.copy(noteId = id, revision = 1, updatedAt = now) })
  }

  /**
   * A stale writer must never overwrite the server winner or silently lose its
   * own edit. Keep that edit under a fresh local note ID, then restore the
   * current server version at the original ID. The operation is transactional
   * so a force-stop leaves either the old local document or both safe copies.
   */
  private suspend fun preserveConflictCopy(
    db: LurgNotesDatabase,
    ownerId: String,
    stale: pro.lurg.notes.data.NoteEntity,
    sync: EncryptedNotesSyncClient,
  ) {
    val remote = sync.downloadOne(ownerId, stale.id)
      ?: throw IllegalStateException("Серверная версия конфликтующей заметки недоступна")
    val dao = db.notesDao()
    val now = Instant.now().toString()
    val copyId = UUID.randomUUID().toString()
    db.withTransaction {
      // Another worker/edit may have changed the original since its upload
      // snapshot. In that case do not replace anything; retry safely.
      val current = dao.note(stale.id, ownerId) ?: return@withTransaction
      if (current.revision != stale.revision || !current.dirty) return@withTransaction
      dao.upsertNote(current.copy(
        id = copyId,
        title = current.title.ifBlank { "Без названия" } + " (конфликт)",
        revision = 1,
        serverRevision = 0,
        createdAt = now,
        updatedAt = now,
        syncState = "local",
        dirty = true,
      ))
      dao.upsertBlocks(dao.blocks(stale.id).map { it.copy(noteId = copyId, revision = 1, updatedAt = now) })
      dao.upsertNote(remote.note)
      dao.deleteBlocks(stale.id)
      if (remote.blocks.isNotEmpty()) dao.upsertBlocks(remote.blocks)
    }
  }

  private suspend fun applyRemoteTombstone(db: LurgNotesDatabase, ownerId: String, noteId: String) {
    val dao = db.notesDao()
    db.withTransaction {
      val local = dao.note(noteId, ownerId) ?: return@withTransaction
      if (local.dirty) {
        preserveDeletedConflictCopyLocked(dao, local)
      }
      dao.deleteBlocks(noteId)
      dao.deleteNote(noteId, ownerId)
      dao.clearOperations(noteId)
      dao.clearDeletedNote(noteId, ownerId)
    }
  }

  private suspend fun preserveDeletedConflictCopy(db: LurgNotesDatabase, ownerId: String, stale: pro.lurg.notes.data.NoteEntity) {
    val dao = db.notesDao()
    db.withTransaction {
      val current = dao.note(stale.id, ownerId) ?: return@withTransaction
      if (current.revision != stale.revision || !current.dirty) return@withTransaction
      preserveDeletedConflictCopyLocked(dao, current)
      dao.deleteBlocks(stale.id)
      dao.deleteNote(stale.id, ownerId)
      dao.clearOperations(stale.id)
    }
  }

  private suspend fun preserveDeletedConflictCopyLocked(
    dao: pro.lurg.notes.data.NotesDao,
    source: pro.lurg.notes.data.NoteEntity,
  ) {
    val now = Instant.now().toString()
    val copyId = UUID.randomUUID().toString()
    dao.upsertNote(source.copy(
      id = copyId,
      title = source.title.ifBlank { "Без названия" } + " (конфликт после удаления)",
      revision = 1,
      serverRevision = 0,
      createdAt = now,
      updatedAt = now,
      syncState = "local",
      dirty = true,
    ))
    dao.upsertBlocks(dao.blocks(source.id).map { it.copy(noteId = copyId, revision = 1, updatedAt = now) })
  }

  private companion object { const val TAG = "LurgNotesSync"; val syncLock = Mutex() }
}
