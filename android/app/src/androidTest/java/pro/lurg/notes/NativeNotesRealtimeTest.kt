package pro.lurg.notes

import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkInfo
import androidx.work.WorkManager
import kotlinx.coroutines.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import pro.lurg.notes.api.AuthClient
import pro.lurg.notes.api.E2eeDeviceClient
import pro.lurg.notes.api.E2eeSetupRequiredException
import pro.lurg.notes.data.*
import pro.lurg.notes.lcp.NotesRealtimeState
import pro.lurg.notes.security.E2eeV2Crypto
import pro.lurg.notes.security.SecureTokenStore
import pro.lurg.notes.api.EncryptedNotesSyncClient
import java.io.File
import java.util.UUID

/** Real encrypted HTTP writer -> PG outbox -> Redis -> LCP -> native HTTP/Room receiver. */
@RunWith(AndroidJUnit4::class)
class NativeNotesRealtimeTest {
  @Test fun foregroundInvalidationsPullEncryptedUpdatesAndDeletesAndResumeCatchUp(): Unit = runBlocking {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    check(BuildConfig.DEBUG && context.packageName == "pro.lurg.notes.uitest")
    check(BuildConfig.API_BASE_URL == "http://127.0.0.1:18001" && BuildConfig.LCP_WS_URL == "ws://127.0.0.1:18080/connect")
    val app = context.applicationContext as LurgNotesApplication
    val manager = WorkManager.getInstance(context)
    manager.cancelAllWork().result.get()
    val fixture = JSONObject(File(context.filesDir, "audit-credentials.json").readText())
    val owner = AuthClient(context).login(fixture.getString("email"), fixture.getString("password")).id
    val generatedIds = coroutineScope { List(20) { async(Dispatchers.Default) { E2eeV2Crypto(context, owner).deviceId() } }.awaitAll() }
    assertEquals("Concurrent settings and sync must share the same device identity", 1, generatedIds.distinct().size)
    context.getSharedPreferences("lurg_notes_flags", 0).edit().putBoolean("onboarding_completed", true).putBoolean("private_screen", false).commit()
    app.requestNotesSync(immediate = true) // real worker competes with manual first-device setup
    if (E2eeV2Crypto(context, owner).hasUmk()) E2eeDeviceClient(context).ensureActive(owner) else coroutineScope {
      val backgroundRegistration = async { runCatching { E2eeDeviceClient(context).ensureActive(owner) } }
      val firstSetup = async { runCatching { E2eeDeviceClient(context).bootstrapInitial(owner) } }
      val secondSetup = async { runCatching { E2eeDeviceClient(context).bootstrapInitial(owner) } }
      val setups = listOf(firstSetup.await(), secondSetup.await())
      assertEquals("Only one concurrent setup may create a recovery secret", 1, setups.count { it.isSuccess })
      assertEquals("UMK уже настроен", setups.single { it.isFailure }.exceptionOrNull()?.message)
      backgroundRegistration.await().exceptionOrNull()?.let { check(it is E2eeSetupRequiredException) { "Concurrent sync registration failed" } }
      assertTrue(E2eeV2Crypto(context, owner).hasUmk())
      E2eeDeviceClient(context).ensureActive(owner)
    }
    app.requestNotesSync(immediate = true) // replace any setup-required retry with the now-ready keys
    val writer = EncryptedNotesSyncClient(context)
    val dao = app.database.notesDao()
    val now = "2026-10-10T12:00:00Z"
    fun remote(id: String, text: String) = NoteEntity(id, owner, text, text, 1, now, now, "local", true, 0)
    suspend fun drained() { withTimeout(15000) { while (manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().any { !it.state.isFinished }) delay(50) } }
    val scenario = ActivityScenario.launch(MainActivity::class.java)
    try {
      withTimeout(12000) { while (app.realtimeSync.state.value != NotesRealtimeState.Connected) delay(50) }
      delay(300); drained()
      val id = UUID.randomUUID().toString()
      assertNull(dao.note(id, owner))
      val started = android.os.SystemClock.elapsedRealtime()
      writer.upload(owner, remote(id, "Encrypted realtime fixture"), emptyList())
      withTimeout(8000) { while (dao.note(id, owner) == null) delay(50) }
      println("NOTES_LCP_NATIVE_UPDATE_MS=" + (android.os.SystemClock.elapsedRealtime() - started))
      assertEquals("Encrypted realtime fixture", dao.note(id, owner)!!.markdown)
      drained()
      val deleted = android.os.SystemClock.elapsedRealtime()
      writer.delete(owner, id, 1)
      withTimeout(8000) { while (dao.note(id, owner) != null) delay(50) }
      println("NOTES_LCP_NATIVE_DELETE_MS=" + (android.os.SystemClock.elapsedRealtime() - deleted))
      drained()
      scenario.moveToState(androidx.lifecycle.Lifecycle.State.STARTED)
      withTimeout(4000) { while (app.realtimeSync.state.value != NotesRealtimeState.Offline) delay(50) }
      val pausedId = UUID.randomUUID().toString()
      writer.upload(owner, remote(pausedId, "Encrypted background fixture"), emptyList())
      delay(1000); assertNull(dao.note(pausedId, owner))
      val resumed = android.os.SystemClock.elapsedRealtime()
      scenario.moveToState(androidx.lifecycle.Lifecycle.State.RESUMED)
      withTimeout(8000) { while (dao.note(pausedId, owner) == null) delay(50) }
      println("NOTES_LCP_NATIVE_RESUME_MS=" + (android.os.SystemClock.elapsedRealtime() - resumed))
      assertEquals("Encrypted background fixture", dao.note(pausedId, owner)!!.markdown)
      app.realtimeSync.setSessionAllowed(false)
      withTimeout(4000) { while (app.realtimeSync.state.value != NotesRealtimeState.Offline) delay(50) }
    } finally {
      scenario.close()
      app.realtimeSync.setSessionAllowed(false)
      app.cancelNotesSync()
    }
  }
}
