package pro.lurg.notes

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.CompletableDeferred

/** Debug-only gate to reproduce an edit arriving while unique work is running. */
class SmokeBlockingWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        started.complete(Unit)
        release.await()
        return Result.success()
    }
    companion object {
        var started = CompletableDeferred<Unit>()
        var release = CompletableDeferred<Unit>()
        fun reset() { started = CompletableDeferred(); release = CompletableDeferred() }
    }
}

/** Debug-only network failure with a long backoff for reconnect regression. */
class SmokeRetryWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result = Result.retry()
}
