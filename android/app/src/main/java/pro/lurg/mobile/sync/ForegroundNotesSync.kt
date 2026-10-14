package pro.lurg.notes.sync

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** Poll only while a screen is resumed; resuming/reconnecting also pulls immediately. */
class ForegroundNotesSync(
  private val scope: CoroutineScope,
  private val hasSession: () -> Boolean,
  private val isOnline: () -> Boolean,
  private val requestSync: () -> Unit,
  private val intervalMs: Long = 30_000L,
) {
  @Volatile private var foreground = false
  private var polling: Job? = null

  @Synchronized fun setForeground(active: Boolean) {
    if (foreground == active) return
    foreground = active
    polling?.cancel()
    polling = if (active) scope.launch {
      while (isActive) {
        requestIfEligible()
        delay(intervalMs)
      }
    } else null
  }

  fun networkAvailable() {
    scope.launch { requestIfEligible() }
  }

  private fun requestIfEligible() {
    if (foreground && hasSession() && isOnline()) requestSync()
  }
}
