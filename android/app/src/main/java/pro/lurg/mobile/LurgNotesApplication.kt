package pro.lurg.notes

import android.app.Application
import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Bundle
import androidx.room.Room
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.Constraints
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkInfo
import pro.lurg.notes.data.LurgNotesDatabase
import pro.lurg.notes.sync.NotesSyncWorker
import pro.lurg.notes.sync.ForegroundNotesSync
import pro.lurg.notes.security.SecureTokenStore
import pro.lurg.notes.lcp.NotesRealtimeSync
import pro.lurg.notes.update.NativeApkUpdater
import pro.lurg.notes.update.NativeUpdateConfig
import pro.lurg.notes.update.NativeUpdateHost
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

class LurgNotesApplication : Application(), NativeUpdateHost {
  override val nativeApkUpdater by lazy {
    NativeApkUpdater(this, NativeUpdateConfig(
      appId = "notes", packageName = "pro.lurg.notes", manifestUrl = "https://notes.lurg.pro/update.json",
      apkHost = "notes.lurg.pro", apkPathPrefix = "/download/", filePrefix = "lurg-notes",
      pinnedSignerSha256 = "053fe44fabe00e67309a277918606968f6225cf1c5a1eac31448f8c620b373c0",
      installedCode = BuildConfig.VERSION_CODE, installedName = BuildConfig.VERSION_NAME, minSdk = 23,
      updateChannelId = "lurg_notes_updates",
    ))
  }
  val motionEnabled = kotlinx.coroutines.flow.MutableStateFlow(true)
  val syncStatus = kotlinx.coroutines.flow.MutableStateFlow("Изменения сохраняются на устройстве")
  lateinit var realtimeSync: NotesRealtimeSync
    private set
  // SharedPreferences keeps listeners weakly; retain the session observer for the process lifetime.
  private lateinit var sessionListener: android.content.SharedPreferences.OnSharedPreferenceChangeListener
  private val syncScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
  private var scheduledSync: Job? = null
  private val enqueueLock = Mutex()
  @Volatile private var syncGeneration = 0L
  private var resumedActivities = 0
  lateinit var database: LurgNotesDatabase
    private set

