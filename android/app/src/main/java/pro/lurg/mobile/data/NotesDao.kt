package pro.lurg.notes.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface NotesDao {
  @Query("SELECT * FROM notes WHERE ownerId = :ownerId ORDER BY updatedAt DESC")
  fun observeNotes(ownerId: String): Flow<List<NoteEntity>>

  @Query("SELECT * FROM notes WHERE id = :id AND ownerId = :ownerId")
  suspend fun note(id: String, ownerId: String): NoteEntity?

  @Query("SELECT * FROM notes WHERE ownerId = :ownerId AND dirty = 1 ORDER BY updatedAt ASC LIMIT :limit")
  suspend fun dirtyNotes(ownerId: String, limit: Int = 50): List<NoteEntity>

  @Query("SELECT * FROM notes WHERE ownerId = :ownerId ORDER BY updatedAt ASC LIMIT :limit")
  suspend fun notesForMigration(ownerId: String, limit: Int = 200): List<NoteEntity>

  @Query("SELECT * FROM note_crypto_migrations WHERE noteId=:noteId AND ownerId=:ownerId")
  suspend fun cryptoMigration(noteId: String, ownerId: String): NoteCryptoMigrationEntity?

  @Insert(onConflict = OnConflictStrategy.REPLACE)
  suspend fun upsertCryptoMigration(state: NoteCryptoMigrationEntity)

  @Query("UPDATE notes SET serverRevision = :serverRevision, revision = MAX(revision, :serverRevision), dirty = CASE WHEN revision = :revision THEN 0 ELSE dirty END, syncState = CASE WHEN revision = :revision THEN 'synced' ELSE syncState END WHERE id = :id AND ownerId = :ownerId")
  suspend fun markSynced(id: String, ownerId: String, revision: Int, serverRevision: Int = revision)

  @Query("SELECT * FROM note_blocks WHERE noteId = :noteId ORDER BY CAST(position AS REAL) ASC")
  suspend fun blocks(noteId: String): List<NoteBlockEntity>

  @Query("DELETE FROM note_blocks WHERE noteId = :noteId AND id = :blockId")
  suspend fun deleteBlock(noteId: String, blockId: String)

  @Query("SELECT * FROM note_operations WHERE noteId = :noteId ORDER BY createdAt ASC")
  suspend fun operations(noteId: String): List<NoteOperationEntity>

  @Insert(onConflict = OnConflictStrategy.REPLACE)
  suspend fun upsertNote(note: NoteEntity)

  @Insert(onConflict = OnConflictStrategy.REPLACE)
  suspend fun upsertBlocks(blocks: List<NoteBlockEntity>)

  @Insert(onConflict = OnConflictStrategy.REPLACE)
  suspend fun upsertOperation(operation: NoteOperationEntity)

  @Insert(onConflict = OnConflictStrategy.REPLACE)
  suspend fun upsertDeletedNote(note: DeletedNoteEntity)

  @Query("SELECT * FROM deleted_notes WHERE ownerId = :ownerId ORDER BY deletedAt ASC")
  suspend fun deletedNotes(ownerId: String): List<DeletedNoteEntity>

  @Query("DELETE FROM deleted_notes WHERE noteId = :noteId AND ownerId = :ownerId")
  suspend fun clearDeletedNote(noteId: String, ownerId: String)

  @Query("DELETE FROM note_blocks WHERE noteId = :noteId")
  suspend fun deleteBlocks(noteId: String)

  @Query("DELETE FROM notes WHERE id = :noteId AND ownerId = :ownerId")
  suspend fun deleteNote(noteId: String, ownerId: String)

  @Query("DELETE FROM note_operations WHERE noteId = :noteId")
  suspend fun clearOperations(noteId: String)

  @Query("DELETE FROM note_operations")
  suspend fun clearAllOperations()

  @Query("DELETE FROM deleted_notes")
  suspend fun clearAllDeletedNotes()

  @Query("DELETE FROM note_crypto_migrations")
  suspend fun clearAllCryptoMigrations()

  @Query("DELETE FROM note_blocks")
  suspend fun clearAllBlocks()

  @Query("DELETE FROM notes")
  suspend fun clearAllNotes()

  @Query("SELECT COUNT(*) FROM notes")
  suspend fun countNotes(): Int
}
