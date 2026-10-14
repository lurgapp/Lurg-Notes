package pro.lurg.notes

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.BackoffPolicy
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.TimeUnit
import pro.lurg.notes.api.AuthClient
import pro.lurg.notes.sync.NotesSyncWorker

@RunWith(AndroidJUnit4::class)
class NativeSyncSchedulingTest {
  @Test fun backgroundSafetyNetRemainsFifteenMinutes(): Unit = runBlocking {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    val work = withTimeout(15000) {
      var jobs = WorkManager.getInstance(context).getWorkInfosForUniqueWork("lurg_notes_background_sync").get()
      while (jobs.isEmpty()) { delay(100); jobs = WorkManager.getInstance(context).getWorkInfosForUniqueWork("lurg_notes_background_sync").get() }
      jobs.single { !it.state.isFinished }
    }
    assertEquals(TimeUnit.MINUTES.toMillis(15), work.periodicityInfo!!.repeatIntervalMillis)
  }

  @Test fun immediateReconnectBypassesOldNetworkRetryBackoff(): Unit = runBlocking {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    val app = context.applicationContext as LurgNotesApplication
    val manager = WorkManager.getInstance(context)
    AuthClient(context).clear()
    app.cancelNotesSync()
    withTimeout(15000) { while (manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().any { !it.state.isFinished }) delay(100) }
    val retry = OneTimeWorkRequestBuilder<SmokeRetryWorker>()
      .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.MINUTES).build()
    manager.enqueueUniqueWork("lurg_notes_immediate_sync", ExistingWorkPolicy.REPLACE, retry).result.get()
    try {
      val waiting = withTimeout(15000) {
        var info = manager.getWorkInfoById(retry.id).get()
        while (info.state != WorkInfo.State.ENQUEUED || info.runAttemptCount == 0) { delay(100); info = manager.getWorkInfoById(retry.id).get() }
        info
      }
      assertTrue(waiting.nextScheduleTimeMillis > System.currentTimeMillis() + TimeUnit.MINUTES.toMillis(20))
      app.requestNotesSync(immediate = true)
      val replacement = withTimeout(15000) {
        var infos = manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get()
        while (infos.none { it.id != retry.id && it.tags.contains(NotesSyncWorker::class.java.name) && it.state == WorkInfo.State.SUCCEEDED }) {
          delay(100); infos = manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get()
        }
        infos
      }
      val previous = manager.getWorkInfoById(retry.id).get()
      assertTrue(previous == null || previous.state == WorkInfo.State.CANCELLED)
      assertEquals(1, replacement.count { it.id != retry.id && it.tags.contains(NotesSyncWorker::class.java.name) })
    } finally { app.cancelNotesSync() }
  }

  @Test fun repeatedEditsAndForegroundPollsRetainOnlyOneFollowUp(): Unit = runBlocking {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    val app = context.applicationContext as LurgNotesApplication
    val manager = WorkManager.getInstance(context)
    app.cancelNotesSync()
    withTimeout(15000) {
      while (manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().any { !it.state.isFinished }) delay(100)
    }
    SmokeBlockingWorker.reset()
    val gate = OneTimeWorkRequestBuilder<SmokeBlockingWorker>().build()
    manager.enqueueUniqueWork("lurg_notes_immediate_sync", ExistingWorkPolicy.REPLACE, gate).result.get()
    try {
      withTimeout(15000) { SmokeBlockingWorker.started.await() }
      app.requestNotesSync(immediate = true)
      withTimeout(15000) {
        while (manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().count { !it.state.isFinished } < 2) delay(100)
      }
      // These requests are deliberately further apart than the edit debounce.
      // APPEND alone would accumulate one worker for every interval.
      repeat(4) {
        app.requestNotesSync(); delay(1700)
        app.requestNotesSync(immediate = true); delay(200)
      }
      val active = manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().filter { !it.state.isFinished }
      assertEquals(2, active.size)
      assertEquals(WorkInfo.State.RUNNING, active.single { it.id == gate.id }.state)
      assertEquals(WorkInfo.State.BLOCKED, active.single { it.id != gate.id }.state)
    } finally {
      app.cancelNotesSync()
      withTimeout(15000) {
        while (manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().any { !it.state.isFinished }) delay(100)
      }
      SmokeBlockingWorker.release.complete(Unit)
    }
  }
}