  override fun onCreate() {
    super.onCreate()
    nativeApkUpdater
    fun refreshMotion() { motionEnabled.value = android.provider.Settings.Global.getFloat(contentResolver, android.provider.Settings.Global.ANIMATOR_DURATION_SCALE, 1f) != 0f }
    refreshMotion()
    contentResolver.registerContentObserver(android.provider.Settings.Global.getUriFor(android.provider.Settings.Global.ANIMATOR_DURATION_SCALE), false,
      object : android.database.ContentObserver(android.os.Handler(android.os.Looper.getMainLooper())) { override fun onChange(selfChange: Boolean) { refreshMotion() } })
    val notifications = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    if (android.os.Build.VERSION.SDK_INT >= 26) notifications.createNotificationChannel(
      NotificationChannel("lurg_notes_updates", "Обновления Lurg Notes", NotificationManager.IMPORTANCE_DEFAULT)
    )
    database = Room.databaseBuilder(this, LurgNotesDatabase::class.java, "lurg-notes.db")
      .addMigrations(LurgNotesDatabase.MIGRATION_1_2, LurgNotesDatabase.MIGRATION_2_3, LurgNotesDatabase.MIGRATION_3_4, LurgNotesDatabase.MIGRATION_4_5)
      .build()

    // Foreground LCP invalidations trigger immediate pulls. The periodic worker
    // remains a safety net for changes created while this app is closed.
    val sync = PeriodicWorkRequestBuilder<NotesSyncWorker>(15, TimeUnit.MINUTES)
      .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build()
    WorkManager.getInstance(this).enqueueUniquePeriodicWork(
      "lurg_notes_background_sync",
      ExistingPeriodicWorkPolicy.UPDATE,
      sync
    )
    val connectivity = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    val sessions = SecureTokenStore(this)
    realtimeSync = NotesRealtimeSync(syncScope, sessions::sessionSnapshot,
      isCurrent = { sessions.isCurrentSession(it.ownerId, it.accessToken) },
      isOnline = { connectivity.getNetworkCapabilities(connectivity.activeNetwork)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true },
      requestSync = { requestNotesSync(immediate = true) }, url = BuildConfig.LCP_WS_URL)
    sessionListener = android.content.SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
      if (key == "active_e2ee_owner" || key?.startsWith("token_") == true) realtimeSync.changed()
    }
    getSharedPreferences("lurg_secure_session", Context.MODE_PRIVATE).registerOnSharedPreferenceChangeListener(sessionListener)
    val foregroundSync = ForegroundNotesSync(syncScope,
      hasSession = { SecureTokenStore(this).let { !it.ownerId().isNullOrBlank() && it.getToken() != null } },
      isOnline = { connectivity.getNetworkCapabilities(connectivity.activeNetwork)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true },
      requestSync = { requestNotesSync(immediate = true) },
    )
    registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
      override fun onActivityResumed(activity: Activity) {
        resumedActivities += 1
        foregroundSync.setForeground(true)
        realtimeSync.setForeground(true)
      }
      override fun onActivityPaused(activity: Activity) {
        resumedActivities = (resumedActivities - 1).coerceAtLeast(0)
        if (resumedActivities == 0) { foregroundSync.setForeground(false); realtimeSync.setForeground(false) }
      }
      override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) = Unit
      override fun onActivityStarted(activity: Activity) = Unit
      override fun onActivityStopped(activity: Activity) = Unit
      override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
      override fun onActivityDestroyed(activity: Activity) = Unit
    })
    val callback = object : ConnectivityManager.NetworkCallback() {
      override fun onAvailable(network: Network) { foregroundSync.networkAvailable(); realtimeSync.changed() }
      override fun onCapabilitiesChanged(network: Network, capabilities: NetworkCapabilities) {
        if (capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) { foregroundSync.networkAvailable(); realtimeSync.changed() }
      }
      override fun onLost(network: Network) { realtimeSync.changed() }
    }
    if (android.os.Build.VERSION.SDK_INT >= 24) connectivity.registerDefaultNetworkCallback(callback)
    else connectivity.registerNetworkCallback(NetworkRequest.Builder().addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).build(), callback)
  }

  @Synchronized fun requestNotesSync(immediate: Boolean = false) {
    scheduledSync?.cancel()
    val generation = syncGeneration
    scheduledSync = syncScope.launch {
      if (!immediate) delay(1500)
      // A new request may cancel the debounce, but must not interrupt an
      // in-flight WorkManager transaction and race another queue read.
      withContext(NonCancellable + Dispatchers.IO) {
        enqueueLock.withLock {
          if (generation != syncGeneration) return@withLock
          val manager = WorkManager.getInstance(this@LurgNotesApplication)
          val active = manager.getWorkInfosForUniqueWork("lurg_notes_immediate_sync").get().filter { !it.state.isFinished }
          // Reconnecting/resuming must not wait behind a previous network
          // failure's exponential backoff. Never replace a running upload.
          val retryWaiting = immediate && active.none { it.state == WorkInfo.State.RUNNING } &&
            active.any { it.state == WorkInfo.State.ENQUEUED && it.runAttemptCount > 0 }
          // A waiting worker will read the newest Room state when it starts.
          // Keep one follow-up behind a running worker, never an edit-per-job chain.
          if (!retryWaiting && active.any { it.state != WorkInfo.State.RUNNING }) return@withLock
          if (generation != syncGeneration) return@withLock
          val work = OneTimeWorkRequestBuilder<NotesSyncWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
          manager.enqueueUniqueWork("lurg_notes_immediate_sync", if (retryWaiting) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.APPEND_OR_REPLACE, work).result.get()
        }
      }
    }
  }

  @Synchronized fun cancelNotesSync() {
    syncGeneration += 1
    scheduledSync?.cancel()
    syncScope.launch(Dispatchers.IO) {
      enqueueLock.withLock { WorkManager.getInstance(this@LurgNotesApplication).cancelUniqueWork("lurg_notes_immediate_sync").result.get() }
    }
  }
}
