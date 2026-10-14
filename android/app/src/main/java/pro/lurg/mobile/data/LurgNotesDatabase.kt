package pro.lurg.notes.data

import androidx.room.Database
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.room.RoomDatabase

@Database(
  entities = [NoteEntity::class, NoteBlockEntity::class, NoteOperationEntity::class, DeletedNoteEntity::class, NoteCryptoMigrationEntity::class],
  version = 5,
  exportSchema = false
)
abstract class LurgNotesDatabase : RoomDatabase() {
  abstract fun notesDao(): NotesDao

  companion object {
    val MIGRATION_4_5 = object : Migration(4, 5) {
      override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE notes ADD COLUMN serverRevision INTEGER NOT NULL DEFAULT 0")
        db.execSQL("ALTER TABLE deleted_notes ADD COLUMN baseDocumentRevision INTEGER NOT NULL DEFAULT 0")
        // A v2 acknowledged clean row already has a known server revision.
        db.execSQL("UPDATE notes SET serverRevision=revision WHERE dirty=0 AND EXISTS (SELECT 1 FROM note_crypto_migrations m WHERE m.noteId=notes.id AND m.ownerId=notes.ownerId AND m.state='acknowledged')")
        // Re-encryption of old rows is queued once by this schema migration,
        // rather than uploading every document before every normal sync.
        db.execSQL("UPDATE notes SET dirty=1,syncState='dirty' WHERE ownerId IS NOT NULL AND serverRevision=0")
      }
    }
    /**
     * Existing rows are deliberately left ownerless.  Assigning them to whoever
     * happens to be signed in would reproduce the account-leak incident.
     */
    val MIGRATION_1_2 = object : Migration(1, 2) {
      override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE notes ADD COLUMN ownerId TEXT")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_notes_ownerId ON notes(ownerId)")
      }
    }

    val MIGRATION_2_3 = object : Migration(2, 3) {
      override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS deleted_notes (noteId TEXT NOT NULL, ownerId TEXT NOT NULL, deletedAt TEXT NOT NULL, PRIMARY KEY(noteId, ownerId))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_deleted_notes_ownerId ON deleted_notes(ownerId)")
      }
    }

    val MIGRATION_3_4 = object : Migration(3, 4) {
      override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("CREATE TABLE IF NOT EXISTS note_crypto_migrations (noteId TEXT NOT NULL, ownerId TEXT NOT NULL, sourceVersion INTEGER NOT NULL, targetVersion INTEGER NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL, updatedAt TEXT NOT NULL, PRIMARY KEY(noteId, ownerId))")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_note_crypto_migrations_ownerId ON note_crypto_migrations(ownerId)")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_note_crypto_migrations_state ON note_crypto_migrations(state)")
      }
    }
  }
}
