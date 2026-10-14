package pro.lurg.notes.sync

import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import org.junit.Assert.*
import org.junit.Test

class ForegroundNotesSyncTest {
  @Test fun backgroundStopsPollingAndResumePullsImmediately() = runBlocking {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val pulls = Channel<Unit>(Channel.UNLIMITED)
    val controller = ForegroundNotesSync(scope, { true }, { true }, { pulls.trySend(Unit) }, 100)
    try {
      controller.setForeground(true)
      withTimeout(3000) { pulls.receive(); pulls.receive() }
      controller.setForeground(false)
      delay(150)
      while (pulls.tryReceive().isSuccess) { }
      delay(250)
      assertTrue("A background screen must not keep pulling", pulls.tryReceive().isFailure)
      controller.setForeground(true)
      withTimeout(3000) { pulls.receive() }
    } finally { scope.cancel() }
  }

  @Test fun loggedOutOfflineAndBackgroundReconnectDoNotPull() = runBlocking {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val signedIn = AtomicBoolean(false); val online = AtomicBoolean(true)
    val pulls = Channel<Unit>(Channel.UNLIMITED)
    val controller = ForegroundNotesSync(scope, signedIn::get, online::get, { pulls.trySend(Unit) }, 50)
    try {
      controller.setForeground(true)
      delay(150)
      assertTrue(pulls.tryReceive().isFailure)
      online.set(false); signedIn.set(true)
      controller.networkAvailable()
      delay(150)
      assertTrue(pulls.tryReceive().isFailure)
      online.set(true); controller.networkAvailable()
      withTimeout(3000) { pulls.receive() }
      controller.setForeground(false)
      delay(100)
      while (pulls.tryReceive().isSuccess) { }
      controller.networkAvailable()
      delay(150)
      assertTrue(pulls.tryReceive().isFailure)
    } finally { scope.cancel() }
  }

  @Test fun repeatedResumeKeepsOneLoopAndReconnectDoesNotWaitForTimer() = runBlocking {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val pulls = Channel<Unit>(Channel.UNLIMITED)
    val controller = ForegroundNotesSync(scope, { true }, { true }, { pulls.trySend(Unit) }, 5000)
    try {
      controller.setForeground(true)
      withTimeout(3000) { pulls.receive() }
      repeat(20) { controller.setForeground(true) }
      delay(150)
      assertTrue("Repeated lifecycle events started extra loops", pulls.tryReceive().isFailure)
      controller.networkAvailable()
      withTimeout(3000) { pulls.receive() }
      controller.setForeground(false); controller.setForeground(true)
      withTimeout(3000) { pulls.receive() }
    } finally { scope.cancel() }
  }
}
