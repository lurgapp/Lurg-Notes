package pro.lurg.notes.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(tableName = "notes", indices = [Index("updatedAt"), Index("ownerId")])
data class NoteEntity(
  @PrimaryKey val id: String,
  /** Null denotes legacy on-device data kept in quarantine until restored from its owner's cloud. */
  val ownerId: String? = null,
  val title: String,
  val markdown: String,
  val revision: Int,
  val createdAt: String,
  val updatedAt: String,
  val syncState: String,
  val dirty: Boolean,
  val serverRevision: Int = 0
)

@Entity(tableName = "note_blocks", primaryKeys = ["noteId", "id"], indices = [Index("noteId")])
data class NoteBlockEntity(
  val noteId: String,
  val id: String,
  val parentId: String?,
  val position: String,
  val type: String,
  val contentJson: String,
  val attrsJson: String,
  val origin: String,
  val revision: Int,
  val updatedAt: String?
)

@Entity(tableName = "note_operations", primaryKeys = ["noteId", "clientOperationId"], indices = [Index("noteId")])
data class NoteOperationEntity(
  val noteId: String,
  val clientOperationId: String,
  val baseRevision: Int,
  val operationType: String,
  val payloadJson: String,
  val createdAt: String
)

/**
 * A local tombstone prevents a note deleted while offline from being restored by
 * the next cloud download before the DELETE reaches the server.
 */
@Entity(tableName = "deleted_notes", primaryKeys = ["noteId", "ownerId"], indices = [Index("ownerId")])
data class DeletedNoteEntity(
  val noteId: String,
  val ownerId: String,
  val deletedAt: String,
  val baseDocumentRevision: Int = 0
)

/** Durable acknowledgement ledger for the v1 -> v2 local re-encryption. */
@Entity(tableName = "note_crypto_migrations", primaryKeys = ["noteId", "ownerId"], indices = [Index("ownerId"), Index("state")])
data class NoteCryptoMigrationEntity(
  val noteId: String,
  val ownerId: String,
  val sourceVersion: Int,
  val targetVersion: Int,
  val revision: Int,
  val state: String,
  val updatedAt: String
)

/**
 * The editor's source of truth.  `position` is deliberately a fractional
 * string rather than a list index: inserting a block never requires rewriting
 * every following block and leaves room for a future CRDT/LCP implementation.
 */
data class DocumentBlock(
  val id: String,
  val position: String,
  val type: String,
  val text: String = "",
  val checked: Boolean = false,
  val uri: String? = null,
  val width: Int? = null,
  val height: Int? = null,
  val origin: String = "user"
)
