package pro.lurg.notes

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.core.app.ActivityScenario
import androidx.room.Room
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.ExistingWorkPolicy
import androidx.work.WorkInfo
import pro.lurg.notes.sync.NotesSyncWorker
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.json.JSONObject
import pro.lurg.notes.api.*
import pro.lurg.notes.data.*
import pro.lurg.notes.security.*
import java.io.File
import java.util.UUID

@org.junit.FixMethodOrder(org.junit.runners.MethodSorters.NAME_ASCENDING)
@RunWith(AndroidJUnit4::class)
class NativeNotesSmokeTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun aOwnerKeysSurviveLogoutWithoutCrossAccountExposure() {
        val store = SecureTokenStore(context)
        val a = UUID.randomUUID().toString(); val b = UUID.randomUUID().toString()
        store.bindOwner(a); store.setSession("test-access", "test-refresh")
        val cryptoA = E2eeV2Crypto(context, a)
        val originalDevice = cryptoA.deviceId()
        val key = ByteArray(32) { it.toByte() }; cryptoA.saveUmk(key)
        store.setProtectedValue("device_credential", "synthetic-device-credential")
        store.clear()
        assertNull(store.getToken()); assertNull(store.ownerId())
        store.bindOwner(b)
        assertFalse(E2eeV2Crypto(context, b).hasUmk())
        assertNull(store.getProtectedValue("device_credential"))
        store.bindOwner(a)
        assertEquals(originalDevice, E2eeV2Crypto(context, a).deviceId())
        assertEquals("synthetic-device-credential", store.getProtectedValue("device_credential"))
        E2eeV2Crypto(context, a).withUmk { assertArrayEquals(key, it) }
        store.clear()
    }

    @Test fun cOwnerSnapshotsAndCursorWritesStayIsolatedDuringAccountSwitch(): Unit = runBlocking {
        val store = SecureTokenStore(context)
        val otherStore = SecureTokenStore(context)
        val a = UUID.randomUUID().toString(); val b = UUID.randomUUID().toString()
        store.setSession("owner-a-token", "owner-a-refresh", a)
        assertEquals("owner-a-token", otherStore.getTokenForOwner(a))
        store.setSession("owner-b-token", "owner-b-refresh", b)
        store.setProtectedValue("sync_cursor", "7", b)
        // A completed old pull may retain its own cursor, never B's namespace.
        otherStore.setProtectedValue("sync_cursor", "900", a)
        assertEquals("7", store.getProtectedValue("sync_cursor", b))
        assertFalse(otherStore.isCurrentSession(a, "owner-a-token"))
        try { otherStore.getTokenForOwner(a); fail("Previous owner's token accepted") } catch (_: IllegalStateException) { }
        try { EncryptedNotesSyncClient(context).downloadSyncPage(a, "0"); fail("Previous owner's sync accepted") } catch (_: IllegalStateException) { }
        try { NotesAiClient(context).runAction(a, UUID.randomUUID().toString(), "summarize", "Owner A private text", "", "", 1); fail("Previous owner's AI text accepted") } catch (_: IllegalStateException) { }
        try { NotesGraphClient(context).extract(a, UUID.randomUUID().toString(), "Private A", "Owner A private text", 1); fail("Previous owner's Graph text accepted") } catch (_: IllegalStateException) { }
        val errors = java.util.concurrent.ConcurrentLinkedQueue<String>()
        val writer = Thread { repeat(100) { store.setSession("owner-a-token", "owner-a-refresh", a); store.setSession("owner-b-token", "owner-b-refresh", b) } }
        val reader = Thread { repeat(200) {
            try { if (otherStore.getTokenForOwner(a) != "owner-a-token") errors.add("mixed-session") } catch (_: IllegalStateException) { }
        } }
        writer.start(); reader.start(); writer.join(); reader.join()
        assertTrue(errors.isEmpty())
        store.clear()
    }

    @Test fun bDurableDocumentOrdersBlocksAndKeepsEditsAfterInflightAck(): Unit = runBlocking {
        val name = "native-smoke-${UUID.randomUUID()}.db"
        var db = Room.databaseBuilder(context, LurgNotesDatabase::class.java, name).build()
        val owner = UUID.randomUUID().toString()
        val repo = NotesRepository(context, db.notesDao(), db)
        val note = repo.createNote(owner)
        val blocks = (1..12).map { DocumentBlock("block-$it", "${it * 1000}.0000000000", "paragraph", "Row $it") }
        repo.saveDocument(note.id, owner, blocks)
        val before = db.notesDao().note(note.id, owner)!!
        assertEquals((1..12).map { "block-$it" }, db.notesDao().blocks(note.id).map { it.id })
        repo.saveDocument(note.id, owner, blocks)
        assertEquals(before.revision, db.notesDao().note(note.id, owner)!!.revision)
        val unchanged = db.notesDao().blocks(note.id)[1]
        repo.saveDocument(note.id, owner, blocks.mapIndexed { index, block -> if (index == 0) block.copy(text = "Offline changed") else block })
        assertEquals(unchanged, db.notesDao().blocks(note.id)[1])
        db.notesDao().markSynced(note.id, owner, before.revision, before.revision)
        val stillDirty = db.notesDao().note(note.id, owner)!!
        assertTrue(stillDirty.dirty); assertEquals(before.revision, stillDirty.serverRevision)
        assertNull(db.notesDao().note(note.id, UUID.randomUUID().toString()))
        db.close()
        db = Room.databaseBuilder(context, LurgNotesDatabase::class.java, name).build()
        assertEquals("Offline changed", JSONObject(db.notesDao().blocks(note.id)[0].contentJson).getString("text"))
        val final = db.notesDao().note(note.id, owner)!!
        db.notesDao().markSynced(note.id, owner, final.revision, final.revision + 5)
        assertEquals(final.revision + 5, db.notesDao().note(note.id, owner)!!.revision)
        db.close(); context.deleteDatabase(name)
    }

    @Test fun yDisposableForegroundRemotePullAndResume(): Unit = runBlocking {
        val credentialsFile = File(context.filesDir, "audit-credentials.json")
        Assume.assumeTrue(credentialsFile.exists())
        check(BuildConfig.DEBUG && BuildConfig.API_BASE_URL.startsWith("http://127.0.0.1:"))
        val credentials = JSONObject(credentialsFile.readText())
        val owner = AuthClient(context).login(credentials.getString("email"), credentials.getString("password")).id
        val e2ee = E2eeDeviceClient(context)
        if (!E2eeV2Crypto(context, owner).hasUmk()) e2ee.bootstrapInitial(owner) else e2ee.ensureActive(owner)
        context.getSharedPreferences("lurg_notes_flags", 0).edit().putBoolean("onboarding_completed", true).commit()
        val app = context.applicationContext as LurgNotesApplication
        val manager = WorkManager.getInstance(context)
        val sync = EncryptedNotesSyncClient(context)
        if (android.os.Build.VERSION.SDK_INT >= 33) InstrumentationRegistry.getInstrumentation().uiAutomation.grantRuntimePermission(context.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
        fun remote(id: String, text: String): NoteEntity {
            val now = java.time.Instant.now().toString()
            return NoteEntity(id, owner, text, text, 1, now, now, "local", true, 0)
        }
        suspend fun drained() = withTimeout(30000) {
            while (manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().any { !it.state.isFinished }) delay(200)
        }
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            drained(); delay(2000)
            val id = UUID.randomUUID().toString()
            val start = android.os.SystemClock.elapsedRealtime()
            sync.upload(owner, remote(id, "Remote note without editing or restarting"), emptyList())
            assertNull(app.database.notesDao().note(id, owner))
            withTimeout(45000) { while (app.database.notesDao().note(id, owner) == null) delay(200) }
            assertEquals("Remote note without editing or restarting", app.database.notesDao().note(id, owner)!!.markdown)
            println("NOTES_REMOTE_RECEIVE_MS=" + (android.os.SystemClock.elapsedRealtime() - start))
            scenario.moveToState(androidx.lifecycle.Lifecycle.State.CREATED)
            drained()
            val paused = UUID.randomUUID().toString()
            sync.upload(owner, remote(paused, "Remote note while paused"), emptyList())
            delay(2000)
            assertNull(app.database.notesDao().note(paused, owner))
            val resume = android.os.SystemClock.elapsedRealtime()
            scenario.moveToState(androidx.lifecycle.Lifecycle.State.RESUMED)
            withTimeout(15000) { while (app.database.notesDao().note(paused, owner) == null) delay(200) }
            println("NOTES_RESUME_RECEIVE_MS=" + (android.os.SystemClock.elapsedRealtime() - resume))
        } finally { scenario.close() }
    }

    @Test fun zDisposableCloudCasPaginationAndDeleteReconciliation(): Unit = runBlocking {
        // Credentials are provisioned in this debug app's private files by the
        // test runner. They never appear in command arguments or test output.
        val credentialsFile = File(context.filesDir, "audit-credentials.json")
        Assume.assumeTrue(credentialsFile.exists())
        check(BuildConfig.DEBUG && BuildConfig.API_BASE_URL.startsWith("http://127.0.0.1:"))
        val credentials = JSONObject(credentialsFile.readText())
        val owner = AuthClient(context).login(credentials.getString("email"), credentials.getString("password")).id
        if (!E2eeV2Crypto(context, owner).hasUmk()) E2eeDeviceClient(context).bootstrapInitial(owner) else E2eeDeviceClient(context).ensureActive(owner)
        val sync = EncryptedNotesSyncClient(context)
        val now = "2026-10-03T12:00:00Z"
        fun note(id: String, revision: Int = 1, base: Int = 0, text: String = "Disposable native test") = NoteEntity(id, owner, text, text, revision, now, now, "local", true, base)
        val id = UUID.randomUUID().toString()
        sync.upload(owner, note(id), emptyList())
        sync.upload(owner, note(id), emptyList()) // fresh nonce, same hash + revision: idempotent retry
        sync.upload(owner, note(id, 2, 1, "Server winner"), emptyList())
        try { sync.upload(owner, note(id, 2, 1, "Stale loser"), emptyList()); fail("Stale write accepted") } catch (_: NotesSyncConflictException) { }
        assertEquals("Server winner", sync.downloadOne(owner, id)!!.note.markdown)
        try { sync.delete(owner, id, 1); fail("Stale delete accepted") } catch (_: NotesSyncConflictException) { }
        val ids = (1..105).map { UUID.randomUUID().toString() }
        ids.forEach { sync.upload(owner, note(it), emptyList()) }
        sync.delete(owner, id, 2)
        var cursor = "0"; var pages = 0; val found = mutableSetOf<String>(); val deleted = mutableSetOf<String>()
        do {
            val page = sync.downloadSyncPage(owner, cursor)
            found += page.documents.map { it.note.id }; deleted += page.tombstones.map { it.noteId }
            assertFalse(page.documents.any { it.note.id == id })
            if (page.hasMore) assertNotEquals(cursor, page.nextCursor)
            cursor = page.nextCursor; pages += 1
        } while (page.hasMore)
        assertTrue(pages >= 2); assertTrue(found.containsAll(ids)); assertTrue(id in deleted)
        assertNull(sync.downloadOne(owner, id))
        // Seed the production-shaped Room data and cached owner for UI/restart
        // testing. This is an isolated disposable server account.
        val app = context.applicationContext as LurgNotesApplication
        val repo = NotesRepository(context, app.database.notesDao(), app.database)
        val local = repo.createNote(owner)
        repo.saveDocument(local.id, owner, listOf(DocumentBlock(UUID.randomUUID().toString(), "1000", "paragraph", "Offline restart smoke")))
        val work = OneTimeWorkRequestBuilder<NotesSyncWorker>().build()
        val manager = WorkManager.getInstance(context)
        manager.enqueueUniqueWork("native-notes-smoke", ExistingWorkPolicy.REPLACE, work).result.get()
        val finished = withTimeout(60_000) {
            var info = manager.getWorkInfoById(work.id).get()
            while (!info.state.isFinished) { delay(250); info = manager.getWorkInfoById(work.id).get() }
            info
        }
        assertEquals(WorkInfo.State.SUCCEEDED, finished.state)
        assertFalse(app.database.notesDao().note(local.id, owner)!!.dirty)
        assertNull(app.database.notesDao().note(id, owner))
        assertTrue(ids.all { app.database.notesDao().note(it, owner) != null })
        // Reproduce a tail edit while a unique worker is already running.
        // APPEND_OR_REPLACE must retain one debounced follow-up; KEEP loses it.
        SmokeBlockingWorker.reset()
        val gate = OneTimeWorkRequestBuilder<SmokeBlockingWorker>().build()
        manager.enqueueUniqueWork("lurg_notes_immediate_sync", ExistingWorkPolicy.REPLACE, gate).result.get()
        withTimeout(15_000) { SmokeBlockingWorker.started.await() }
        val tail = repo.createNote(owner)
        repo.saveDocument(tail.id, owner, listOf(DocumentBlock(UUID.randomUUID().toString(), "1000", "paragraph", "Tail edit while worker is running")))
        app.requestNotesSync(); delay(200); app.requestNotesSync(); delay(200); app.requestNotesSync()
        val chain = withTimeout(15_000) {
            var jobs = manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().filter { !it.state.isFinished }
            while (jobs.size < 2) { delay(100); jobs = manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().filter { !it.state.isFinished } }
            jobs
        }
        assertEquals(2, chain.size)
        assertEquals(WorkInfo.State.RUNNING, manager.getWorkInfoById(gate.id).get().state)
        assertTrue(chain.any { it.id != gate.id && it.state == WorkInfo.State.BLOCKED })
        SmokeBlockingWorker.release.complete(Unit)
        withTimeout(60_000) {
            while (app.database.notesDao().note(tail.id, owner)!!.dirty) delay(200)
        }
        assertEquals("Tail edit while worker is running", sync.downloadOne(owner, tail.id)!!.note.markdown)
        context.getSharedPreferences("lurg_notes_flags", 0).edit().putBoolean("onboarding_completed", true).commit()
        // A server-only change must reach Room while the user leaves Notes
        // open, without another edit, login, or app restart to trigger a pull.
        if (android.os.Build.VERSION.SDK_INT >= 33) InstrumentationRegistry.getInstrumentation().uiAutomation.grantRuntimePermission(context.packageName, android.Manifest.permission.POST_NOTIFICATIONS)
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            withTimeout(30000) {
                while (manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().any { !it.state.isFinished }) delay(200)
            }
            delay(2000) // Let the activity's initial account activation finish.
            val remoteId = UUID.randomUUID().toString()
            val receiveStarted = android.os.SystemClock.elapsedRealtime()
            sync.upload(owner, note(remoteId, text = "Remote change while Notes is open"), emptyList())
            assertNull(app.database.notesDao().note(remoteId, owner))
            withTimeout(45000) {
                while (app.database.notesDao().note(remoteId, owner) == null) delay(200)
            }
            assertEquals("Remote change while Notes is open", app.database.notesDao().note(remoteId, owner)!!.markdown)
            println("NOTES_REMOTE_RECEIVE_MS=" + (android.os.SystemClock.elapsedRealtime() - receiveStarted))
            // Pausing stops the foreground loop; resuming pulls immediately.
            scenario.moveToState(androidx.lifecycle.Lifecycle.State.CREATED)
            withTimeout(30000) {
                while (manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().any { !it.state.isFinished }) delay(200)
            }
            val pausedId = UUID.randomUUID().toString()
            sync.upload(owner, note(pausedId, text = "Remote change while Notes is paused"), emptyList())
            delay(2000)
            assertNull(app.database.notesDao().note(pausedId, owner))
            val resumedAt = android.os.SystemClock.elapsedRealtime()
            scenario.moveToState(androidx.lifecycle.Lifecycle.State.RESUMED)
            withTimeout(15000) { while (app.database.notesDao().note(pausedId, owner) == null) delay(200) }
            assertEquals("Remote change while Notes is paused", app.database.notesDao().note(pausedId, owner)!!.markdown)
            println("NOTES_RESUME_RECEIVE_MS=" + (android.os.SystemClock.elapsedRealtime() - resumedAt))
        } finally { scenario.close() }
        credentialsFile.delete()
    }
}
