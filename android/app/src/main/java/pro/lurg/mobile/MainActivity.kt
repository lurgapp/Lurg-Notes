package pro.lurg.notes

import android.content.Intent
import android.content.ClipData
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.BackHandler
import androidx.activity.compose.PredictiveBackHandler
import androidx.activity.BackEventCompat
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.contract.ActivityResultContract
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.togetherWith
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredHeightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.VisualTransformation
import pro.lurg.notes.editor.InlineMarkdown
import pro.lurg.notes.editor.InlineMarkdownTransformation
import pro.lurg.notes.editor.InlineMarkdownText
import pro.lurg.notes.editor.MarkdownDocumentPreview
import pro.lurg.notes.editor.MarkdownTransforms
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.TextRange
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusEvent
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import coil.compose.AsyncImage
import coil.imageLoader
import coil.request.ImageRequest
import coil.decode.VideoFrameDecoder
import androidx.core.content.FileProvider
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.Lifecycle
import kotlinx.coroutines.Job
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import pro.lurg.notes.api.AuthClient
import pro.lurg.notes.api.AuthUser
import pro.lurg.notes.api.NotesAiClient
import pro.lurg.notes.api.NotesGraphCandidate
import pro.lurg.notes.api.NotesGraphClient
import pro.lurg.notes.api.PushRegistrationClient
import pro.lurg.notes.api.E2eeDeviceClient
import pro.lurg.notes.api.E2eeDeviceStatus
import pro.lurg.notes.data.NoteEntity
import pro.lurg.notes.data.DocumentBlock
import pro.lurg.notes.data.NotesRepository
import java.util.ArrayDeque
import java.io.File
import java.io.FileOutputStream
import java.util.Locale
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
  private val viewModel by viewModels<NotesViewModel>()
  private var installationJob: Job? = null
  private var openingConfirmation = false
  private val installationPermissionLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
    (application as LurgNotesApplication).nativeApkUpdater.resumeAfterPermission()
  }
  private val installationConfirmationLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
    openingConfirmation = false
    (application as LurgNotesApplication).nativeApkUpdater.installer.userActionActivityReturned()
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    handleAuthIntent(intent)
    lifecycleScope.launch {
      repeatOnLifecycle(Lifecycle.State.RESUMED) {
        (application as LurgNotesApplication).nativeApkUpdater.state.collect { state ->
          if (state.pendingConfirmation && !openingConfirmation) {
            val installer = (application as LurgNotesApplication).nativeApkUpdater.installer
            installer.takeConfirmationIntent()?.let { confirmation ->
              openingConfirmation = true
              try { installationConfirmationLauncher.launch(confirmation) }
              catch (cancelled: CancellationException) { throw cancelled }
              catch (_: Exception) { openingConfirmation = false; installer.userActionActivityReturned() }
            }
          } else if (state.installRequested && state.readyFile != null && !state.installing) installVerifiedUpdate()
        }
      }
    }
    window.statusBarColor = 0xFF050509.toInt()
    window.navigationBarColor = 0xFF050509.toInt()
    setContent {
      MaterialTheme {
        LurgNotesApp(viewModel)
      }
    }
  }

  override fun onNewIntent(intent: Intent) {
    super.onNewIntent(intent)
    setIntent(intent)
    handleAuthIntent(intent)
  }

  override fun onResume() {
    super.onResume()
    (application as LurgNotesApplication).nativeApkUpdater.resumeAfterPermission()
    (application as LurgNotesApplication).nativeApkUpdater.installer.setForeground(true)
  }

  override fun onPause() {
    (application as LurgNotesApplication).nativeApkUpdater.installer.setForeground(false)
    super.onPause()
  }

  private fun installVerifiedUpdate() {
    if (installationJob?.isActive == true) return
    val updater = (application as LurgNotesApplication).nativeApkUpdater
    installationJob = lifecycleScope.launch {
      val file = updater.installationFile() ?: return@launch
      if (!updater.state.value.installRequested || updater.state.value.readyFile != file.name) return@launch
      try {
        if (android.os.Build.VERSION.SDK_INT >= 26 && !packageManager.canRequestPackageInstalls()) {
          if (updater.installationPermissionDeferred()) {
            installationPermissionLauncher.launch(Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
          }
          return@launch
        }
        updater.installer.requestApprovedInstall()
      } catch (cancelled: CancellationException) { throw cancelled }
      catch (_: Exception) { updater.installationLaunchFailed() }
    }
  }

  override fun onStop() {
    // A delayed autosave must not be left pending when the user backgrounds the
    // app, switches tasks, or Android starts destroying this activity.
    viewModel.flushOpenDocument()
    super.onStop()
  }

  private fun handleAuthIntent(intent: Intent?) {
    val updateIntent = intent ?: return
    if (updateIntent.getStringExtra("app_id")?.let { it != "notes" } == true ||
      updateIntent.getStringExtra("package_name")?.let { it != BuildConfig.APPLICATION_ID } == true) return
    if (updateIntent.getStringExtra("type") == "app_update" || updateIntent.getBooleanExtra("open_update", false) || updateIntent.action == "pro.lurg.notes.UPDATE") {
      val version = updateIntent.getStringExtra("version_code")?.toIntOrNull()
      viewModel.openUpdateFromPush(version)
      updateIntent.removeExtra("open_update"); updateIntent.removeExtra("type"); updateIntent.action = Intent.ACTION_MAIN
    }
  }
}

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class NotesViewModel(app: android.app.Application) : AndroidViewModel(app) {
  private val repo = NotesRepository(app, (app as LurgNotesApplication).database.notesDao(), (app as LurgNotesApplication).database)
  private val activeOwnerId = MutableStateFlow<String?>(null)
  val notes: StateFlow<List<NoteEntity>> = activeOwnerId.flatMapLatest { ownerId ->
    ownerId?.let { repo.observeNotes(it) } ?: flowOf(emptyList())
  }
    .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
  var openedNote by mutableStateOf<NoteEntity?>(null)
    private set
  /** Only local Room data feeds this state; it is never gated on a request. */
  var openedBlocks by mutableStateOf<List<DocumentBlock>>(emptyList())
    private set
  var aiPreview by mutableStateOf<AiPreview?>(null)
    private set
  var aiError by mutableStateOf<String?>(null)
    private set
  var aiBusy by mutableStateOf(false)
    private set
  var graphCandidates by mutableStateOf<List<NotesGraphCandidate>?>(null)
    private set
  var graphBusy by mutableStateOf(false)
    private set
  var graphError by mutableStateOf<String?>(null)
    private set
  var graphAddedCount by mutableStateOf(0)
    private set
  var authChecked by mutableStateOf(false)
    private set
  var authUser by mutableStateOf<AuthUser?>(null)
    private set
  var authError by mutableStateOf<String?>(null)
    private set
  var profileOpen by mutableStateOf(false)
    private set
  var graphOpen by mutableStateOf(false)
    private set
  var selectedNote by mutableStateOf<NoteEntity?>(null)
    private set
  var archiveOpen by mutableStateOf(false)
    private set
  var pinnedIds by mutableStateOf<Set<String>>(emptySet())
    private set
  var archivedIds by mutableStateOf<Set<String>>(emptySet())
    private set
  private var saveJob: Job? = null
  private var aiJob: Job? = null
  private var aiRequestGeneration = 0L
  private var accountScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
  private var authCheckJob: Job? = null
  private var identityGeneration = 0L
  private var accountGeneration = 0L
  /** Latest in-memory version per note.  Never conflate different note IDs. */
  private val pendingDocumentWrites = mutableMapOf<String, List<DocumentBlock>>()
  private val documentSaveJobs = mutableMapOf<String, Job>()
  private val ai = NotesAiClient(app)
  private val graph = NotesGraphClient(app)
  private val auth = AuthClient(app)
  val updates = (app as LurgNotesApplication).nativeApkUpdater
  private val push = PushRegistrationClient(app)
  private val e2ee = E2eeDeviceClient(app)
  private val prefs = app.getSharedPreferences("lurg_notes_flags", android.content.Context.MODE_PRIVATE)
  var privateScreen by mutableStateOf(prefs.getBoolean("private_screen", false))
    private set
  fun syncNow() { (getApplication<android.app.Application>() as LurgNotesApplication).requestNotesSync(immediate = true) }
  fun togglePrivateScreen() { privateScreen = !privateScreen; prefs.edit().putBoolean("private_screen", privateScreen).apply() }
  var onboardingCompleted by mutableStateOf(prefs.getBoolean("onboarding_completed", false))
    private set
  var authBusy by mutableStateOf(false)
    private set
  var registrationEmail by mutableStateOf<String?>(null)
    private set
  var e2eeStatus by mutableStateOf<String?>(null)
    private set
  var recoverySecret by mutableStateOf<String?>(null)
    private set
  var e2eeDevices by mutableStateOf<List<E2eeDeviceStatus>>(emptyList())
    private set
  val e2eeCurrentDeviceId: String get() = e2ee.currentDeviceId()
  val syncStatus = (app as LurgNotesApplication).syncStatus

  init {
    pinnedIds = prefs.getStringSet("pinned", emptySet()) ?: emptySet()
    archivedIds = prefs.getStringSet("archived", emptySet()) ?: emptySet()
    checkAuth()
    viewModelScope.launch { withContext(Dispatchers.IO) { repo.importLegacyIfNeeded() } }
  }

  fun checkAuth() {
    authCheckJob?.cancel()
    val generation = ++identityGeneration
    authCheckJob = viewModelScope.launch {
      val cached = withContext(Dispatchers.IO) { auth.cachedUser() }
      if (generation != identityGeneration) return@launch
      if (cached != null) activateAccount(cached)
      authChecked = cached != null || !auth.hasToken()
      try {
        val verified = auth.me()
        if (generation != identityGeneration) return@launch
        if (verified == null) { clearAccountUi(); auth.clear() }
        activateAccount(verified)
      } catch (cancelled: CancellationException) { throw cancelled
      } catch (_: Exception) {
        if (generation == identityGeneration) authError = if (cached != null) "Нет сети: доступна локальная копия." else "Не удалось проверить Lurg ID. Повторите вход."
      } finally { if (generation == identityGeneration) authChecked = true }
    }
  }

  private fun clearAccountUi() {
    (getApplication<android.app.Application>() as LurgNotesApplication).realtimeSync.setSessionAllowed(false)
    accountGeneration += 1
    accountScope.cancel()
    accountScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    documentSaveJobs.values.forEach { it.cancel() }
    documentSaveJobs.clear(); pendingDocumentWrites.clear(); saveJob?.cancel()
    openedNote = null; openedBlocks = emptyList(); selectedNote = null; archiveOpen = false
    aiPreview = null; aiError = null; aiBusy = false
    graphCandidates = null; graphError = null; graphBusy = false; graphAddedCount = 0
    recoverySecret = null; e2eeDevices = emptyList(); e2eeStatus = null
    profileOpen = false; graphOpen = false
    authUser = null; activeOwnerId.value = null
  }

  override fun onCleared() { accountScope.cancel(); super.onCleared() }

  fun login(email: String, password: String) {
    if (email.isBlank() || password.isBlank()) {
      authError = "Введите почту и пароль Lurg ID"
      return
    }
    authError = null
    authBusy = true
    authChecked = false
    identityGeneration += 1; authCheckJob?.cancel()
    viewModelScope.launch {
      runCatching { auth.login(email, password) }
        .onSuccess { activateAccount(it) }
        .onFailure { if (it is CancellationException) throw it; authError = it.message ?: "Не удалось войти" }
      authBusy = false
      authChecked = true
    }
  }

  fun requestRegistration(name: String, email: String, password: String) {
    if (name.isBlank() || email.isBlank() || password.length < 8) {
      authError = "Укажите имя, почту и пароль не короче 8 символов"
      return
    }
    authError = null
    authBusy = true
    viewModelScope.launch {
      runCatching { auth.requestRegistration(name, email, password) }
        .onSuccess { registrationEmail = email.trim() }
        .onFailure { if (it is CancellationException) throw it; authError = it.message ?: "Не удалось начать регистрацию" }
      authBusy = false
    }
  }

  fun confirmRegistration(code: String) {
    val email = registrationEmail ?: return
    if (!code.matches(Regex("^\\d{6}$"))) {
      authError = "Введите шестизначный код из письма"
      return
    }
    authError = null
    authBusy = true
    authChecked = false
    identityGeneration += 1; authCheckJob?.cancel()
    viewModelScope.launch {
      runCatching { auth.confirmRegistration(email, code) }
        .onSuccess {
          activateAccount(it)
          registrationEmail = null
        }
        .onFailure { if (it is CancellationException) throw it; authError = it.message ?: "Не удалось подтвердить регистрацию" }
      authBusy = false
      authChecked = true
    }
  }

  fun cancelRegistration() {
    registrationEmail = null
    authError = null
  }

  fun completeOnboarding() {
    prefs.edit().putBoolean("onboarding_completed", true).apply()
    onboardingCompleted = true
  }

  fun registerPushToken() {
    accountScope.launch { runCatching { push.registerCurrentToken() } }
  }

  fun openProfile() {
    profileOpen = true
    refreshE2eeDevices()
  }

  fun closeProfile() {
    profileOpen = false
    recoverySecret = null
  }

  fun setupE2ee() {
    e2eeStatus = "Настраиваем E2EE…"
    accountScope.launch {
      runCatching { e2ee.bootstrapInitial(requireOwnerId()) }
        .onSuccess { material ->
          recoverySecret = material.secret
          e2eeStatus = "Сохраните recovery secret вне приложения. После этого синхронизация включена."
          refreshE2eeDevices()
          (getApplication<android.app.Application>() as LurgNotesApplication).requestNotesSync()
        }
        .onFailure { if (it is CancellationException) throw it; e2eeStatus = it.message ?: "Не удалось настроить E2EE" }
    }
  }

  fun refreshE2eeDevices() {
    if (authUser == null) return
    accountScope.launch {
      runCatching { e2ee.pendingDevices() }
        .onSuccess { e2eeDevices = it }
        .onFailure { if (it is CancellationException) throw it; if (e2eeStatus == null) e2eeStatus = "E2EE ещё не настроен или устройство ожидает одобрения." }
    }
  }

  fun approveE2eeDevice(device: E2eeDeviceStatus) {
    accountScope.launch {
      runCatching { e2ee.approve(device, requireOwnerId()) }
        .onSuccess { e2eeStatus = "Устройство ${device.name} одобрено."; refreshE2eeDevices() }
        .onFailure { if (it is CancellationException) throw it; e2eeStatus = it.message ?: "Не удалось одобрить устройство" }
    }
  }

  fun revokeE2eeDevice(device: E2eeDeviceStatus) {
    accountScope.launch {
      runCatching { e2ee.revoke(device.id) }
        .onSuccess { e2eeStatus = "Устройство ${device.name} отозвано."; refreshE2eeDevices() }
        .onFailure { if (it is CancellationException) throw it; e2eeStatus = it.message ?: "Не удалось отозвать устройство" }
    }
  }

  fun recoverE2ee(recoverySecret: String) {
    if (recoverySecret.isBlank()) { e2eeStatus = "Введите сохранённый recovery secret."; return }
    e2eeStatus = "Восстанавливаем доступ E2EE…"
    accountScope.launch {
      runCatching { e2ee.recover(requireOwnerId(), recoverySecret.trim()) }
        .onSuccess { e2eeStatus = "UMK восстановлен локально, устройство доверено."; refreshE2eeDevices(); (getApplication<android.app.Application>() as LurgNotesApplication).requestNotesSync() }
        .onFailure { if (it is CancellationException) throw it; e2eeStatus = it.message ?: "Recovery не удался" }
    }
  }

  fun openGraph() {
    graphOpen = true
  }

  fun closeGraph() { graphOpen = false }

  fun checkForUpdates() {
    updates.checkForUpdate()
  }

  fun openUpdateFromPush(version: Int? = null) = updates.onPushTap(version)

  fun downloadUpdate() {
    updates.approve()
  }

  fun logout() {
    (getApplication<android.app.Application>() as LurgNotesApplication).realtimeSync.setSessionAllowed(false)
    identityGeneration += 1; authCheckJob?.cancel()
    authChecked = false
    viewModelScope.launch {
      pendingDocumentWrites.keys.toList().forEach { flushDocument(it) }
      (getApplication<android.app.Application>() as LurgNotesApplication).cancelNotesSync()
      clearAccountUi()
      withContext(Dispatchers.IO) { push.deactivate(); auth.logout() }
      prefs.edit().remove("pinned").remove("archived").apply()
      pinnedIds = emptySet(); archivedIds = emptySet(); authChecked = true
    }
  }

  fun selectNote(note: NoteEntity) {
    selectedNote = note
  }

  fun clearSelection() {
    selectedNote = null
  }

  fun openArchive() {
    archiveOpen = true
  }

  fun closeArchive() {
    archiveOpen = false
    selectedNote = null
  }

  fun deleteSelectedNote() {
    val note = selectedNote ?: return
    accountScope.launch {
      repo.deleteNote(note.id, requireOwnerId())
      (getApplication<android.app.Application>() as LurgNotesApplication).requestNotesSync()
      pinnedIds = pinnedIds - note.id
      archivedIds = archivedIds - note.id
      prefs.edit().putStringSet("pinned", pinnedIds).putStringSet("archived", archivedIds).apply()
      selectedNote = null
    }
  }

  fun togglePinSelected() {
    val id = selectedNote?.id ?: return
    pinnedIds = if (pinnedIds.contains(id)) pinnedIds - id else pinnedIds + id
    prefs.edit().putStringSet("pinned", pinnedIds).apply()
    selectedNote = null
  }

  fun archiveSelected() {
    val id = selectedNote?.id ?: return
    archivedIds = if (archiveOpen) archivedIds - id else archivedIds + id
    prefs.edit().putStringSet("archived", archivedIds).apply()
    selectedNote = null
  }

  fun createNote() {
    clearAiPreview()
    accountScope.launch {
      val note = repo.createNote(requireOwnerId())
      openedNote = note
      openedBlocks = withContext(Dispatchers.IO) { repo.document(note) }
      (getApplication<android.app.Application>() as LurgNotesApplication).requestNotesSync()
    }
  }

  fun open(note: NoteEntity) {
    clearAiPreview()
    openedNote = note
    openedBlocks = emptyList()
    accountScope.launch { val blocks = withContext(Dispatchers.IO) { repo.document(note) }; if (openedNote?.id == note.id) openedBlocks = blocks }
  }

  fun close() {
    clearAiPreview()
    openedNote?.let { note ->
      accountScope.launch { flushDocument(note.id) }
    }
    openedNote = null
    openedBlocks = emptyList()
  }

  fun updateDocument(noteId: String, blocks: List<DocumentBlock>) {
    openedBlocks = blocks
    pendingDocumentWrites[noteId] = blocks
    documentSaveJobs.remove(noteId)?.cancel()
    documentSaveJobs[noteId] = accountScope.launch {
      delay(DOCUMENT_SAVE_DEBOUNCE_MS)
      documentSaveJobs.remove(noteId)
      persistPendingDocument(noteId)
    }
  }

  /** Called when the activity leaves the foreground as well as on editor back. */
  fun flushOpenDocument() {
    openedNote?.let { note -> accountScope.launch { flushDocument(note.id) } }
  }

  private suspend fun flushDocument(noteId: String) {
    documentSaveJobs.remove(noteId)?.cancel()
    persistPendingDocument(noteId)
  }

  private suspend fun persistPendingDocument(noteId: String) {
    val blocks = pendingDocumentWrites.remove(noteId) ?: return
    val owner = authUser?.id ?: return
    repo.saveDocument(noteId, owner, blocks)
    (getApplication<android.app.Application>() as LurgNotesApplication).requestNotesSync()
  }

  fun saveSoon(noteId: String, markdown: String) {
    saveJob?.cancel()
    saveJob = accountScope.launch {
      delay(280)
      repo.saveMarkdown(noteId, requireOwnerId(), markdown)
      (getApplication<android.app.Application>() as LurgNotesApplication).requestNotesSync()
    }
  }

  private fun activateAccount(user: AuthUser?) {
    if (user == null) {
      (getApplication<android.app.Application>() as LurgNotesApplication).realtimeSync.setSessionAllowed(false)
      authUser = null
      activeOwnerId.value = null
      return
    }
    if (authUser?.id == user.id) { authUser = user; (getApplication<android.app.Application>() as LurgNotesApplication).realtimeSync.setSessionAllowed(true); return }
    // Never retain an opened note or selection across an identity transition.
    openedNote = null
    selectedNote = null
    archiveOpen = false
    if (authUser?.id != null && authUser?.id != user.id) clearAccountUi()
    authUser = user
    activeOwnerId.value = user.id
    (getApplication<android.app.Application>() as LurgNotesApplication).realtimeSync.setSessionAllowed(true)
    (getApplication<android.app.Application>() as LurgNotesApplication).requestNotesSync()
  }

  private fun requireOwnerId(): String = authUser?.id ?: error("Требуется вход в Lurg ID")

  private companion object {
    const val DOCUMENT_SAVE_DEBOUNCE_MS = 350L
  }

  fun runAiAction(noteId: String, action: String, document: String, selected: String, userRequest: String) {
    val ownerId = authUser?.id ?: return
    if (openedNote?.id != noteId || openedNote?.ownerId != ownerId) return
    val ownerGeneration = accountGeneration
    clearAiPreview()
    val requestGeneration = aiRequestGeneration
    aiBusy = true
    aiError = null
    aiJob = accountScope.launch {
      runCatching {
        flushDocument(noteId)
        val current = repo.note(noteId, ownerId) ?: error("Заметка удалена")
        ai.runAction(ownerId, noteId, action, if (selected.isBlank()) document else "", selected, userRequest, current.serverRevision)
      }
        .onSuccess { if (ownerGeneration == accountGeneration && authUser?.id == ownerId && requestGeneration == aiRequestGeneration && openedNote?.id == noteId) aiPreview = AiPreview(action = it.action, content = it.content) }
        .onFailure { if (it is CancellationException) throw it; if (ownerGeneration == accountGeneration && authUser?.id == ownerId && requestGeneration == aiRequestGeneration) aiError = it.message ?: "AI недоступен" }
      if (ownerGeneration == accountGeneration && authUser?.id == ownerId && requestGeneration == aiRequestGeneration) aiBusy = false
    }
  }

  fun clearAiPreview() {
    aiRequestGeneration += 1
    aiJob?.cancel(); aiJob = null
    aiBusy = false
    aiPreview = null
    aiError = null
  }

  fun extractGraphFacts(note: NoteEntity, markdown: String) {
    val ownerId = authUser?.id ?: return
    if (note.ownerId != ownerId) return
    val ownerGeneration = accountGeneration
    if (graphBusy) return
    graphBusy = true
    graphError = null
    graphAddedCount = 0
    accountScope.launch {
      runCatching {
        flushDocument(note.id)
        val current = repo.note(note.id, ownerId) ?: error("Заметка удалена")
        graph.extract(ownerId, note.id, current.title, markdown, current.serverRevision.coerceAtLeast(1))
      }
        .onSuccess { if (ownerGeneration == accountGeneration && authUser?.id == ownerId) graphCandidates = it }
        .onFailure { if (it is CancellationException) throw it; if (ownerGeneration == accountGeneration && authUser?.id == ownerId) graphError = it.message ?: "Не удалось проанализировать заметку." }
      if (ownerGeneration == accountGeneration && authUser?.id == ownerId) graphBusy = false
    }
  }

  fun confirmGraphFacts(selected: List<NotesGraphCandidate>) {
    val ownerId = authUser?.id ?: return
    val ownerGeneration = accountGeneration
    if (graphBusy || selected.isEmpty()) return
    graphBusy = true
    graphError = null
    accountScope.launch {
      var added = 0
      selected.forEach { candidate ->
        runCatching { graph.confirm(ownerId, candidate.id) }
          .onSuccess { added += 1 }
          .onFailure { if (it is CancellationException) throw it; if (ownerGeneration == accountGeneration && authUser?.id == ownerId) graphError = it.message ?: "Не удалось добавить часть фактов." }
      }
      if (ownerGeneration != accountGeneration || authUser?.id != ownerId) return@launch
      graphAddedCount = added
      graphBusy = false
      if (graphError == null) graphCandidates = null
    }
  }

  fun dismissGraphFacts(reject: Boolean = false) {
    val ownerId = authUser?.id
    val pending = graphCandidates.orEmpty()
    graphCandidates = null
    if (reject && ownerId != null) accountScope.launch { pending.forEach { runCatching { graph.reject(ownerId, it.id) } } }
  }
}

data class AiPreview(val action: String, val content: String)

private val LocalMotionEnabled = staticCompositionLocalOf { true }
@Composable
private fun LurgNotesApp(viewModel: NotesViewModel) {
  val app = LocalContext.current.applicationContext as LurgNotesApplication
  val motionEnabled by app.motionEnabled.collectAsState()
  CompositionLocalProvider(LocalMotionEnabled provides motionEnabled) { LurgNotesAppContent(viewModel) }
}
@Composable
private fun LurgNotesAppContent(viewModel: NotesViewModel) {
  val opened = viewModel.openedNote
  val context = LocalContext.current
  val updateState by viewModel.updates.state.collectAsState()
  val syncStatus by viewModel.syncStatus.collectAsState()
  DisposableEffect(context, viewModel.privateScreen) {
    val window = (context as? android.app.Activity)?.window
    if (viewModel.privateScreen) window?.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
    else window?.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)
    onDispose { window?.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
  }
  val notificationPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
    if (granted) viewModel.registerPushToken()
  }
  val motionEnabled = rememberLurgMotionEnabled()
  val homeListState = rememberLazyListState()
  var homeSearch by rememberSaveable { mutableStateOf("") }
  var homeSearchOpen by rememberSaveable { mutableStateOf(false) }
  var homeSection by rememberSaveable { mutableStateOf("Все") }
  val predictiveBack = remember { Animatable(0f) }
  val homeScale by animateFloatAsState(
    targetValue = if (opened == null) 1f else .985f,
    animationSpec = LurgMotion.surfaceSpring(motionEnabled), label = "home-spatial-scale"
  )
  val homeAlpha by animateFloatAsState(
    // The editor is a separate opaque surface.  Keeping a dimmed home screen
    // beneath it made a partially progressed back gesture look like two UI
    // screens were permanently composited together.
    targetValue = if (opened == null) 1f else 0f,
    animationSpec = LurgMotion.fast(motionEnabled), label = "home-spatial-alpha"
  )
  PredictiveBackHandler(enabled = opened != null) { progress ->
    var committed = false
    try {
      progress.collect { event: BackEventCompat -> predictiveBack.snapTo(event.progress) }
      committed = true
    } finally {
      if (committed) viewModel.close() else predictiveBack.animateTo(0f, LurgMotion.backSpring(motionEnabled))
    }
  }
  BackHandler(enabled = opened != null && android.os.Build.VERSION.SDK_INT < 34) { viewModel.close() }
  BackHandler(enabled = viewModel.selectedNote != null || viewModel.archiveOpen || homeSearchOpen) {
    when {
      viewModel.selectedNote != null -> viewModel.clearSelection()
      viewModel.archiveOpen -> viewModel.closeArchive()
      homeSearchOpen -> { homeSearchOpen = false; homeSearch = "" }
    }
  }
  LaunchedEffect(viewModel.authUser?.id) {
    if (viewModel.authUser != null) {
      if (android.os.Build.VERSION.SDK_INT < 33 || androidx.core.content.ContextCompat.checkSelfPermission(context, android.Manifest.permission.POST_NOTIFICATIONS) == android.content.pm.PackageManager.PERMISSION_GRANTED) {
        viewModel.registerPushToken()
      } else {
        notificationPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
      }
    }
  }
  Box(Modifier.fillMaxSize().background(LurgVoid)) {
    when {
      !viewModel.authChecked -> LoadingScreen()
      !viewModel.onboardingCompleted -> OnboardingScreen(onContinue = { viewModel.completeOnboarding() })
      viewModel.authUser == null -> AuthScreen(viewModel)
      else -> {
        Box(Modifier.fillMaxSize()) {
          if (opened == null || homeAlpha > .001f || predictiveBack.value > 0f) NotesList(
            viewModel = viewModel,
            listState = homeListState,
            search = homeSearch,
            searchOpen = homeSearchOpen,
            section = homeSection,
            onSearchChange = { homeSearch = it },
            onSearchOpenChange = { homeSearchOpen = it },
            onSectionChange = { homeSection = it },
            modifier = Modifier.fillMaxSize().graphicsLayer {
              scaleX = homeScale
              scaleY = homeScale
              alpha = (homeAlpha + predictiveBack.value).coerceIn(0f, 1f)
              translationY = if (opened == null) 0f else -12f
            }
          )
          AnimatedContent(
            targetState = opened,
            modifier = Modifier.fillMaxSize(),
            transitionSpec = {
              if (initialState == null && targetState != null) {
                (scaleIn(LurgMotion.surfaceSpring(motionEnabled), initialScale = .975f) +
                  slideInVertically(LurgMotion.standard(motionEnabled)) { it / 18 }) togetherWith
                  scaleOut(LurgMotion.fast(motionEnabled), targetScale = .99f)
              } else {
                (fadeIn(LurgMotion.fast(motionEnabled)) + scaleIn(LurgMotion.surfaceSpring(motionEnabled), initialScale = .99f)) togetherWith
                  (fadeOut(LurgMotion.fast(motionEnabled)) + slideOutVertically(LurgMotion.standard(motionEnabled)) { it / 14 })
              }
            },
            label = "home-note-spatial-transition"
          ) { destination ->
            if (destination != null) {
              EditorScreen(
                viewModel = viewModel,
                note = destination,
                modifier = Modifier.fillMaxSize().graphicsLayer {
                  translationX = size.width * predictiveBack.value * .24f
                  scaleX = 1f - predictiveBack.value * .018f
                  scaleY = 1f - predictiveBack.value * .018f
                  // Never reduce editor opacity: it must not reveal the home
                  // scene underneath during predictive-back interaction.
                  alpha = 1f
                }
              )
            }
          }
        }
      }
    }
    if (viewModel.profileOpen && viewModel.authUser != null) {
      NotesSettingsScreen(
        user = viewModel.authUser!!,
        checkingUpdate = updateState.checking,
        updateStatus = updateState.error ?: if (updateState.downloading) "${updateState.status} ${updateState.progress}%" else updateState.status,
        updateAvailable = updateState.release != null && !updateState.downloading && (!updateState.installing || updateState.pendingConfirmation),
        onClose = { viewModel.closeProfile() },
        onLogout = { viewModel.logout() },
        onGraph = { viewModel.openGraph() },
        onCheckUpdate = { viewModel.checkForUpdates() },
        onDownloadUpdate = { viewModel.downloadUpdate() },
        e2eeStatus = viewModel.e2eeStatus,
        recoverySecret = viewModel.recoverySecret,
        devices = viewModel.e2eeDevices,
        currentDeviceId = viewModel.e2eeCurrentDeviceId,
        onSetupE2ee = { viewModel.setupE2ee() },
        onApproveE2ee = { viewModel.approveE2eeDevice(it) },
        onRevokeE2ee = { viewModel.revokeE2eeDevice(it) },
        onRecoverE2ee = { viewModel.recoverE2ee(it) },
        privateScreen = viewModel.privateScreen,
        onPrivateScreen = viewModel::togglePrivateScreen,
        syncStatus = syncStatus, onSync = viewModel::syncNow, motionEnabled = motionEnabled
      )
    }
    if (viewModel.graphOpen && viewModel.authUser != null) {
      UserGraphScreen(user = viewModel.authUser!!, onClose = { viewModel.closeGraph() })
    }
  }
  if (updateState.prompt && updateState.release != null) {
    AlertDialog(
      onDismissRequest = { viewModel.updates.defer() },
      title = { Text("Установить обновление?") },
      text = { Column(Modifier.verticalScroll(rememberScrollState())) {
        Text("Lurg Notes ${updateState.release!!.versionName}")
        Text(updateState.release!!.changelog.take(5).joinToString("\n") { it.take(300) }, fontSize = 14.sp)
        Text("Файл будет скачан и проверен. Если Android потребует подтверждение, откроется системное окно.", fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp))
      } },
      confirmButton = { TextButton(onClick = { viewModel.updates.approve() }) { Text("Да") } },
      dismissButton = { TextButton(onClick = { viewModel.updates.defer() }) { Text("Нет") } },
      containerColor = LurgGlass, titleContentColor = LurgText, textContentColor = LurgText,
    )
  }
}

@Composable
private fun LoadingScreen() {
  Box(Modifier.fillMaxSize().statusBarsPadding().padding(24.dp), contentAlignment = Alignment.Center) {
    Text("Lurg Notes", color = LurgText, fontSize = 30.sp, fontWeight = FontWeight.Bold)
  }
}

@Composable
private fun AuthScreen(viewModel: NotesViewModel) {
  var mode by remember { mutableStateOf(AuthMode.Choice) }
  var name by remember { mutableStateOf("") }
  var email by remember { mutableStateOf("") }
  var password by remember { mutableStateOf("") }
  var code by remember { mutableStateOf("") }
  val registrationEmail = viewModel.registrationEmail
  fun back() { if (registrationEmail != null) viewModel.cancelRegistration() else mode = AuthMode.Choice }
  BackHandler(enabled = mode != AuthMode.Choice || registrationEmail != null) { back() }
  Column(
    Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 34.dp),
    verticalArrangement = Arrangement.Center
  ) {
    if (mode != AuthMode.Choice || registrationEmail != null) CompactIconButton("←", "Назад", onClick = { back() })
    Text("Lurg Notes", color = LurgText, fontSize = 40.sp, fontWeight = FontWeight.Bold)
    Spacer(Modifier.height(12.dp))
    Text("Один Lurg ID для Notes, Graph и других сервисов. Аккаунт и сессия создаются через auth.lurg.pro.", color = LurgMuted, fontSize = 18.sp)
    if (viewModel.authError != null) {
      Spacer(Modifier.height(18.dp))
      Text(viewModel.authError.orEmpty(), color = LurgBlue, fontSize = 16.sp)
    }
    Spacer(Modifier.height(28.dp))
    if (registrationEmail != null) {
      AuthField(code, { code = it }, "Код из письма", KeyboardType.Number, ImeAction.Done)
      Spacer(Modifier.height(12.dp))
      Text("Код отправлен на $registrationEmail", color = LurgMuted, fontSize = 14.sp)
      Spacer(Modifier.height(12.dp))
      PanelAction(if (viewModel.authBusy) "Проверяем…" else "Подтвердить Lurg ID", Modifier.fillMaxWidth(), fill = LurgBlue) { if (!viewModel.authBusy) viewModel.confirmRegistration(code) }
      Spacer(Modifier.height(12.dp))
      PanelAction("Изменить данные", Modifier.fillMaxWidth()) { viewModel.cancelRegistration() }
    } else when (mode) {
      AuthMode.Choice -> {
        PanelAction("Войти с Lurg ID", Modifier.fillMaxWidth(), fill = LurgBlue) { mode = AuthMode.Login }
        Spacer(Modifier.height(12.dp))
        PanelAction("Создать Lurg ID", Modifier.fillMaxWidth()) { mode = AuthMode.Register }
      }
      AuthMode.Login -> {
        AuthField(email, { email = it }, "Почта", KeyboardType.Email, ImeAction.Next)
        Spacer(Modifier.height(10.dp))
        AuthField(password, { password = it }, "Пароль", KeyboardType.Password, ImeAction.Done, secret = true) { viewModel.login(email, password) }
        Spacer(Modifier.height(12.dp))
        PanelAction(if (viewModel.authBusy) "Входим…" else "Войти", Modifier.fillMaxWidth(), fill = LurgBlue) { if (!viewModel.authBusy) viewModel.login(email, password) }
        Spacer(Modifier.height(12.dp))
        PanelAction("Создать Lurg ID", Modifier.fillMaxWidth()) { mode = AuthMode.Register }
      }
      AuthMode.Register -> {
        AuthField(name, { name = it }, "Имя", KeyboardType.Text, ImeAction.Next)
        Spacer(Modifier.height(10.dp))
        AuthField(email, { email = it }, "Почта", KeyboardType.Email, ImeAction.Next)
        Spacer(Modifier.height(10.dp))
        AuthField(password, { password = it }, "Пароль не короче 8 символов", KeyboardType.Password, ImeAction.Done, secret = true) { viewModel.requestRegistration(name, email, password) }
        Spacer(Modifier.height(12.dp))
        PanelAction(if (viewModel.authBusy) "Отправляем код…" else "Продолжить", Modifier.fillMaxWidth(), fill = LurgBlue) { if (!viewModel.authBusy) viewModel.requestRegistration(name, email, password) }
        Spacer(Modifier.height(12.dp))
        PanelAction("У меня уже есть Lurg ID", Modifier.fillMaxWidth()) { mode = AuthMode.Login }
      }
    }
    Spacer(Modifier.height(18.dp))
    Text("Защищено auth.lurg.pro", color = LurgMuted, fontSize = 14.sp)
  }
}

private enum class AuthMode { Choice, Login, Register }

@Composable
private fun AuthField(value: String, onChange: (String) -> Unit, hint: String, keyboardType: KeyboardType, imeAction: ImeAction, secret: Boolean = false, onDone: (() -> Unit)? = null) {
  BasicTextField(
    value = value, onValueChange = onChange, singleLine = true,
    visualTransformation = if (secret) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
    keyboardOptions = KeyboardOptions(keyboardType = keyboardType, imeAction = imeAction),
    keyboardActions = KeyboardActions(onDone = { onDone?.invoke() }),
    textStyle = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 17.sp), cursorBrush = SolidColor(LurgText),
    modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(15.dp)).background(LurgGlass).padding(15.dp),
    decorationBox = { inner -> if (value.isBlank()) Text(hint, color = LurgMuted); inner() }
  )
}

@Composable
private fun OnboardingScreen(onContinue: () -> Unit) {
  val pages = listOf(
    OnboardingPage("✦", "Мысли остаются вашими.", "Создавайте заметки локально — без ожидания сети и лишних шагов."),
    OnboardingPage("⌁", "Markdown без компромиссов.", "Текст, чек-листы, таблицы и вложения собираются в один ясный документ."),
    OnboardingPage("◈", "Один Lurg ID.", "Войдите или создайте аккаунт через auth.lurg.pro, чтобы синхронизировать Notes и вручную отправлять факты в Graph.")
  )
  var page by remember { mutableStateOf(0) }
  val current = pages[page]
  Column(
    Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().padding(horizontal = 24.dp, vertical = 34.dp),
    verticalArrangement = Arrangement.SpaceBetween
  ) {
    Text("Lurg Notes", color = LurgText, fontSize = 26.sp, fontWeight = FontWeight.Bold)
    Column {
      Text("0${page + 1} / 0${pages.size}", color = LurgBlue, fontSize = 14.sp, fontWeight = FontWeight.Bold)
      Spacer(Modifier.height(30.dp))
      Box(Modifier.size(96.dp).clip(RoundedCornerShape(30.dp)).background(LurgGlass), contentAlignment = Alignment.Center) {
        Text(current.mark, color = LurgBlue, fontSize = 42.sp, fontWeight = FontWeight.Bold)
      }
      Spacer(Modifier.height(28.dp))
      Text(current.title, color = LurgText, fontSize = 39.sp, lineHeight = 43.sp, fontWeight = FontWeight.Bold)
      Spacer(Modifier.height(16.dp))
      Text(current.body, color = LurgMuted, fontSize = 19.sp, lineHeight = 27.sp)
    }
    Column {
      Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        pages.indices.forEach { index -> Box(Modifier.size(if (index == page) 24.dp else 8.dp, 8.dp).clip(RoundedCornerShape(8.dp)).background(if (index == page) LurgBlue else LurgGlass)) }
      }
      Spacer(Modifier.height(26.dp))
      PanelAction(if (page == pages.lastIndex) "Продолжить с Lurg ID" else "Далее", Modifier.fillMaxWidth(), fill = LurgBlue) {
        if (page == pages.lastIndex) onContinue() else page += 1
      }
    }
  }
}

private data class OnboardingPage(val mark: String, val title: String, val body: String)

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NotesList(
  viewModel: NotesViewModel,
  listState: LazyListState,
  search: String,
  searchOpen: Boolean,
  section: String,
  onSearchChange: (String) -> Unit,
  onSearchOpenChange: (Boolean) -> Unit,
  onSectionChange: (String) -> Unit,
  modifier: Modifier = Modifier
) {
  val notes by viewModel.notes.collectAsState()
  val syncStatus by viewModel.syncStatus.collectAsState()
  val searchFocus = remember { FocusRequester() }
  val keyboard = LocalSoftwareKeyboardController.current
  LaunchedEffect(searchOpen) {
    if (searchOpen) {
      delay(32)
      searchFocus.requestFocus()
      keyboard?.show()
    }
  }
  val archiveOpen = viewModel.archiveOpen
  val visibleNotes = remember(notes, search, section, archiveOpen, viewModel.pinnedIds, viewModel.archivedIds) { notes
    .filter { note -> viewModel.archivedIds.contains(note.id) == archiveOpen }
    .filter { note -> search.isBlank() || note.title.contains(search, true) || note.markdown.contains(search, true) }
    .filter { note -> section != "Закреплённые" || viewModel.pinnedIds.contains(note.id) }
    .sortedWith(compareByDescending<NoteEntity> { viewModel.pinnedIds.contains(it.id) }.thenByDescending { it.updatedAt }) }
  val selected = viewModel.selectedNote
  Box(modifier) {
    Column(
      Modifier.fillMaxSize()
        .imePadding()
        .statusBarsPadding()
        .padding(horizontal = 18.dp)
    ) {
      Row(Modifier.fillMaxWidth().padding(top = 14.dp, bottom = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(if (archiveOpen) "Архив" else "Lurg Notes", color = LurgText, fontSize = 23.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.weight(1f))
        if (archiveOpen) {
          CompactIconButton("‹", "Закрыть архив") { viewModel.closeArchive() }
        } else {
          CompactIconButton(if (searchOpen) "×" else "⌕", if (searchOpen) "Закрыть поиск" else "Поиск") {
            onSearchOpenChange(!searchOpen)
            if (searchOpen) {
              onSearchChange("")
              keyboard?.hide()
            }
          }
          Spacer(Modifier.width(4.dp))
          CompactIconButton("+", "Новая заметка", highlighted = true) { viewModel.createNote() }
        }
      }
      AnimatedVisibility(visible = searchOpen, enter = fadeIn(LurgMotion.fast(rememberLurgMotionEnabled())) + scaleIn(LurgMotion.surfaceSpring(rememberLurgMotionEnabled()), initialScale = .97f), exit = fadeOut(LurgMotion.fast(rememberLurgMotionEnabled())) + scaleOut(LurgMotion.fast(rememberLurgMotionEnabled()), targetScale = .98f)) {
        BasicTextField(
          value = search, onValueChange = onSearchChange, singleLine = true,
          textStyle = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 16.sp), cursorBrush = SolidColor(LurgAccent),
          modifier = Modifier.fillMaxWidth().focusRequester(searchFocus).clip(RoundedCornerShape(13.dp)).background(LurgField).padding(horizontal = 14.dp, vertical = 12.dp),
          decorationBox = { inner -> Row(verticalAlignment = Alignment.CenterVertically) { Text("⌕", color = LurgMuted, fontSize = 17.sp); Spacer(Modifier.width(8.dp)); if (search.isBlank()) Text("Поиск по заметкам", color = LurgMuted, fontSize = 16.sp); inner() } }
        )
      }
      if (!archiveOpen) {
        Text(syncStatus, color = LurgMuted, fontSize = 11.sp, modifier = Modifier.padding(bottom = 6.dp))
        Row(Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 12.dp).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(18.dp)) {
          listOf("Все", "Закреплённые", "Недавние").forEach { label ->
            MotionTab(label = label, selected = section == label, onClick = { onSectionChange(label) })
          }
          if (viewModel.archivedIds.isNotEmpty()) Text("Архив ${viewModel.archivedIds.size}", color = LurgMuted, fontSize = 13.sp, modifier = Modifier.clickable { viewModel.openArchive() }.padding(vertical = 6.dp))
        }
      }
      LazyColumn(Modifier.weight(1f), state = listState, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (visibleNotes.isEmpty()) {
          item {
            EmptyNotesState(if (archiveOpen) "В архиве пусто" else "Пока нет заметок")
          }
        }
        items(visibleNotes, key = { it.id }) { note ->
          NoteRow(
            note = note,
            pinned = viewModel.pinnedIds.contains(note.id),
            onClick = { viewModel.open(note) },
            onLongClick = { viewModel.selectNote(note) },
            modifier = Modifier.animateItemPlacement(LurgMotion.listSpring(rememberLurgMotionEnabled()))
          )
        }
      }
      if (!archiveOpen) {
        Row(Modifier.fillMaxWidth().navigationBarsPadding().padding(top = 8.dp, bottom = 8.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
          BottomNavItem("▣", "Заметки", true) { onSectionChange("Все") }
          BottomNavItem("⌕", "Поиск", searchOpen) { onSearchOpenChange(true) }
          BottomNavItem("⚙", "Настройки", false) { viewModel.openProfile() }
        }
      }
    }
    if (selected != null) {
      SelectedNoteOverlay(
        note = selected,
        pinned = viewModel.pinnedIds.contains(selected.id),
        onDismiss = { viewModel.clearSelection() },
        onDelete = { viewModel.deleteSelectedNote() },
        onPin = { viewModel.togglePinSelected() },
        onArchive = { viewModel.archiveSelected() },
        archiveOpen = archiveOpen
      )
    }
  }
}

@Composable
private fun EmptyNotesState(text: String) {
  Box(
    Modifier.fillMaxWidth()
      .padding(top = 42.dp)
      .clip(RoundedCornerShape(16.dp))
      .background(LurgCard)
      .padding(horizontal = 22.dp, vertical = 28.dp),
    contentAlignment = Alignment.Center
  ) {
    Text(text, color = LurgMuted, fontSize = 18.sp, fontWeight = FontWeight.Bold)
  }
}

@Composable
private fun ProfileRow(label: String, value: String) {
  Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
    Text(label, color = LurgMuted, fontSize = 13.sp)
    Text(value, color = LurgText, fontSize = 16.sp)
  }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NoteRow(note: NoteEntity, pinned: Boolean, onClick: () -> Unit, onLongClick: () -> Unit, modifier: Modifier = Modifier) {
  val title = displayNoteTitle(note)
  val preview = remember(note.markdown, title) { documentPreview(note.markdown, title) }
  val motionEnabled = rememberLurgMotionEnabled()
  val interaction = remember { MutableInteractionSource() }
  val pressed by interaction.collectIsPressedAsState()
  val scale by animateFloatAsState(if (pressed) .992f else 1f, LurgMotion.press(motionEnabled), label = "note-row-press")
  val cardColor by animateColorAsState(if (pressed) LurgField else LurgCard, LurgMotion.fast(motionEnabled), label = "note-row-highlight")
  Row(
    modifier.fillMaxWidth()
      .graphicsLayer { this.scaleX = scale; this.scaleY = scale }
      .clip(RoundedCornerShape(15.dp))
      .background(cardColor)
      .combinedClickable(interactionSource = interaction, indication = null, onClick = onClick, onLongClick = onLongClick)
      .padding(horizontal = 14.dp, vertical = 13.dp),
    verticalAlignment = Alignment.CenterVertically
  ) {
    Column(Modifier.weight(1f)) {
      Row(verticalAlignment = Alignment.CenterVertically) {
        if (pinned) {
          Text("⌃", color = LurgAccent, fontSize = 14.sp, fontWeight = FontWeight.Bold)
          Spacer(Modifier.width(5.dp))
        }
        Text(title, color = LurgText, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
      }
      if (preview.isNotBlank()) Text(preview, color = LurgMuted, fontSize = 13.sp, maxLines = 2, modifier = Modifier.padding(top = 3.dp))
    }
    Column(Modifier.padding(start = 10.dp)) {
      Text(note.updatedAt.substringAfter("T").take(5), color = LurgMuted, fontSize = 12.sp)
      Text(if (note.dirty) "На устройстве" else "В облаке", color = if (note.dirty) LurgAccent else LurgMuted, fontSize = 10.sp)
    }
  }
}

@Composable
private fun CompactIconButton(mark: String, description: String, highlighted: Boolean = false, onClick: () -> Unit) {
  Box(Modifier.size(48.dp).semantics { contentDescription = description }.lurgPressable(onClick).clip(CircleShape).background(if (highlighted) LurgAccent else LurgField), contentAlignment = Alignment.Center) {
    Text(mark, color = if (highlighted) LurgVoid else LurgText, fontSize = 24.sp, fontWeight = FontWeight.Medium)
  }
}

@Composable
private fun BottomNavItem(mark: String, label: String, selected: Boolean, onClick: () -> Unit) {
  Column(Modifier.width(76.dp).lurgPressable(onClick).padding(vertical = 4.dp), horizontalAlignment = Alignment.CenterHorizontally) {
    Text(mark, color = if (selected) LurgAccent else LurgMuted, fontSize = 20.sp)
    Text(label, color = if (selected) LurgAccent else LurgMuted, fontSize = 10.sp, fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
  }
}

@Composable
private fun MotionTab(label: String, selected: Boolean, onClick: () -> Unit) {
  val motionEnabled = rememberLurgMotionEnabled()
  val textColor by animateColorAsState(if (selected) LurgText else LurgMuted, LurgMotion.fast(motionEnabled), label = "tab-color-$label")
  val indicatorWidth by animateDpAsState(if (selected) 18.dp else 0.dp, LurgMotion.fast(motionEnabled), label = "tab-indicator-$label")
  Column(Modifier.lurgPressable(onClick).padding(vertical = 4.dp), horizontalAlignment = Alignment.CenterHorizontally) {
    Text(label, color = textColor, fontSize = 13.sp, fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
    Spacer(Modifier.height(5.dp))
    Box(Modifier.width(indicatorWidth).height(2.dp).clip(RoundedCornerShape(2.dp)).background(LurgAccent))
  }
}

private fun documentPreview(markdown: String, title: String): String {
  val lines = markdown.lineSequence().map(String::trim).filter(String::isNotBlank).take(8).toList()
  if (lines.windowed(2).any { (first, second) -> first.startsWith("|") && second.contains("---") }) return "Таблица"
  return lines.firstOrNull { line ->
    line != title && !line.startsWith("#") && !line.startsWith("![")
  }?.let { line ->
    when {
      line.startsWith("```") -> "Код"
      line.startsWith("[PDF") -> "PDF"
      line.startsWith("[Видео]") -> "Видео"
      else -> line.replace(Regex("^[-*]\\s+(\\[[ xX]\\]\\s+)?|^>\\s+|^\\d+\\.\\s+"), "")
    }
  }.orEmpty()
}

@Composable
private fun SelectedNoteOverlay(
  note: NoteEntity,
  pinned: Boolean,
  onDismiss: () -> Unit,
  onDelete: () -> Unit,
  onPin: () -> Unit,
  onArchive: () -> Unit,
  archiveOpen: Boolean
) {
  val scale by animateFloatAsState(targetValue = 1.035f, animationSpec = tween(130), label = "selected-note-scale")
  Box(
    Modifier.fillMaxSize()
      .background(Color(0xAA050509))
      .clickable(onClick = onDismiss)
      .padding(horizontal = 22.dp),
    contentAlignment = Alignment.Center
  ) {
    Box(Modifier.fillMaxWidth().graphicsLayer { scaleX = scale; scaleY = scale }) {
      NoteRow(note = note, pinned = pinned, onClick = {}, onLongClick = {})
    }
    Row(
      Modifier.align(Alignment.BottomCenter)
        .navigationBarsPadding()
        .padding(bottom = 28.dp)
        .clip(RoundedCornerShape(28.dp))
        .background(LurgPanelStrong)
        .padding(horizontal = 12.dp, vertical = 10.dp),
      horizontalArrangement = Arrangement.spacedBy(10.dp),
      verticalAlignment = Alignment.CenterVertically
    ) {
      ContextAction("⌃", if (pinned) "Открепить" else "Закрепить", onPin)
      ContextAction(if (archiveOpen) "↥" else "▣", if (archiveOpen) "Вернуть" else "В архив", onArchive)
      ContextAction("⌫", "Удалить", onDelete, danger = true)
    }
  }
}

@Composable
private fun ContextAction(icon: String, label: String, onClick: () -> Unit, danger: Boolean = false) {
  Column(
    Modifier.width(96.dp)
      .clip(RoundedCornerShape(20.dp))
      .background(if (danger) Color(0x33B73A5A) else LurgGlass)
      .clickable(onClick = onClick)
      .padding(vertical = 10.dp),
    horizontalAlignment = Alignment.CenterHorizontally
  ) {
    Text(icon, color = if (danger) Color(0xFFFF6B8A) else LurgText, fontSize = 23.sp, fontWeight = FontWeight.Bold)
    Text(label, color = if (danger) Color(0xFFFF9BAD) else LurgMuted, fontSize = 11.sp)
  }
}

@Composable
private fun EditorScreen(viewModel: NotesViewModel, note: NoteEntity, modifier: Modifier = Modifier) {
  val initialBlocks = viewModel.openedBlocks
  if (initialBlocks.isEmpty()) {
    Box(modifier.fillMaxSize().background(LurgVoid), contentAlignment = Alignment.Center) {
      Text("Открываем заметку", color = LurgMuted, fontSize = 15.sp)
    }
    return
  }
  BlockDocumentEditor(
    noteId = note.id,
    initialBlocks = initialBlocks,
    onDocumentChanged = { viewModel.updateDocument(note.id, it) },
    onClose = viewModel::close,
    viewModel = viewModel,
    modifier = modifier
  )
}

/** A document controller never parses Markdown while the user types. */
internal data class AiEditorSnapshot(val blocks: List<DocumentBlock>, val blockId: String?, val range: TextRange, val selected: String)

internal class BlockDocumentController(initial: List<DocumentBlock>, private val onChanged: (List<DocumentBlock>) -> Unit) {
  var blocks by mutableStateOf(initial)
    private set
  var activeBlockId by mutableStateOf(initial.firstOrNull()?.id)
    private set
  var focusBlockId by mutableStateOf<String?>(initial.firstOrNull()?.id)
    private set
  var focusToken by mutableStateOf(0)
    private set

  var selection by mutableStateOf(TextRange.Zero)
    private set
  var selectionText by mutableStateOf("")
    private set
  private val undoStack = ArrayDeque<List<DocumentBlock>>()
  private val redoStack = ArrayDeque<List<DocumentBlock>>()
  private var lastEmitted = initial
  private var typing = false
  private var lastTypingId: String? = null
  private var lastTypingTime = 0L
  var canUndo by mutableStateOf(false); private set
  var canRedo by mutableStateOf(false); private set
  fun undo() {
    val previous = undoStack.pollLast() ?: return
    redoStack.addLast(blocks); blocks = previous; lastEmitted = previous
    canUndo = undoStack.isNotEmpty(); canRedo = true; lastTypingId = null
    onChanged(blocks); blocks.firstOrNull { it.id == activeBlockId }?.let { focus(it.id) }
  }
  fun redo() {
    val next = redoStack.pollLast() ?: return
    undoStack.addLast(blocks); blocks = next; lastEmitted = next
    canUndo = true; canRedo = redoStack.isNotEmpty(); lastTypingId = null
    onChanged(blocks); blocks.firstOrNull { it.id == activeBlockId }?.let { focus(it.id) }
  }
  fun formatInline(mark: String) {
    val block = blocks.firstOrNull { it.id == activeBlockId } ?: return
    if (block.type in setOf("image", "file", "divider", "table")) return
    val range = if (selection.collapsed && block.text.isNotEmpty()) TextRange(0, block.text.length) else selection
    val edit = when (mark) {
      "bold" -> MarkdownTransforms.wrap(block.text, range.start, range.end, "**")
      "italic" -> MarkdownTransforms.wrap(block.text, range.start, range.end, "*")
      "strike" -> MarkdownTransforms.wrap(block.text, range.start, range.end, "~~")
      "code" -> MarkdownTransforms.wrap(block.text, range.start, range.end, "`")
      else -> return
    }
    replace(block.copy(text = edit.text)); selection = TextRange(edit.selectionStart, edit.selectionEnd)
    selectionText = edit.text; focus(block.id)
  }
  fun insertLink(label: String, url: String): Boolean {
    if (!InlineMarkdown.safeLink(url)) return false
    val block = blocks.firstOrNull { it.id == activeBlockId } ?: return false
    val start = selection.min.coerceIn(0, block.text.length); val end = selection.max.coerceIn(start, block.text.length)
    val safeLabel = label.ifBlank { "Ссылка" }.replace("[", "\\[").replace("]", "\\]")
    val text = "[$safeLabel]($url)"
    replace(block.copy(text = block.text.replaceRange(start, end, text)))
    selection = TextRange(start + text.length); selectionText = blocks.first { it.id == block.id }.text; focus(block.id)
    return true
  }
  fun numberedIndex(id: String): Int {
    val index = blocks.indexOfFirst { it.id == id }
    if (index < 0) return 1
    return blocks.subList(0, index + 1).asReversed().takeWhile { it.type == "numbered" }.size.coerceAtLeast(1)
  }
  fun insertTable() {
    val id = activeBlockId ?: return
    focus(insertAfter(id, newBlock("table").copy(text = MarkdownTransforms.table(2, 2).trim())).id)
  }
  fun select(id: String, value: TextFieldValue) { activeBlockId = id; focusBlockId = id; selection = value.selection; selectionText = value.text }
  fun selectedText(): String = if (!selection.collapsed) InlineMarkdown.parse(selectionText.substring(selection.min.coerceIn(0, selectionText.length), selection.max.coerceIn(0, selectionText.length))).text else ""
  fun applyAi(snapshot: AiEditorSnapshot, content: String): Boolean {
    if (blocks != snapshot.blocks) return false
    if (snapshot.selected.isBlank()) {
      val contentBlocks = NotesRepository.markdownToBlocks(content)
      val attachments = blocks.filter { it.type in setOf("image", "file") }.mapIndexed { index, block -> block.copy(position = "${(contentBlocks.size + index + 1) * 1000}.0000000000") }
      blocks = contentBlocks + attachments
    } else {
      val current = blocks.firstOrNull { it.id == snapshot.blockId } ?: return false
      val range = snapshot.range
      if (range.max > current.text.length || InlineMarkdown.parse(current.text.substring(range.min, range.max)).text != snapshot.selected) return false
      replace(current.copy(text = current.text.replaceRange(range.min, range.max, content)))
      return true
    }
    emit(); return true
  }
  fun activate(id: String) { if (activeBlockId != id) { selection = TextRange.Zero; selectionText = "" }; activeBlockId = id }
  fun edit(id: String, value: String) {
    typing = true
    try { editContent(id, value) } finally { typing = false }
  }
  private fun editContent(id: String, value: String) {
    val block = blocks.firstOrNull { it.id == id } ?: return
    if (block.type == "code") { replace(block.copy(text = value)); return }
    if (value == "\n" && block.text.isBlank() && block.type in setOf("bullet", "numbered", "checklist")) {
      replace(block.copy(type = "paragraph", text = "", checked = false)); return
    }
    if (value.contains('\n')) {
      val source = value.replace("\r\n", "\n")
      val parts = source.split('\n')
      val hasMarkdownBlocks = parts.any { it.matches(Regex("^(#{1,3} |[-*] |[0-9]+[.] |> |```|[|]).*")) }
      if (hasMarkdownBlocks) {
        val pasted = NotesRepository.markdownToBlocks(source)
        val first = pasted.first()
        replace(block.copy(type = first.type, text = first.text, checked = first.checked, uri = first.uri))
        var after = id
        pasted.drop(1).forEach { after = insertAfter(after, it).id }
        focus(after)
      } else {
        replace(block.copy(text = parts.first()))
        var after = id
        parts.drop(1).forEach { part -> after = insertAfter(after, continuation(block, part)).id }
        focus(after)
      }
      return
    }
    val command = slashCommand(value)
    val prefix = Regex("""^(#{1,3}|>|[-*](?: \[([ xX])\])?|[0-9]+[.]) (.*)$""").matchEntire(value)
    if (command != null) replace(block.copy(type = command, text = ""))
    else if (prefix != null && block.type == "paragraph") {
      val marker = prefix.groupValues[1]
      val type = when { marker.startsWith("#") -> "heading${marker.length}"; marker == ">" -> "quote"; marker.contains("[") -> "checklist"; marker.last() == '.' -> "numbered"; else -> "bullet" }
      replace(block.copy(type = type, text = prefix.groupValues[3], checked = prefix.groupValues[2].equals("x", true)))
    } else replace(block.copy(text = value))
  }

  fun submit(id: String) {
    val block = blocks.firstOrNull { it.id == id } ?: return
    if (block.text.isBlank() && block.type in setOf("bullet", "numbered", "checklist")) {
      replace(block.copy(type = "paragraph", checked = false)); return
    }
    focus(insertAfter(id, continuation(block, "")).id)
  }

  fun backspaceOnEmpty(id: String) {
    val index = blocks.indexOfFirst { it.id == id }
    if (index <= 0 || blocks.getOrNull(index)?.text?.isNotBlank() == true) return
    val previous = blocks[index - 1]
    blocks = blocks.toMutableList().also { it.removeAt(index) }
    emit(); focus(previous.id)
  }

  fun transformActive(type: String) {
    val id = activeBlockId ?: return
    val current = blocks.firstOrNull { it.id == id } ?: return
    replace(current.copy(type = type, checked = false))
  }

  fun insertDivider() { activeBlockId?.let { focus(insertAfter(it, newBlock("divider")).id) } }
  fun insertImage(uri: String, width: Int?, height: Int?): DocumentBlock {
    val after = activeBlockId ?: blocks.lastOrNull()?.id ?: error("Document has no insertion point")
    val image = insertAfter(after, newBlock("image", uri = uri, width = width, height = height))
    focus(insertAfter(image.id, newBlock("paragraph")).id)
    return image
  }
  fun updateImageDimensions(id: String, width: Int?, height: Int?) {
    val image = blocks.firstOrNull { it.id == id } ?: return
    if (width == null || height == null || image.width == width && image.height == height) return
    replace(image.copy(width = width, height = height))
  }
  fun toggleChecked(id: String) { blocks.firstOrNull { it.id == id }?.let { replace(it.copy(checked = !it.checked)) } }
  fun updateTableCell(id: String, row: Int, column: Int, value: String) {
    val block = blocks.firstOrNull { it.id == id } ?: return
    val rows = parseLegacyTable(block.text).toMutableList()
    if (row !in rows.indices || column !in rows[row].indices) return
    rows[row] = rows[row].mapIndexed { index, cell -> if (index == column) value else cell }
    replace(block.copy(text = tableToMarkdown(rows)))
  }
  fun addTableRow(id: String) {
    val block = blocks.firstOrNull { it.id == id } ?: return
    val rows = parseLegacyTable(block.text).toMutableList()
    val columns = rows.maxOfOrNull { it.size }?.coerceAtLeast(2) ?: 2
    rows += List(columns) { "" }
    replace(block.copy(text = tableToMarkdown(rows)))
  }
  fun addTableColumn(id: String) {
    val block = blocks.firstOrNull { it.id == id } ?: return
    val rows = parseLegacyTable(block.text).toMutableList()
    if (rows.isEmpty()) rows += listOf("Колонка")
    val next = rows.mapIndexed { index, row -> row + if (index == 0) "Колонка ${row.size + 1}" else "" }
    replace(block.copy(text = tableToMarkdown(next)))
  }
  fun delete(id: String) {
    if (blocks.size == 1) { replace(blocks.single().copy(type = "paragraph", text = "", uri = null)); return }
    val i = blocks.indexOfFirst { it.id == id }; val target = blocks.getOrNull((i - 1).coerceAtLeast(0)) ?: return
    blocks = blocks.filterNot { it.id == id }; emit(); focus(target.id)
  }

  private fun replace(block: DocumentBlock) { blocks = blocks.map { if (it.id == block.id) block else it }; emit() }
  private fun insertAfter(afterId: String, block: DocumentBlock): DocumentBlock {
    val i = blocks.indexOfFirst { it.id == afterId }.coerceAtLeast(0)
    val before = blocks[i].position.toDoubleOrNull() ?: (i + 1) * 1000.0
    val next = blocks.getOrNull(i + 1)?.position?.toDoubleOrNull()
    val positioned = block.copy(position = String.format(Locale.US, "%.10f", if (next != null) (before + next) / 2 else before + 1000.0))
    blocks = blocks.toMutableList().also { it.add(i + 1, positioned) }; emit(); return positioned
  }
  private fun focus(id: String) { activeBlockId = id; focusBlockId = id; focusToken += 1 }
  private fun emit() {
    if (blocks != lastEmitted) {
      val now = System.nanoTime()
      val coalesced = typing && lastTypingId == activeBlockId && now - lastTypingTime < 1_000_000_000L
      if (!coalesced) { undoStack.addLast(lastEmitted); while (undoStack.size > 80) undoStack.removeFirst() }
      redoStack.clear(); canUndo = undoStack.isNotEmpty(); canRedo = false
      lastTypingId = if (typing) activeBlockId else null; lastTypingTime = now; lastEmitted = blocks
      onChanged(blocks)
    }
  }
  private fun newBlock(type: String, uri: String? = null, width: Int? = null, height: Int? = null) =
    DocumentBlock(UUID.randomUUID().toString(), "0", type, uri = uri, width = width, height = height)
  private fun continuation(from: DocumentBlock, text: String) = newBlock(
    if (from.type in setOf("bullet", "numbered", "checklist")) from.type else "paragraph"
  ).copy(text = text)
  private fun slashCommand(value: String): String? = when (value.trim().lowercase()) {
    "/heading", "/h1" -> "heading1"; "/h2" -> "heading2"; "/h3" -> "heading3"
    "/list", "/bullet" -> "bullet"; "/numbered" -> "numbered"; "/checklist" -> "checklist"
    "/quote" -> "quote"; "/code" -> "code"; else -> null
  }
}

@Composable
private fun BlockDocumentEditor(
  noteId: String,
  initialBlocks: List<DocumentBlock>,
  onDocumentChanged: (List<DocumentBlock>) -> Unit,
  onClose: () -> Unit,
  viewModel: NotesViewModel,
  modifier: Modifier = Modifier
) {
  val controller = remember(noteId) { BlockDocumentController(initialBlocks, onDocumentChanged) }
  val motionEnabled = rememberLurgMotionEnabled()
  val context = LocalContext.current
  val scope = rememberCoroutineScope()
  val keyboard = LocalSoftwareKeyboardController.current
  val focusManager = LocalFocusManager.current
  val haptic = LocalHapticFeedback.current
  var insertMenu by remember { mutableStateOf(false) }
  var aiMenu by remember { mutableStateOf(false) }
  var graphMenu by remember { mutableStateOf(false) }
  var formatMenu by remember { mutableStateOf(false) }
  var listMenu by remember { mutableStateOf(false) }
  var readMode by rememberSaveable(noteId) { mutableStateOf(false) }
  var linkMenu by remember { mutableStateOf(false) }
  var linkLabel by remember { mutableStateOf("") }
  var linkUrl by remember { mutableStateOf("https://") }
  var linkError by remember { mutableStateOf<String?>(null) }
  fun dismissMenus() { insertMenu = false; formatMenu = false; listMenu = false; aiMenu = false; graphMenu = false; viewModel.clearAiPreview() }
  fun back() {
    when {
      linkMenu -> linkMenu = false
      insertMenu || formatMenu || listMenu || aiMenu || graphMenu -> dismissMenus()
      readMode -> { readMode = false }
      else -> onClose()
    }
  }
  var aiConsent by remember { mutableStateOf<Pair<String, String>?>(null) }
  var aiSnapshot by remember { mutableStateOf<AiEditorSnapshot?>(null) }
  var applyError by remember { mutableStateOf<String?>(null) }
  fun captureAi() = AiEditorSnapshot(controller.blocks.toList(), controller.activeBlockId, controller.selection, controller.selectedText())
  var pendingCameraPath by rememberSaveable(noteId) { mutableStateOf<String?>(null) }
  var cameraError by rememberSaveable(noteId) { mutableStateOf<String?>(null) }
  fun pendingCameraUri(): Uri? = pendingCameraPath?.let { path ->
    runCatching { FileProvider.getUriForFile(context, "${context.packageName}.files", File(path)) }.getOrNull()
  }
  suspend fun addImage(uri: Uri) {
    val image = controller.insertImage(uri.toString(), null, null)
    val size = withContext(Dispatchers.IO) {
      runCatching {
        context.contentResolver.openInputStream(uri)?.use { input ->
          val options = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
          android.graphics.BitmapFactory.decodeStream(input, null, options)
          options.outWidth.takeIf { it > 0 } to options.outHeight.takeIf { it > 0 }
        }
      }.getOrNull() ?: (null to null)
    }
    controller.updateImageDimensions(image.id, size.first, size.second)
  }
  val gallery = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
    if (uri != null) {
      scope.launch {
        val local = copyImageIntoDocument(context, uri)
        if (local != null) addImage(local) else cameraError = "Не удалось сохранить фотографию на устройстве"
      }
    }
    insertMenu = false
  }
  val camera = rememberLauncherForActivityResult(DocumentCameraContract()) { saved ->
    val path = pendingCameraPath
    pendingCameraPath = null
    if (saved && path != null) {
      runCatching { FileProvider.getUriForFile(context, "${context.packageName}.files", File(path)) }.getOrNull()?.let { uri ->
        scope.launch { addImage(uri) }
      }
    } else path?.let { File(it).delete() }
    insertMenu = false
  }
  val cameraPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
    val uri = pendingCameraUri()
    if (granted && uri != null) {
      runCatching { camera.launch(uri) }.onFailure { if (it is CancellationException) throw it; cameraError = "Не удалось открыть камеру" }
    } else {
      pendingCameraPath?.let { File(it).delete() }
      pendingCameraPath = null
      cameraError = "Для съёмки нужна камера. Можно выбрать фото из галереи."
    }
  }
  BackHandler(enabled = insertMenu || formatMenu || listMenu || aiMenu || graphMenu || readMode || linkMenu) { back() }
  LaunchedEffect(readMode) { if (readMode) { focusManager.clearFocus(force = true); keyboard?.hide(); delay(100); keyboard?.hide() } }
  LaunchedEffect(noteId) { delay(80); if (!readMode) keyboard?.show() }

  Box(modifier.fillMaxSize().background(LurgVoid)) {
    LazyColumn(
      modifier = Modifier.fillMaxSize().imePadding(),
      // Keep the first document block below the persistent editor controls.
      contentPadding = androidx.compose.foundation.layout.PaddingValues(start = 22.dp, end = 22.dp, top = 98.dp, bottom = 112.dp),
      verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
      items(controller.blocks, key = { it.id }) { block ->
        AnimatedVisibility(visible = true, enter = fadeIn(LurgMotion.fast(motionEnabled)) + scaleIn(LurgMotion.surfaceSpring(motionEnabled), initialScale = .985f)) {
          if (readMode) DocumentReadBlock(block, controller) else DocumentBlockRow(block, controller, haptic, onAdd = { insertMenu = true; formatMenu = false; listMenu = false })
        }
      }
    }
    Row(Modifier.fillMaxWidth().statusBarsPadding().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
      CompactIconButton("←", "Назад", onClick = { back() })
      EditorToolButton("↶", "Отменить", enabled = controller.canUndo) { controller.undo() }
      EditorToolButton("↷", "Повторить", enabled = controller.canRedo) { controller.redo() }
      Spacer(Modifier.weight(1f))
      EditorToolButton(if (readMode) "✎" else "▤", if (readMode) "Редактировать" else "Читать") { dismissMenus(); focusManager.clearFocus(force = true); readMode = !readMode; keyboard?.hide() }
      EditorToolButton("Graph", "Личный Graph") { dismissMenus(); graphMenu = true; keyboard?.hide() }
    }
    if (aiMenu) Dialog(onDismissRequest = { aiMenu = false; viewModel.clearAiPreview() }) {
      Column(Modifier.fillMaxWidth().requiredHeightIn(max = 620.dp).verticalScroll(rememberScrollState()).clip(RoundedCornerShape(22.dp)).background(LurgPanelStrong).padding(12.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) { CompactIconButton("←", "Назад из AI") { aiMenu = false; viewModel.clearAiPreview() }; Text(if (controller.selectedText().isBlank()) "AI: вся заметка" else "AI: выделенный текст", color = LurgText, fontSize = 17.sp, modifier = Modifier.padding(start = 8.dp)) }
        Text("Текст отправляется AI только после вашего разрешения. Результат применяется отдельно.", color = LurgMuted, fontSize = 12.sp)
        val preview = viewModel.aiPreview
        if (preview != null) AiPreviewPanel(preview, onApply = {
          val snapshot = aiSnapshot
          if (snapshot != null && controller.applyAi(snapshot, preview.content)) { viewModel.clearAiPreview(); aiMenu = false; applyError = null }
          else applyError = "Заметка изменилась после запроса. Отмените результат и повторите AI."
        }, onCancel = { viewModel.clearAiPreview(); aiSnapshot = null })
        else AiActionPanel(viewModel.aiBusy, viewModel.aiError) { action, request -> aiSnapshot = captureAi(); aiConsent = action to request }
        if (applyError != null) Text(applyError.orEmpty(), color = Color(0xFFFF8CA3), fontSize = 13.sp)
      }
    }
    aiConsent?.let { (action, request) -> AlertDialog(onDismissRequest = { aiConsent = null }, title = { Text("Разрешить обработку AI?") }, text = {
      Text(if (aiSnapshot?.selected.isNullOrBlank()) "Будет отправлен текст всей заметки. Фотографии остаются на устройстве. Изменения появятся в предпросмотре." else "Будет отправлен только выделенный текст. Изменения появятся в предпросмотре.")
    }, confirmButton = { TextButton({
      val snapshot = aiSnapshot ?: captureAi()
      viewModel.runAiAction(noteId, action, NotesRepository.blocksToMarkdown(snapshot.blocks.filterNot { it.type in setOf("image", "file") }), snapshot.selected, request)
      aiConsent = null
    }) { Text("Разрешить") } }, dismissButton = { TextButton({ aiConsent = null }) { Text("Отмена") } }) }
    if (graphMenu) Dialog(onDismissRequest = { graphMenu = false }) {
      Column(Modifier.fillMaxWidth().requiredHeightIn(max = 620.dp).verticalScroll(rememberScrollState())) {
        CompactIconButton("←", "Назад из Graph") { graphMenu = false }
        GraphFactsPanel(viewModel.graphCandidates, viewModel.graphBusy, viewModel.graphError, viewModel.graphAddedCount,
          onAnalyze = { viewModel.openedNote?.let { viewModel.extractGraphFacts(it, NotesRepository.blocksToMarkdown(controller.blocks.filterNot { block -> block.type in setOf("image", "file") })) } },
          onConfirm = viewModel::confirmGraphFacts, onDismiss = { viewModel.dismissGraphFacts(reject = true); graphMenu = false })
      }
    }
    AnimatedVisibility(
      visible = insertMenu,
      enter = fadeIn(LurgMotion.fast(motionEnabled)) + scaleIn(LurgMotion.surfaceSpring(motionEnabled), initialScale = .94f) + slideInVertically(LurgMotion.standard(motionEnabled)) { it / 8 },
      exit = fadeOut(LurgMotion.fast(motionEnabled)) + scaleOut(LurgMotion.fast(motionEnabled), targetScale = .96f) + slideOutVertically(LurgMotion.fast(motionEnabled)) { it / 10 },
      modifier = Modifier.align(Alignment.BottomCenter).imePadding().navigationBarsPadding().padding(start = 16.dp, end = 16.dp, bottom = 80.dp)
    ) {
      DocumentInsertMenu(onBack = { insertMenu = false },
        onPhoto = { gallery.launch(arrayOf("image/*")) },
        onCamera = {
          cameraError = null
          val file = File(context.filesDir, "images/${UUID.randomUUID()}.jpg").apply { parentFile?.mkdirs() }
          pendingCameraPath = file.absolutePath
          val uri = pendingCameraUri()
          if (uri == null) {
            pendingCameraPath = null
            cameraError = "Не удалось подготовить место для фотографии"
          } else if (androidx.core.content.ContextCompat.checkSelfPermission(context, android.Manifest.permission.CAMERA) == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            runCatching { camera.launch(uri) }.onFailure { if (it is CancellationException) throw it; cameraError = "Не удалось открыть камеру" }
          } else {
            cameraPermission.launch(android.Manifest.permission.CAMERA)
          }
        },
        onType = { type -> controller.transformActive(type); insertMenu = false },
        onDivider = { controller.insertDivider(); insertMenu = false }
      )
    }
    if (!readMode) {
      Column(Modifier.align(Alignment.BottomCenter).imePadding().navigationBarsPadding().fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
        AnimatedVisibility(formatMenu || listMenu, enter = fadeIn(LurgMotion.fast(motionEnabled)), exit = fadeOut(LurgMotion.fast(motionEnabled))) {
          Column(Modifier.fillMaxWidth().requiredHeightIn(max = LocalConfiguration.current.screenHeightDp.dp * .43f).clip(RoundedCornerShape(18.dp)).background(LurgPanelStrong).verticalScroll(rememberScrollState()).padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
              CompactIconButton("←", "Назад из форматирования") { formatMenu = false; listMenu = false }
              Text(if (formatMenu) "Форматирование" else "Структура", color = LurgText, fontSize = 17.sp, modifier = Modifier.padding(start = 8.dp))
            }
            if (formatMenu) {
              Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                EditorToolButton("B", "Жирный") { controller.formatInline("bold") }
                EditorToolButton("I", "Курсив") { controller.formatInline("italic") }
                EditorToolButton("S̶", "Зачёркнутый") { controller.formatInline("strike") }
                EditorToolButton("‹›", "Код в строке") { controller.formatInline("code") }
                EditorToolButton("↗", "Ссылка") { linkLabel = controller.selectedText(); linkUrl = "https://"; linkError = null; linkMenu = true }
              }
              Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("Текст" to "paragraph", "H1" to "heading1", "H2" to "heading2", "H3" to "heading3").forEach { (label, type) -> EditorToolButton(label, label) { controller.transformActive(type) } }
              }
            } else {
              listOf("Без списка" to "paragraph", "С маркировкой" to "bullet", "С номерами" to "numbered", "Чек-лист" to "checklist", "Цитата" to "quote", "Блок кода" to "code").forEach { (label, type) ->
                Text(label, color = LurgText, fontSize = 16.sp, modifier = Modifier.fillMaxWidth().clickable { controller.transformActive(type); listMenu = false }.padding(12.dp))
              }
            }
          }
        }
        Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(LurgPanelStrong).padding(horizontal = 4.dp, vertical = 5.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
          EditorToolButton("AI", "AI для заметки") { dismissMenus(); aiMenu = true; keyboard?.hide() }
          EditorToolButton("Aa", "Форматирование", active = formatMenu) { formatMenu = !formatMenu; listMenu = false; insertMenu = false }
          EditorToolButton("☷", "Списки и блоки", active = listMenu) { listMenu = !listMenu; formatMenu = false; insertMenu = false }
          EditorToolButton("▦", "Вставить таблицу") { dismissMenus(); controller.insertTable(); keyboard?.hide() }
          EditorToolButton("+", "Вставить", active = insertMenu) { insertMenu = !insertMenu; formatMenu = false; listMenu = false }
          EditorToolButton("✓", "Готово") { keyboard?.hide(); dismissMenus(); onClose() }
        }
      }
    }
    if (linkMenu) AlertDialog(onDismissRequest = { linkMenu = false }, title = { Row(verticalAlignment = Alignment.CenterVertically) { CompactIconButton("←", "Назад из ссылки") { linkMenu = false }; Text("Добавить ссылку", modifier = Modifier.padding(start = 8.dp)) } }, text = {
      Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        AuthField(linkLabel, { linkLabel = it }, "Текст ссылки", KeyboardType.Text, ImeAction.Next)
        AuthField(linkUrl, { linkUrl = it }, "https://…", KeyboardType.Uri, ImeAction.Done)
        linkError?.let { Text(it, color = Color(0xFFFF8797)) }
      }
    }, confirmButton = { TextButton(onClick = { if (controller.insertLink(linkLabel, linkUrl.trim())) linkMenu = false else linkError = "Укажите ссылку http://, https:// или mailto:" }) { Text("Добавить") } }, dismissButton = { TextButton(onClick = { linkMenu = false }) { Text("Отмена") } }, containerColor = LurgPanelStrong, titleContentColor = LurgText, textContentColor = LurgText)
    AnimatedVisibility(visible = cameraError != null, enter = fadeIn(tween(120)), exit = fadeOut(tween(100)), modifier = Modifier.align(Alignment.BottomCenter).imePadding().padding(22.dp)) {
      Text(cameraError.orEmpty(), color = LurgText, fontSize = 14.sp, modifier = Modifier.clip(RoundedCornerShape(14.dp)).background(LurgPanelStrong).padding(horizontal = 14.dp, vertical = 10.dp))
    }
  }
}

/** Explicit ClipData grants keep image capture valid on Android 17 and Android 18+. */
private class DocumentCameraContract : ActivityResultContract<Uri, Boolean>() {
  override fun createIntent(context: android.content.Context, input: Uri): Intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
    putExtra(MediaStore.EXTRA_OUTPUT, input)
    clipData = ClipData.newRawUri("lurg-notes-image", input)
    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
  }

  override fun parseResult(resultCode: Int, intent: Intent?): Boolean = resultCode == android.app.Activity.RESULT_OK
}

private suspend fun copyImageIntoDocument(context: android.content.Context, source: Uri): Uri? = withContext(Dispatchers.IO) {
  runCatching {
    val destination = File(context.filesDir, "images/${UUID.randomUUID()}.jpg").apply { parentFile?.mkdirs() }
    context.contentResolver.openInputStream(source)?.use { input -> FileOutputStream(destination).use(input::copyTo) }
      ?: return@runCatching null
    FileProvider.getUriForFile(context, "${context.packageName}.files", destination)
  }.getOrNull()
}

@Composable
private fun DocumentInsertMenu(onBack: () -> Unit, onPhoto: () -> Unit, onCamera: () -> Unit, onType: (String) -> Unit, onDivider: () -> Unit) {
  Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(LurgPanelStrong).padding(14.dp)) {
    Row(verticalAlignment = Alignment.CenterVertically) { CompactIconButton("←", "Назад из вставки", onClick = onBack); Text("Вставить", color = LurgText, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 8.dp)) }
    Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) { DocumentMenuItem("Фото", "▧", onPhoto, Modifier.weight(1f)); DocumentMenuItem("Камера", "◉", onCamera, Modifier.weight(1f)); DocumentMenuItem("Заголовок", "H", { onType("heading1") }, Modifier.weight(1f)) }
    Spacer(Modifier.height(7.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) { DocumentMenuItem("Список", "☷", { onType("bullet") }, Modifier.weight(1f)); DocumentMenuItem("Чек-лист", "✓", { onType("checklist") }, Modifier.weight(1f)); DocumentMenuItem("Цитата", "❝", { onType("quote") }, Modifier.weight(1f)) }
    Spacer(Modifier.height(7.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) { DocumentMenuItem("Код", "‹›", { onType("code") }, Modifier.weight(1f)); DocumentMenuItem("H2", "H₂", { onType("heading2") }, Modifier.weight(1f)); DocumentMenuItem("Разделитель", "—", onDivider, Modifier.weight(1f)) }
  }
}

@Composable
private fun DocumentMenuItem(label: String, mark: String, onClick: () -> Unit, modifier: Modifier) {
  Column(modifier.lurgPressable(onClick).clip(RoundedCornerShape(12.dp)).background(LurgField).padding(vertical = 10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
    Text(mark, color = LurgAccent, fontSize = 18.sp); Spacer(Modifier.height(3.dp)); Text(label, color = LurgText, fontSize = 10.sp)
  }
}

@Composable
private fun EditorToolButton(label: String, description: String, enabled: Boolean = true, active: Boolean = false, onClick: () -> Unit) {
  Box(Modifier.requiredHeightIn(min = 48.dp).width(if (label == "Graph") 58.dp else 48.dp)
    .clip(RoundedCornerShape(13.dp)).background(if (active) LurgBlue.copy(alpha = .18f) else Color.Transparent)
    .semantics { contentDescription = description }.lurgPressable(onClick, enabled), contentAlignment = Alignment.Center) {
    Text(label, color = if (!enabled) LurgMuted.copy(alpha = .35f) else if (active) LurgBlue else LurgText, fontSize = if (label == "Graph") 12.sp else 20.sp)
  }
}

@Composable
private fun DocumentReadBlock(block: DocumentBlock, controller: BlockDocumentController) {
  when (block.type) {
    "image" -> {
      if (block.uri?.startsWith("content://") == true || block.uri?.startsWith("file://") == true) {
        AsyncImage(model = block.uri, contentDescription = "Изображение в заметке", contentScale = ContentScale.Fit,
          modifier = Modifier.fillMaxWidth().aspectRatio(((block.width ?: 4).toFloat() / (block.height ?: 3)).coerceIn(.55f, 2.2f)).clip(RoundedCornerShape(12.dp)))
      } else Text("Изображение по ссылке", color = LurgMuted, fontSize = 15.sp)
    }
    "file" -> Text("Прикреплённый файл", color = LurgBlue, fontSize = 16.sp)
    "numbered" -> InlineMarkdownText("${controller.numberedIndex(block.id)}. ${block.text}", style = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 18.sp, lineHeight = 27.sp))
    else -> SelectionContainer { MarkdownDocumentPreview(NotesRepository.blocksToMarkdown(listOf(block))) }
  }
}

@Composable
private fun DocumentBlockRow(block: DocumentBlock, controller: BlockDocumentController, haptic: androidx.compose.ui.hapticfeedback.HapticFeedback, onAdd: () -> Unit) {
  when (block.type) {
    "image" -> DocumentImageBlock(block, controller)
    "table" -> LegacyTableBlock(block, controller)
    "file" -> Text("Файл", color = LurgBlue, fontSize = 16.sp, modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(LurgGlass).padding(14.dp))
    "divider" -> Box(Modifier.fillMaxWidth().padding(vertical = 13.dp).height(1.dp).background(LurgMuted.copy(alpha = .35f)))
    "checklist" -> Row(verticalAlignment = Alignment.Top) {
      ChecklistToggle(checked = block.checked, onToggle = { controller.toggleChecked(block.id); haptic.performHapticFeedback(HapticFeedbackType.LongPress) })
      Spacer(Modifier.width(11.dp)); DocumentTextBlock(block, controller, Modifier.weight(1f), if (block.checked) LurgMuted else LurgText, 19, FontWeight.Normal, "Задача", onAdd)
    }
    "bullet" -> Row(verticalAlignment = Alignment.Top) { Text("•", color = LurgAccent, fontSize = 24.sp, modifier = Modifier.width(28.dp)); DocumentTextBlock(block, controller, Modifier.weight(1f), LurgText, 18, FontWeight.Normal, "Пункт", onAdd) }
    "numbered" -> Row(verticalAlignment = Alignment.Top) { Text("${controller.numberedIndex(block.id)}.", color = LurgMuted, fontSize = 18.sp, modifier = Modifier.width(30.dp).padding(top = 3.dp)); DocumentTextBlock(block, controller, Modifier.weight(1f), LurgText, 19, FontWeight.Normal, "Пункт", onAdd) }
    "quote" -> Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(LurgField).padding(12.dp)) { Box(Modifier.width(3.dp).height(42.dp).background(LurgAccent)); Spacer(Modifier.width(12.dp)); DocumentTextBlock(block, controller, Modifier.weight(1f), LurgText, 17, FontWeight.Normal, "Цитата", onAdd) }
    "code" -> DocumentTextBlock(block, controller, Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(LurgField).padding(13.dp), LurgText, 15, FontWeight.Normal, "Код", onAdd)
    else -> DocumentTextBlock(block, controller, Modifier.fillMaxWidth(), LurgText, when (block.type) { "heading1" -> 30; "heading2" -> 24; "heading3" -> 20; else -> 18 }, if (block.type.startsWith("heading")) FontWeight.Bold else FontWeight.Normal, if (block.type.startsWith("heading")) "Заголовок" else "Текст", onAdd)
  }
}

@Composable
private fun LegacyTableBlock(block: DocumentBlock, controller: BlockDocumentController) {
  val rows = remember(block.id, block.text) { parseLegacyTable(block.text) }
  Column(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).clip(RoundedCornerShape(12.dp)).background(LurgGlass).padding(8.dp)) {
    rows.forEachIndexed { rowIndex, cells ->
      Row(Modifier.padding(vertical = 4.dp)) {
        cells.forEachIndexed { columnIndex, cell ->
          BasicTextField(
            value = cell,
            onValueChange = { controller.updateTableCell(block.id, rowIndex, columnIndex, it) },
            singleLine = false,
            textStyle = androidx.compose.ui.text.TextStyle(color = if (rowIndex == 0) LurgText else LurgMuted, fontSize = 14.sp, fontWeight = if (rowIndex == 0) FontWeight.SemiBold else FontWeight.Normal),
            cursorBrush = SolidColor(LurgBlue),
            modifier = Modifier.width(180.dp).requiredHeightIn(min = 42.dp).padding(horizontal = 6.dp, vertical = 8.dp),
            decorationBox = { inner -> if (cell.isBlank()) Text("", color = LurgMuted); inner() }
          )
        }
      }
      if (rowIndex == 0) Box(Modifier.fillMaxWidth().height(1.dp).background(LurgMuted.copy(alpha = .25f)))
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
      DocumentMenuItem("+ строка", "+", { controller.addTableRow(block.id) }, Modifier.width(120.dp))
      DocumentMenuItem("+ колонка", "+", { controller.addTableColumn(block.id) }, Modifier.width(130.dp))
    }
  }
}

private fun parseLegacyTable(markdown: String): List<List<String>> = pro.lurg.notes.editor.MarkdownTables.rows(markdown)

private fun tableToMarkdown(rows: List<List<String>>): String = pro.lurg.notes.editor.MarkdownTables.serialize(rows)

@Composable
private fun DocumentTextBlock(block: DocumentBlock, controller: BlockDocumentController, modifier: Modifier, color: Color, size: Int, weight: FontWeight, placeholder: String, onAdd: () -> Unit) {
  val requester = remember(block.id) { FocusRequester() }
  val keyboard = LocalSoftwareKeyboardController.current
  val bring = rememberBringIntoViewModifier(modifier.focusRequester(requester).requiredHeightIn(min = 32.dp))
    .onFocusEvent { if (it.isFocused) controller.activate(block.id) }
    .onPreviewKeyEvent { event -> if (event.type == KeyEventType.KeyDown && event.key == Key.Backspace && block.text.isEmpty()) { controller.backspaceOnEmpty(block.id); true } else false }
  LaunchedEffect(controller.focusToken) { if (controller.focusBlockId == block.id) { delay(24); requester.requestFocus(); keyboard?.show() } }
  var field by remember(block.id) { mutableStateOf(TextFieldValue(block.text)) }
  if (field.text != block.text) field = field.copy(text = block.text, selection = TextRange(field.selection.end.coerceAtMost(block.text.length)), composition = null)
  LaunchedEffect(controller.focusToken) {
    if (controller.focusBlockId == block.id) {
      val range = controller.selection
      field = field.copy(selection = TextRange(range.start.coerceIn(0, block.text.length), range.end.coerceIn(0, block.text.length)))
    }
  }
  BasicTextField(value = field, onValueChange = { value ->
    field = value; controller.select(block.id, value)
    if (value.text != block.text) when (value.text.trim().lowercase()) {
      "/image" -> { controller.activate(block.id); onAdd() }
      "/divider" -> { controller.activate(block.id); controller.insertDivider() }
      else -> controller.edit(block.id, value.text)
    }
  }, visualTransformation = if (block.type == "code") VisualTransformation.None else remember { InlineMarkdownTransformation() },
    textStyle = androidx.compose.ui.text.TextStyle(color = color, fontSize = size.sp, lineHeight = (size + 8).sp, fontWeight = weight,
      fontFamily = if (block.type == "code") FontFamily.Monospace else FontFamily.Default,
      textDecoration = if (block.checked) TextDecoration.LineThrough else TextDecoration.None),
    cursorBrush = SolidColor(LurgBlue), modifier = bring.fillMaxWidth().semantics { contentDescription = if (block.type.startsWith("heading")) "Заголовок заметки" else "Текст заметки" },
    decorationBox = { inner -> if (block.text.isEmpty()) Text(placeholder, color = LurgMuted.copy(alpha = .5f), fontSize = size.sp, fontWeight = weight); inner() })
}

@Composable
private fun DocumentImageBlock(block: DocumentBlock, controller: BlockDocumentController) {
  val ratio = ((block.width ?: 4).toFloat() / (block.height ?: 3).toFloat()).coerceIn(.55f, 2.2f)
  val motionEnabled = rememberLurgMotionEnabled()
  Column(Modifier.fillMaxWidth().animateContentSize(LurgMotion.standard(motionEnabled))) {
    AsyncImage(model = ImageRequest.Builder(LocalContext.current).data(block.uri).crossfade(LurgMotion.FastMillis).build(), contentDescription = "Изображение в заметке", contentScale = ContentScale.Crop, modifier = Modifier.fillMaxWidth().aspectRatio(ratio).clip(RoundedCornerShape(14.dp)).background(LurgField).lurgPressable(onClick = { controller.activate(block.id) }))
    Spacer(Modifier.height(5.dp))
    Text("Фото хранится только на этом устройстве", color = LurgMuted, fontSize = 11.sp)
    Text("+ продолжить", color = LurgMuted.copy(alpha = .7f), fontSize = 13.sp, modifier = Modifier.clickable { controller.submit(block.id) }.padding(vertical = 4.dp))
  }
}

@Composable
private fun ChecklistToggle(checked: Boolean, onToggle: () -> Unit) {
  val motionEnabled = rememberLurgMotionEnabled()
  val fill by animateColorAsState(if (checked) LurgAccent else LurgMuted.copy(alpha = .45f), LurgMotion.fast(motionEnabled), label = "check-fill")
  val scale by animateFloatAsState(if (checked) 1f else .86f, LurgMotion.checkSpring(motionEnabled), label = "check-scale")
  Box(
    Modifier.padding(top = 5.dp).size(21.dp).graphicsLayer { scaleX = scale; scaleY = scale }
      .clip(RoundedCornerShape(7.dp)).background(fill).lurgPressable(onToggle),
    contentAlignment = Alignment.Center
  ) {
    AnimatedVisibility(checked, enter = fadeIn(LurgMotion.fast(motionEnabled)) + scaleIn(LurgMotion.checkSpring(motionEnabled), initialScale = .45f), exit = fadeOut(LurgMotion.fast(motionEnabled))) {
      Text("✓", color = LurgVoid, fontSize = 14.sp, fontWeight = FontWeight.Bold)
    }
  }
}

private class VisualEditorController(initialMarkdown: String) {
  var markdown by mutableStateOf(initialMarkdown)
    private set
  var activeBlockIndex by mutableStateOf(0)
  var focusRequestIndex by mutableStateOf<Int?>(null)
    private set
  var focusRequestToken by mutableStateOf(0)
    private set
  var onChanged: ((String) -> Unit)? = null
  private val undoStack = ArrayDeque<String>()
  private val redoStack = ArrayDeque<String>()

  fun updateMarkdown(next: String) {
    if (next == markdown) return
    undoStack.addLast(markdown)
    redoStack.clear()
    markdown = next
    onChanged?.invoke(next)
  }

  fun replaceBlock(index: Int, block: VisualMdBlock) {
    val blocks = parseVisualMarkdown(markdown).toMutableList()
    if (blocks.isEmpty()) blocks += VisualMdBlock.Paragraph("")
    val safeIndex = index.coerceIn(0, blocks.lastIndex)
    blocks[safeIndex] = block
    activeBlockIndex = safeIndex
    updateBlocks(blocks)
  }

  fun insertMarkdown(value: String) {
    val next = if (markdown.isBlank()) value.trim('\n') else markdown.trimEnd() + "\n" + value.trim('\n')
    updateMarkdown(next)
    activeBlockIndex = parseVisualMarkdown(next).lastIndex.coerceAtLeast(0)
  }

  fun insertBlock(index: Int, block: VisualMdBlock) {
    val blocks = parseVisualMarkdown(markdown).toMutableList()
    val safeIndex = index.coerceIn(0, blocks.size)
    blocks.add(safeIndex, block)
    activeBlockIndex = safeIndex
    updateBlocks(blocks)
  }

  /** Enter always creates a real sibling block, never a literal newline inside a list item. */
  fun submitBlock(index: Int, value: String) {
    val blocks = parseVisualMarkdown(markdown).toMutableList()
    if (blocks.isEmpty()) blocks += VisualMdBlock.Paragraph("")
    val safeIndex = index.coerceIn(0, blocks.lastIndex)
    val current = blocks[safeIndex]
    val clean = value.replace("\n", "")
    if (current.isListItem() && clean.isBlank()) {
      blocks[safeIndex] = VisualMdBlock.Paragraph("")
      activeBlockIndex = safeIndex
      updateBlocks(blocks, safeIndex)
      return
    }
    blocks[safeIndex] = current.withEditedText(clean)
    val next = current.continuation()
    blocks.add(safeIndex + 1, next)
    activeBlockIndex = safeIndex + 1
    updateBlocks(blocks, safeIndex + 1)
  }

  /** Handles both the IME Enter key and multi-line paste without losing list structure. */
  fun editBlock(index: Int, value: String) {
    if (!value.contains('\n')) {
      val blocks = parseVisualMarkdown(markdown).toMutableList()
      if (blocks.isEmpty()) blocks += VisualMdBlock.Paragraph("")
      val safeIndex = index.coerceIn(0, blocks.lastIndex)
      replaceBlock(safeIndex, blocks[safeIndex].withEditedText(value))
      return
    }
    val blocks = parseVisualMarkdown(markdown).toMutableList()
    if (blocks.isEmpty()) blocks += VisualMdBlock.Paragraph("")
    val safeIndex = index.coerceIn(0, blocks.lastIndex)
    val current = blocks[safeIndex]
    val pieces = value.replace("\r\n", "\n").split('\n')
    if (current.isListItem() && pieces.size == 2 && pieces.all { it.isBlank() }) {
      blocks[safeIndex] = VisualMdBlock.Paragraph("")
      updateBlocks(blocks, safeIndex)
      return
    }
    blocks[safeIndex] = current.withEditedText(pieces.first())
    var insertAt = safeIndex + 1
    pieces.drop(1).forEach { piece ->
      blocks.add(insertAt, current.withEditedText(piece))
      insertAt += 1
    }
    val focusIndex = (insertAt - 1).coerceAtLeast(safeIndex)
    activeBlockIndex = focusIndex
    updateBlocks(blocks, focusIndex)
  }

  fun backspaceOnEmptyListItem(index: Int) {
    val blocks = parseVisualMarkdown(markdown).toMutableList()
    if (blocks.isEmpty()) return
    val safeIndex = index.coerceIn(0, blocks.lastIndex)
    val block = blocks[safeIndex]
    if (!block.isListItem() || block.plainText().isNotBlank()) return
    blocks[safeIndex] = if (block.listLevel() > 0) block.withLevel(block.listLevel() - 1) else VisualMdBlock.Paragraph("")
    updateBlocks(blocks, safeIndex)
  }

  fun replaceSelectedOrAll(value: String) = insertMarkdown(value)
  fun selectedTextOrAll(): String = markdown
  fun undo() {
    val previous = undoStack.pollLast() ?: return
    redoStack.addLast(markdown)
    markdown = previous
    onChanged?.invoke(previous)
  }
  fun redo() {
    val next = redoStack.pollLast() ?: return
    undoStack.addLast(markdown)
    markdown = next
    onChanged?.invoke(next)
  }

  fun paragraph() = changeActive { VisualMdBlock.Paragraph(it.plainText()) }
  fun heading(level: Int) = changeActive { VisualMdBlock.Heading(level, it.plainText()) }
  fun bullet() = changeActive { VisualMdBlock.Bullet(it.plainText()) }
  fun numbered() = changeActive { VisualMdBlock.Numbered(1, it.plainText()) }
  fun checklist() = changeActive { VisualMdBlock.Checklist(false, it.plainText()) }
  fun quote() = changeActive { VisualMdBlock.Quote(it.plainText()) }
  fun collapse() = changeActive { VisualMdBlock.Collapse(it.plainText().ifBlank { "Сворачиваемый блок" }) }
  fun bold() = changeActiveText { toggleInlineMark(it, "bold") }
  fun italic() = changeActiveText { toggleInlineMark(it, "italic") }
  fun strike() = changeActiveText { toggleInlineMark(it, "strike") }
  fun inlineCode() = changeActiveText { toggleInlineMark(it, "code") }
  fun codeBlock() = changeActive { VisualMdBlock.Code(it.plainText()) }
  fun divider() = insertBlock(activeBlockIndex + 1, VisualMdBlock.Divider)
  fun insertLatex() = insertMarkdown("$$\nE = mc^2\n$$")
  fun insertEmoji(value: String) = insertMarkdown(value)

  private fun updateBlocks(source: MutableList<VisualMdBlock>, focusIndex: Int? = null) {
    val normalized = normalizeListNumbers(source)
    updateMarkdown(normalized.joinToString("\n") { it.toMarkdown() })
    focusIndex?.let {
      focusRequestIndex = it
      focusRequestToken += 1
    }
  }

  private fun changeActive(transform: (VisualMdBlock) -> VisualMdBlock) {
    val blocks = parseVisualMarkdown(markdown).ifEmpty { listOf(VisualMdBlock.Paragraph("")) }
    val index = activeBlockIndex.coerceIn(0, blocks.lastIndex)
    replaceBlock(index, transform(blocks[index]))
  }

  private fun changeActiveText(transform: (String) -> String) {
    changeActive {
      when (it) {
        is VisualMdBlock.Paragraph -> it.copy(text = transform(it.text))
        is VisualMdBlock.Heading -> it.copy(text = transform(it.text))
        is VisualMdBlock.Bullet -> it.copy(text = transform(it.text))
        is VisualMdBlock.Numbered -> it.copy(text = transform(it.text))
        is VisualMdBlock.Checklist -> it.copy(text = transform(it.text))
        is VisualMdBlock.Quote -> it.copy(text = transform(it.text))
        is VisualMdBlock.Collapse -> it.copy(text = transform(it.text))
        is VisualMdBlock.Code -> VisualMdBlock.Paragraph(transform(it.text))
        VisualMdBlock.Divider -> VisualMdBlock.Paragraph("")
        is VisualMdBlock.Attachment -> VisualMdBlock.Paragraph(transform(it.target))
        is VisualMdBlock.Table -> VisualMdBlock.Paragraph(transform(it.plainText()))
      }
    }
  }
}

private sealed class VisualMdBlock {
  data class Paragraph(val text: String) : VisualMdBlock()
  data class Heading(val level: Int, val text: String) : VisualMdBlock()
  data class Bullet(val text: String, val level: Int = 0) : VisualMdBlock()
  data class Numbered(val number: Int, val text: String, val level: Int = 0) : VisualMdBlock()
  data class Checklist(val done: Boolean, val text: String, val level: Int = 0) : VisualMdBlock()
  data class Quote(val text: String) : VisualMdBlock()
  data class Collapse(val text: String) : VisualMdBlock()
  data class Code(val text: String) : VisualMdBlock()
  data object Divider : VisualMdBlock()
  data class Attachment(val kind: String, val target: String) : VisualMdBlock()
  data class Table(val rows: List<List<String>>) : VisualMdBlock()

  fun plainText(): String = when (this) {
    is Paragraph -> cleanInline(text)
    is Heading -> cleanInline(text)
    is Bullet -> cleanInline(text)
    is Numbered -> cleanInline(text)
    is Checklist -> cleanInline(text)
    is Quote -> cleanInline(text)
    is Collapse -> cleanInline(text)
    is Code -> text
    Divider -> ""
    is Attachment -> target
    is Table -> rows.flatten().joinToString(" ").trim()
  }

  fun toMarkdown(): String = when (this) {
    is Paragraph -> text
    is Heading -> "${"#".repeat(level.coerceIn(1, 3))} $text"
    is Bullet -> "${"  ".repeat(level)}- $text"
    is Numbered -> "${"  ".repeat(level)}$number. $text"
    is Checklist -> "${"  ".repeat(level)}- [${if (done) "x" else " "}] $text"
    is Quote -> text.lineSequence().joinToString("\n") { "> $it" }
    is Collapse -> "> [!collapse]\n" + text.lineSequence().joinToString("\n") { "> $it" }
    is Code -> "```\n$text\n```"
    Divider -> "---"
    is Attachment -> when (kind) {
      "image" -> "![Изображение]($target)"
      "video" -> "[Видео]($target)"
      "pdf" -> "[PDF]($target)"
      else -> "[Файл]($target)"
    }
    is Table -> visualCellsToMarkdown(rows)
  }

  fun isListItem(): Boolean = this is Bullet || this is Numbered || this is Checklist
  fun listLevel(): Int = when (this) {
    is Bullet -> level
    is Numbered -> level
    is Checklist -> level
    else -> 0
  }
  fun withLevel(nextLevel: Int): VisualMdBlock = when (this) {
    is Bullet -> copy(level = nextLevel.coerceAtLeast(0))
    is Numbered -> copy(level = nextLevel.coerceAtLeast(0))
    is Checklist -> copy(level = nextLevel.coerceAtLeast(0))
    else -> this
  }
  fun withText(value: String): VisualMdBlock = when (this) {
    is Paragraph -> copy(text = value)
    is Heading -> copy(text = value)
    is Bullet -> copy(text = value)
    is Numbered -> copy(text = value)
    is Checklist -> copy(text = value)
    is Quote -> copy(text = value)
    is Collapse -> copy(text = value)
    is Code -> copy(text = value)
    Divider -> this
    is Attachment -> this
    is Table -> this
  }
  fun withEditedText(value: String): VisualMdBlock = when (this) {
    is Paragraph -> copy(text = applyInlineStyle(text, value))
    is Heading -> copy(text = applyInlineStyle(text, value))
    is Bullet -> copy(text = applyInlineStyle(text, value))
    is Numbered -> copy(text = applyInlineStyle(text, value))
    is Checklist -> copy(text = applyInlineStyle(text, value))
    is Quote -> copy(text = applyInlineStyle(text, value))
    is Collapse -> copy(text = applyInlineStyle(text, value))
    is Code -> copy(text = value)
    Divider -> this
    is Attachment -> this
    is Table -> this
  }
  fun continuation(): VisualMdBlock = when (this) {
    is Bullet -> Bullet("", level)
    is Numbered -> Numbered(number + 1, "", level)
    is Checklist -> Checklist(false, "", level)
    else -> Paragraph("")
  }
}

private fun normalizeListNumbers(blocks: List<VisualMdBlock>): MutableList<VisualMdBlock> {
  val counters = mutableMapOf<Int, Int>()
  var previousWasNumbered = false
  return blocks.map { block ->
    when (block) {
      is VisualMdBlock.Numbered -> {
        if (!previousWasNumbered) counters.clear()
        val next = (counters[block.level] ?: 0) + 1
        counters[block.level] = next
        counters.keys.filter { it > block.level }.toList().forEach(counters::remove)
        previousWasNumbered = true
        block.copy(number = next)
      }
      is VisualMdBlock.Bullet, is VisualMdBlock.Checklist -> {
        previousWasNumbered = false
        counters.clear()
        block
      }
      else -> {
        previousWasNumbered = false
        counters.clear()
        block
      }
    }
  }.toMutableList()
}

@Composable
private fun VisualMarkdownEditor(
  controller: VisualEditorController,
  title: String,
  onTitleChange: (String) -> Unit,
  autoFocus: Boolean,
  modifier: Modifier = Modifier
) {
  val blocks = parseVisualMarkdown(controller.markdown).ifEmpty { listOf(VisualMdBlock.Paragraph("")) }
  val focusRequester = remember { FocusRequester() }
  val keyboard = LocalSoftwareKeyboardController.current
  LaunchedEffect(autoFocus) {
    if (autoFocus) {
      delay(250)
      focusRequester.requestFocus()
      keyboard?.show()
    }
  }
  Column(
    modifier
      .imePadding()
      .verticalScroll(rememberScrollState())
      .padding(start = 24.dp, end = 24.dp, top = 126.dp, bottom = 280.dp),
    verticalArrangement = Arrangement.spacedBy(8.dp)
  ) {
    DocumentTitleField(title = title, onTitleChange = onTitleChange)
    Spacer(Modifier.height(2.dp))
    blocks.forEachIndexed { index, block ->
      VisualBlock(
        block = block,
        index = index,
        controller = controller,
        focusRequester = if (index == 0) focusRequester else null,
        focusToken = if (controller.focusRequestIndex == index) controller.focusRequestToken else null
      )
    }
  }
}

@Composable
private fun DocumentTitleField(title: String, onTitleChange: (String) -> Unit) {
  val fieldModifier = rememberBringIntoViewModifier(
    Modifier.fillMaxWidth().requiredHeightIn(min = 58.dp)
  )
  Column(Modifier.fillMaxWidth()) {
    BasicTextField(
      value = title,
      onValueChange = onTitleChange,
      singleLine = false,
      textStyle = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 34.sp, fontWeight = FontWeight.Bold),
      cursorBrush = SolidColor(LurgText),
      modifier = fieldModifier,
      decorationBox = { inner ->
        if (title.isBlank()) Text("Название", color = LurgMuted.copy(alpha = 0.55f), fontSize = 34.sp, fontWeight = FontWeight.Bold)
        inner()
      }
    )
    Spacer(Modifier.height(12.dp))
    Box(Modifier.fillMaxWidth().height(1.dp).background(LurgMuted.copy(alpha = 0.22f)))
  }
}

@Composable
private fun InsertLine(index: Int, controller: VisualEditorController) {
  var value by remember(controller.markdown, index) { mutableStateOf("") }
  val fieldModifier = rememberBringIntoViewModifier(
    Modifier.fillMaxWidth().height(32.dp)
  )
  BasicTextField(
    value = value,
    onValueChange = {
      value = it
      if (it.isNotBlank()) {
        controller.insertBlock(index, VisualMdBlock.Paragraph(it))
        value = ""
      }
    },
    singleLine = true,
    textStyle = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 18.sp),
    cursorBrush = SolidColor(LurgText),
    modifier = fieldModifier,
    decorationBox = { inner ->
      if (value.isBlank()) Text("", color = LurgMuted, fontSize = 18.sp)
      inner()
    }
  )
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun rememberBringIntoViewModifier(base: Modifier): Modifier {
  val requester = remember { BringIntoViewRequester() }
  val scope = rememberCoroutineScope()
  return base
    .bringIntoViewRequester(requester)
    .onFocusEvent { state ->
      if (state.isFocused) {
        scope.launch {
          delay(90)
          requester.bringIntoView()
        }
      }
    }
}

@Composable
private fun VisualBlock(
  block: VisualMdBlock,
  index: Int,
  controller: VisualEditorController,
  focusRequester: FocusRequester?,
  focusToken: Int?
) {
  when (block) {
    is VisualMdBlock.Paragraph -> VisualTextBlock(block.text, 19, FontWeight.Normal, "Текст", focusRequester = focusRequester, focusToken = focusToken, onSubmit = { controller.editBlock(index, it) }) {
      controller.replaceBlock(index, VisualMdBlock.Paragraph(it))
    }
    is VisualMdBlock.Heading -> VisualTextBlock(block.text, when (block.level) { 1 -> 34; 2 -> 28; else -> 23 }, FontWeight.Bold, "Заголовок", focusToken = focusToken, onSubmit = { controller.editBlock(index, it) }) {
      controller.replaceBlock(index, block.copy(text = it))
    }
    is VisualMdBlock.Bullet -> Row(Modifier.padding(start = (block.level * 18).dp), verticalAlignment = Alignment.Top) {
      Text("•", color = LurgBlue, fontSize = 24.sp, modifier = Modifier.width(28.dp).padding(top = 2.dp))
      VisualTextBlock(block.text, 19, FontWeight.Normal, "Пункт", Modifier.weight(1f), focusToken = focusToken, onSubmit = { controller.editBlock(index, it) }, onEmptyBackspace = { controller.backspaceOnEmptyListItem(index) }) {
        controller.replaceBlock(index, block.copy(text = it))
      }
    }
    is VisualMdBlock.Numbered -> Row(Modifier.padding(start = (block.level * 18).dp), verticalAlignment = Alignment.Top) {
      Text("${block.number}.", color = LurgMuted, fontSize = 19.sp, modifier = Modifier.width(34.dp).padding(top = 4.dp))
      VisualTextBlock(block.text, 19, FontWeight.Normal, "Пункт", Modifier.weight(1f), focusToken = focusToken, onSubmit = { controller.editBlock(index, it) }, onEmptyBackspace = { controller.backspaceOnEmptyListItem(index) }) {
        controller.replaceBlock(index, block.copy(text = it))
      }
    }
    is VisualMdBlock.Checklist -> Row(Modifier.padding(start = (block.level * 18).dp), verticalAlignment = Alignment.Top) {
      Text(if (block.done) "☑" else "☐", color = if (block.done) LurgBlue else LurgMuted, fontSize = 24.sp, modifier = Modifier.width(36.dp).clickable {
        controller.replaceBlock(index, block.copy(done = !block.done))
      })
      VisualTextBlock(block.text, 19, FontWeight.Normal, "Задача", Modifier.weight(1f), focusToken = focusToken, onSubmit = { controller.editBlock(index, it) }, onEmptyBackspace = { controller.backspaceOnEmptyListItem(index) }) {
        controller.replaceBlock(index, block.copy(text = it))
      }
    }
    is VisualMdBlock.Quote -> Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(LurgGlass).padding(12.dp)) {
      Box(Modifier.width(3.dp).height(42.dp).clip(RoundedCornerShape(2.dp)).background(LurgBlue))
      VisualTextBlock(block.text, 18, FontWeight.Normal, "Цитата", Modifier.weight(1f).padding(start = 12.dp)) {
        controller.replaceBlock(index, block.copy(text = it))
      }
    }
    is VisualMdBlock.Collapse -> {
      var expanded by remember(index, block.text) { mutableStateOf(true) }
      Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(LurgGlass).padding(12.dp)) {
        Row(
          Modifier.fillMaxWidth().clickable { expanded = !expanded },
          verticalAlignment = Alignment.CenterVertically
        ) {
          Text(if (expanded) "⌄" else "›", color = LurgBlue, fontSize = 22.sp, modifier = Modifier.width(30.dp))
          Text("Сворачиваемый блок", color = LurgMuted, fontSize = 14.sp, fontWeight = FontWeight.Bold)
        }
        AnimatedVisibility(visible = expanded) {
          VisualTextBlock(block.text, 18, FontWeight.Normal, "Текст", Modifier.fillMaxWidth().padding(top = 8.dp)) {
            controller.replaceBlock(index, block.copy(text = it))
          }
        }
      }
    }
    is VisualMdBlock.Code -> {
      val fieldModifier = rememberBringIntoViewModifier(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(LurgGlass).padding(14.dp)
      )
      BasicTextField(
        value = block.text,
        onValueChange = { controller.replaceBlock(index, block.copy(text = it)) },
        textStyle = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 16.sp, fontWeight = FontWeight.Normal),
        cursorBrush = SolidColor(LurgText),
        modifier = fieldModifier
      )
    }
    VisualMdBlock.Divider -> Box(
      Modifier.fillMaxWidth().height(1.dp).padding(vertical = 13.dp).background(LurgMuted.copy(alpha = 0.34f))
    )
    is VisualMdBlock.Attachment -> AttachmentPreview(
      when (block.kind) {
        "image" -> "Изображение"
        "video" -> "Видео"
        "pdf" -> "PDF"
        else -> "Файл"
      },
      block.target
    )
    is VisualMdBlock.Table -> VisualTableEditor(block, index, controller)
  }
}

@Composable
private fun VisualTextBlock(
  value: String,
  fontSize: Int,
  weight: FontWeight,
  placeholder: String,
  modifier: Modifier = Modifier.fillMaxWidth(),
  focusRequester: FocusRequester? = null,
  focusToken: Int? = null,
  onSubmit: ((String) -> Unit)? = null,
  onEmptyBackspace: (() -> Unit)? = null,
  onChange: (String) -> Unit
) {
  val ownedFocusRequester = remember { FocusRequester() }
  val actualFocusRequester = focusRequester ?: ownedFocusRequester
  val fieldModifier = modifier.focusRequester(actualFocusRequester)
  val inlineStyle = remember(value, weight) { inlineStyleFor(value, weight) }
  val bringModifier = rememberBringIntoViewModifier(fieldModifier.requiredHeightIn(min = 32.dp))
    .onPreviewKeyEvent { event ->
      if (event.type == KeyEventType.KeyDown && event.key == Key.Backspace && inlineStyle.text.isEmpty() && onEmptyBackspace != null) {
        onEmptyBackspace()
        true
      } else false
    }
  val keyboard = LocalSoftwareKeyboardController.current
  LaunchedEffect(focusToken) {
    if (focusToken != null) {
      delay(32)
      actualFocusRequester.requestFocus()
      keyboard?.show()
    }
  }
  BasicTextField(
    value = inlineStyle.text,
    onValueChange = { next ->
      if (next.contains('\n') && onSubmit != null) onSubmit(next)
      else onChange(applyInlineStyle(value, next))
    },
    textStyle = androidx.compose.ui.text.TextStyle(
      color = LurgText,
      fontSize = fontSize.sp,
      fontWeight = inlineStyle.weight,
      fontStyle = inlineStyle.fontStyle,
      textDecoration = inlineStyle.textDecoration
    ),
    cursorBrush = SolidColor(LurgText),
    singleLine = false,
    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Default),
    modifier = bringModifier,
    decorationBox = { inner ->
      if (value.isBlank()) Text(placeholder, color = LurgMuted.copy(alpha = 0.55f), fontSize = fontSize.sp, fontWeight = weight)
      inner()
    }
  )
}

@Composable
private fun VisualTableEditor(block: VisualMdBlock.Table, index: Int, controller: VisualEditorController) {
  Column(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).clip(RoundedCornerShape(14.dp))) {
    block.rows.forEachIndexed { rowIndex, row ->
      Row {
        row.forEachIndexed { colIndex, cell ->
          BasicTextField(
            value = cleanInline(cell),
            onValueChange = { next ->
              val rows = block.rows.mapIndexed { r, source ->
                if (r != rowIndex) source else source.mapIndexed { c, old -> if (c == colIndex) next else old }
              }
              controller.replaceBlock(index, block.copy(rows = rows))
            },
            textStyle = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 15.sp, fontWeight = if (rowIndex == 0) FontWeight.Bold else FontWeight.Normal),
            cursorBrush = SolidColor(LurgText),
            modifier = rememberBringIntoViewModifier(
              Modifier.width(142.dp).height(48.dp)
                .background(if (rowIndex == 0) LurgBlue.copy(alpha = 0.20f) else LurgPanel)
                .padding(horizontal = 10.dp, vertical = 9.dp)
            )
          )
        }
      }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
      PanelAction("+ строка", Modifier.width(110.dp)) {
        val columns = block.rows.firstOrNull()?.size?.coerceAtLeast(2) ?: 2
        controller.replaceBlock(index, block.copy(rows = block.rows + listOf(List(columns) { "" })))
      }
      PanelAction("+ колонка", Modifier.width(120.dp)) {
        controller.replaceBlock(index, block.copy(rows = block.rows.mapIndexed { r, row -> row + if (r == 0) "Колонка ${row.size + 1}" else "" }))
      }
    }
  }
}

private fun parseVisualMarkdown(markdown: String): List<VisualMdBlock> {
  val lines = markdown.lines()
  val blocks = mutableListOf<VisualMdBlock>()
  var index = 0
  while (index < lines.size) {
    val line = lines[index]
    val trimmed = line.trim()
    // Syntax checks use a trimmed copy; block content keeps the user's spaces.
    val content = line.trimStart()
    val listLevel = ((line.length - line.trimStart().length) / 2).coerceAtLeast(0)
    when {
      trimmed.isBlank() -> index += 1
      isTableStart(lines, index) -> {
        val tableLines = mutableListOf<String>()
        while (index < lines.size && lines[index].trim().startsWith("|")) {
          tableLines += lines[index]
          index += 1
        }
        blocks += VisualMdBlock.Table(parseTableRows(tableLines))
      }
      trimmed.startsWith("```") -> {
        val code = mutableListOf<String>()
        index += 1
        while (index < lines.size && !lines[index].trim().startsWith("```")) {
          code += lines[index]
          index += 1
        }
        if (index < lines.size) index += 1
        blocks += VisualMdBlock.Code(code.joinToString("\n"))
      }
      Regex("^#{1,6}\\s+.+").matches(trimmed) -> {
        val markers = trimmed.takeWhile { it == '#' }.length
        blocks += VisualMdBlock.Heading(markers.coerceIn(1, 3), content.drop(markers).trimStart())
        index += 1
      }
      trimmed.startsWith("- [x] ", ignoreCase = true) -> {
        blocks += VisualMdBlock.Checklist(true, content.drop(6), listLevel)
        index += 1
      }
      trimmed.startsWith("[x] ", ignoreCase = true) -> {
        blocks += VisualMdBlock.Checklist(true, content.drop(4).trimStart(), listLevel)
        index += 1
      }
      trimmed.startsWith("- [ ] ") -> {
        blocks += VisualMdBlock.Checklist(false, content.drop(6), listLevel)
        index += 1
      }
      trimmed.startsWith("[ ] ") || trimmed.startsWith("[]") -> {
        blocks += VisualMdBlock.Checklist(false, content.removePrefix("[ ]").removePrefix("[]").trimStart(), listLevel)
        index += 1
      }
      trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.startsWith("+ ") -> {
        blocks += VisualMdBlock.Bullet(content.drop(2), listLevel)
        index += 1
      }
      Regex("^\\d+\\.\\s*.*").matches(trimmed) -> {
        val number = content.substringBefore(".").toIntOrNull() ?: 1
        blocks += VisualMdBlock.Numbered(number, content.substringAfter(".").trimStart(), listLevel)
        index += 1
      }
      trimmed.matches(Regex("^(-{3,}|\\*{3,}|_{3,})\\s*")) -> {
        blocks += VisualMdBlock.Divider
        index += 1
      }
      trimmed.equals("> [!collapse]", ignoreCase = true) -> {
        val collapseLines = mutableListOf<String>()
        index += 1
        while (index < lines.size && lines[index].trim().startsWith(">")) {
          collapseLines += lines[index].trim().removePrefix(">").trimStart()
          index += 1
        }
        blocks += VisualMdBlock.Collapse(collapseLines.joinToString("\n").trim())
      }
      trimmed.startsWith(">") -> {
        val quoteLines = mutableListOf<String>()
        while (index < lines.size && lines[index].trim().startsWith(">")) {
          quoteLines += lines[index].trim().removePrefix(">").removePrefix(" ")
          index += 1
        }
        blocks += VisualMdBlock.Quote(quoteLines.joinToString("\n"))
      }
      trimmed.startsWith("![") -> {
        blocks += VisualMdBlock.Attachment("image", extractLinkTarget(trimmed))
        index += 1
      }
      trimmed.startsWith("[Видео]") -> {
        blocks += VisualMdBlock.Attachment("video", extractLinkTarget(trimmed))
        index += 1
      }
      trimmed.startsWith("[PDF]") -> {
        blocks += VisualMdBlock.Attachment("pdf", extractLinkTarget(trimmed))
        index += 1
      }
      else -> {
        blocks += VisualMdBlock.Paragraph(content)
        index += 1
      }
    }
  }
  return blocks
}

private fun parseTableRows(tableLines: List<String>): List<List<String>> {
  val rows = tableLines
    .filterNot { it.trim().matches(Regex("^\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)+\\|?$")) }
    .map { line -> splitMarkdownTableRow(line).map { it.trim() } }
    .filter { it.isNotEmpty() }
  val columns = rows.maxOfOrNull { it.size }?.coerceAtLeast(2) ?: 2
  return rows.map { it + List(columns - it.size) { "" } }.ifEmpty {
    listOf(List(columns) { col -> "Колонка ${col + 1}" }, List(columns) { "" })
  }
}

/** Splits only unescaped cell separators, so URLs and pasted text cannot corrupt a table. */
private fun splitMarkdownTableRow(line: String): List<String> {
  val source = line.trim().trim('|')
  val cells = mutableListOf<String>()
  val cell = StringBuilder()
  var escaped = false
  source.forEach { char ->
    when {
      escaped -> {
        cell.append(char)
        escaped = false
      }
      char == '\\' -> escaped = true
      char == '|' -> {
        cells += cell.toString()
        cell.clear()
      }
      else -> cell.append(char)
    }
  }
  if (escaped) cell.append('\\')
  cells += cell.toString()
  return cells
}

private fun visualCellsToMarkdown(cells: List<List<String>>): String {
  val normalized = cells.ifEmpty { listOf(listOf("Колонка 1", "Колонка 2"), listOf("", "")) }
  val columns = normalized.maxOf { it.size }.coerceAtLeast(2)
  val padded = normalized.map { row -> row + List(columns - row.size) { "" } }
  val header = padded.first().mapIndexed { index, value -> value.ifBlank { "Колонка ${index + 1}" } }
  val body = padded.drop(1).ifEmpty { listOf(List(columns) { "" }) }
  return buildString {
    append("|")
    append(header.joinToString("|") { " ${it.replace("|", "\\|")} " })
    append("|\n|")
    append(List(columns) { " --- " }.joinToString("|"))
    append("|\n")
    body.forEach { row ->
      append("|")
      append(row.joinToString("|") { " ${it.replace("|", "\\|")} " })
      append("|\n")
    }
  }.trimEnd()
}

private fun extractMarkdownTitle(markdown: String): String {
  return markdown.lineSequence()
    .firstOrNull { it.trim().isNotBlank() }
    ?.trim()
    ?.takeIf { it.startsWith("# ") }
    ?.removePrefix("# ")
    ?.trim()
    .orEmpty()
}

private fun displayNoteTitle(note: NoteEntity): String {
  leadingContentTitle(note.markdown)?.let { return it }
  val stored = note.title.trim()
  if (looksLikeGeneratedTableTitle(stored)) return "Таблица"
  if (stored.isNotBlank() && !looksLikeRawMarkdownTitle(stored)) return stored.take(80)
  return fallbackTitleFromMarkdown(note.markdown).ifBlank { "Без названия" }
}

private fun looksLikeGeneratedTableTitle(value: String): Boolean {
  if (value.isBlank()) return false
  val normalized = value.lowercase()
  return Regex("колонка\\s*\\d+").findAll(normalized).take(2).count() >= 2 ||
    Regex("column\\s*\\d+").findAll(normalized).take(2).count() >= 2
}

private fun leadingContentTitle(markdown: String): String? {
  val lines = markdown.lines()
  var index = 0
  while (index < lines.size) {
    val trimmed = lines[index].trim()
    when {
      trimmed.isBlank() -> Unit
      trimmed.startsWith("# ") -> return null
      trimmed.startsWith("|") && index + 1 < lines.size && lines[index + 1].contains("---") -> return "Таблица"
      trimmed.startsWith("```") -> return "Код"
      trimmed.startsWith("![") -> return "Изображение"
      trimmed.startsWith("[Видео]") -> return "Видео"
      trimmed.startsWith("[PDF]") -> return "PDF"
      trimmed.contains("```") && trimmed.length <= 24 -> return "Код"
      trimmed.count { it == '|' } >= 2 -> return "Таблица"
      else -> return null
    }
    index += 1
  }
  return null
}

private fun looksLikeRawMarkdownTitle(value: String): Boolean {
  val trimmed = value.trim()
  return trimmed.startsWith("|") ||
    trimmed.startsWith("```") ||
    trimmed.startsWith(",,") ||
    trimmed.startsWith(",") ||
    trimmed.contains("||||") ||
    trimmed.contains("---|") ||
    trimmed.contains("**") ||
    trimmed.contains("~~") ||
    trimmed.startsWith("![") ||
    trimmed.startsWith("[Видео]") ||
    trimmed.startsWith("[PDF]")
}

private fun fallbackTitleFromMarkdown(markdown: String): String {
  val lines = markdown.lines()
  var index = 0
  while (index < lines.size) {
    val trimmed = lines[index].trim()
    when {
      trimmed.isBlank() -> Unit
      trimmed.startsWith("# ") -> return cleanInline(trimmed.removePrefix("# ")).trim().take(80)
      trimmed.startsWith("|") && index + 1 < lines.size && lines[index + 1].contains("---") -> return "Таблица"
      trimmed.startsWith("```") -> {
        index += 1
        while (index < lines.size && !lines[index].trim().startsWith("```")) {
          val codeLine = lines[index].trim()
          if (codeLine.isNotBlank()) return "Код"
          index += 1
        }
      }
      trimmed.startsWith("![") -> return "Изображение"
      trimmed.startsWith("[Видео]") -> return "Видео"
      trimmed.startsWith("[PDF]") -> return "PDF"
      else -> {
        val text = cleanInline(
          trimmed
            .replace(Regex("^#{1,6}\\s+"), "")
            .replace(Regex("^\\[[ xX]\\]\\s*"), "")
            .replace(Regex("^[-*]\\s+\\[[ xX]\\]\\s+"), "")
            .replace(Regex("^[-*]\\s+"), "")
            .replace(Regex("^\\d+\\.\\s+"), "")
            .replace(Regex("^>\\s+"), "")
        ).trim()
        if (text.isNotBlank()) return text.take(80)
      }
    }
    index += 1
  }
  return ""
}

private fun stripLeadingTitle(markdown: String): String {
  val lines = markdown.lines().toMutableList()
  val firstContent = lines.indexOfFirst { it.trim().isNotBlank() }
  if (firstContent >= 0 && lines[firstContent].trim().startsWith("# ")) {
    lines.removeAt(firstContent)
    while (lines.firstOrNull()?.isBlank() == true) lines.removeAt(0)
  }
  return lines.joinToString("\n").trim('\n')
}

private fun stripDuplicateBodyTitle(markdown: String, title: String): String {
  val cleanTitle = cleanInline(title).trim()
  if (cleanTitle.isBlank()) return markdown
  val lines = markdown.lines().toMutableList()
  val firstContent = lines.indexOfFirst { it.trim().isNotBlank() }
  if (firstContent < 0) return markdown
  val first = lines[firstContent].trim()
  val firstText = cleanInline(
    if (first.startsWith("# ")) first.replace(Regex("^#{1,6}\\s+"), "") else first
  ).trim()
  if (!firstText.equals(cleanTitle, ignoreCase = true)) return markdown
  lines.removeAt(firstContent)
  while (lines.firstOrNull()?.isBlank() == true) lines.removeAt(0)
  return lines.joinToString("\n").trim('\n')
}

private fun composeNoteMarkdown(title: String, body: String): String {
  val cleanTitle = title.trim()
  val cleanBody = body.trim('\n')
  return when {
    cleanTitle.isBlank() -> cleanBody
    cleanBody.isBlank() -> "# $cleanTitle"
    else -> "# $cleanTitle\n\n$cleanBody"
  }
}

@Composable
private fun BottomToolbar(active: String?, onPanel: (String) -> Unit, onSend: () -> Unit, modifier: Modifier = Modifier) {
  Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
    IslandButton("☺", modifier = Modifier.size(50.dp)) { onPanel("emoji") }
    IslandButton("✦AI", modifier = Modifier.width(60.dp).height(50.dp)) { onPanel("ai") }
    Row(
      Modifier.width(176.dp).height(50.dp).clip(RoundedCornerShape(25.dp)).background(LurgGlass).padding(horizontal = 4.dp),
      verticalAlignment = Alignment.CenterVertically,
      horizontalArrangement = Arrangement.Center
    ) {
      ToolbarIcon("Aa", active == "type", Modifier.weight(1f)) { onPanel("type") }
      ToolbarIcon("☷", active == "list", Modifier.weight(1f)) { onPanel("list") }
      ToolbarIcon("▦", active == "table", Modifier.weight(1f)) { onPanel("table") }
      ToolbarIcon("Σ", active == "formula", Modifier.weight(1f)) { onPanel("formula") }
    }
    IslandButton("⌇", modifier = Modifier.size(50.dp)) { onPanel("attach") }
    IslandButton("➤", modifier = Modifier.size(55.dp), fill = LurgBlue) { onSend() }
  }
}

@Composable
private fun FormatPanel(
  panel: String,
  editor: VisualEditorController,
  note: NoteEntity,
  aiBusy: Boolean,
  aiError: String?,
  onAi: (String, String) -> Unit,
  onPickImage: () -> Unit,
  onPickVideo: () -> Unit,
  onPickPdf: () -> Unit,
  graphCandidates: List<NotesGraphCandidate>?,
  graphBusy: Boolean,
  graphError: String?,
  graphAddedCount: Int,
  onGraphAnalyze: () -> Unit,
  onGraphConfirm: (List<NotesGraphCandidate>) -> Unit,
  onGraphDismiss: () -> Unit,
  close: () -> Unit
) {
  if (panel == "table") {
    TableEditorPanel(editor, close)
    return
  }
  if (panel == "ai") {
    AiActionPanel(aiBusy, aiError, onAi)
    return
  }
  if (panel == "graph") {
    GraphFactsPanel(graphCandidates, graphBusy, graphError, graphAddedCount, onGraphAnalyze, onGraphConfirm, onGraphDismiss)
    return
  }
  val rows: List<Pair<String, () -> Unit>> = when (panel) {
    "type" -> listOf(
      "✓  Обычный текст" to { editor.paragraph(); close() },
      "H1  Заголовок H1" to { editor.heading(1); close() },
      "H2  Заголовок H2" to { editor.heading(2); close() },
      "H3  Заголовок H3" to { editor.heading(3); close() },
      "B  Жирный" to { editor.bold(); close() },
      "I  Курсив" to { editor.italic(); close() },
      "S  Зачёркнутый" to { editor.strike(); close() }
    )
    "list" -> listOf(
      "✓  Без списка" to { editor.paragraph(); close() },
      "•  Список с маркерами" to { editor.bullet(); close() },
      "1  Список с номерами" to { editor.numbered(); close() },
      "☑  Чек-лист" to { editor.checklist(); close() },
      "⌄  Сворачиваемый блок" to { editor.collapse(); close() },
      "—  Разделитель" to { editor.divider(); close() }
    )
    "formula" -> listOf(
      "Σ  Формула LaTeX" to { editor.insertLatex(); close() },
      "`  Inline code" to { editor.inlineCode(); close() },
      "{ }  Блок кода" to { editor.codeBlock(); close() }
    )
    "emoji" -> listOf("😀" to { editor.insertEmoji("😀"); close() }, "😂" to { editor.insertEmoji("😂"); close() }, "❤️" to { editor.insertEmoji("❤️"); close() }, "✅" to { editor.insertEmoji("✅"); close() })
    else -> listOf(
      "□  Фото" to onPickImage,
      "▶  Видео" to onPickVideo,
      "PDF  PDF" to onPickPdf,
      "⌁  Файл" to onPickPdf
    )
  }
  Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(LurgPanelStrong).padding(vertical = 8.dp)) {
    if (aiError != null) {
      Text(aiError, color = LurgBlue, fontSize = 15.sp, modifier = Modifier.fillMaxWidth().padding(horizontal = 22.dp, vertical = 10.dp))
    }
    rows.forEach { (label, action) ->
      Text(label, color = LurgText, fontSize = 19.sp, modifier = Modifier.fillMaxWidth().clickable(onClick = action).padding(horizontal = 22.dp, vertical = 13.dp))
    }
  }
}

@Composable
private fun GraphFactsPanel(
  candidates: List<NotesGraphCandidate>?,
  busy: Boolean,
  error: String?,
  addedCount: Int,
  onAnalyze: () -> Unit,
  onConfirm: (List<NotesGraphCandidate>) -> Unit,
  onDismiss: () -> Unit
) {
  var selectedIds by remember(candidates) { mutableStateOf(emptySet<String>()) }
  val selected = candidates.orEmpty().filter { selectedIds.contains(it.id) }
  Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(LurgPanelStrong).padding(18.dp)) {
    Row(verticalAlignment = Alignment.CenterVertically) {
      Column(Modifier.weight(1f)) {
        Text("Найденные факты", color = LurgText, fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text("Выберите, какие данные можно добавить в ваш Lurg Graph", color = LurgMuted, fontSize = 14.sp)
      }
      Text("×", color = LurgMuted, fontSize = 27.sp, modifier = Modifier.clickable(onClick = onDismiss))
    }
    Spacer(Modifier.height(14.dp))
    when {
      busy -> Text("Анализирую заметку…", color = LurgMuted, fontSize = 17.sp, modifier = Modifier.padding(vertical = 16.dp))
      candidates == null && addedCount > 0 -> Text("Добавлено в Graph: $addedCount", color = LurgBlue, fontSize = 17.sp, modifier = Modifier.padding(vertical = 16.dp))
      candidates == null -> {
        Text("Текст заметки будет отправлен в Lurg AI только для поиска потенциальных фактов. Ничего не добавится без отдельного подтверждения.", color = LurgMuted, fontSize = 15.sp)
        Spacer(Modifier.height(14.dp))
        PanelAction("Разрешить анализ", Modifier.fillMaxWidth(), fill = LurgBlue, onClick = onAnalyze)
      }
      candidates.isEmpty() -> Text("В заметке не найдено фактов, которые можно безопасно предложить для Graph.", color = LurgMuted, fontSize = 16.sp, modifier = Modifier.padding(vertical = 16.dp))
      else -> {
        Text("Найдено: ${candidates.size}", color = LurgMuted, fontSize = 14.sp)
        candidates.forEach { candidate ->
          val selectedNow = selectedIds.contains(candidate.id)
          Column(
            Modifier.fillMaxWidth().padding(top = 10.dp).clip(RoundedCornerShape(14.dp)).background(LurgGlass)
              .clickable { selectedIds = if (selectedNow) selectedIds - candidate.id else selectedIds + candidate.id }.padding(13.dp)
          ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
              Text(if (selectedNow) "☑" else "☐", color = if (selectedNow) LurgBlue else LurgMuted, fontSize = 22.sp)
              Spacer(Modifier.width(10.dp))
              Text(candidate.factText, color = LurgText, fontSize = 16.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            }
            Text(candidate.changes.joinToString(" · "), color = LurgBlue, fontSize = 13.sp, modifier = Modifier.padding(start = 32.dp, top = 4.dp))
            Text("${(candidate.confidence * 100).toInt()}% · ${candidate.sourceExcerpt}", color = LurgMuted, fontSize = 12.sp, modifier = Modifier.padding(start = 32.dp, top = 4.dp))
          }
        }
        Spacer(Modifier.height(14.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
          PanelAction("Отмена", Modifier.weight(1f), onClick = onDismiss)
          PanelAction("Добавить (${selected.size})", Modifier.weight(1f), fill = if (selected.isEmpty()) LurgGlass else LurgBlue, onClick = { if (selected.isNotEmpty()) onConfirm(selected) })
        }
      }
    }
    if (error != null) Text(error, color = Color(0xFFFF8CA3), fontSize = 14.sp, modifier = Modifier.padding(top = 10.dp))
  }
}

@Composable
private fun AiActionPanel(
  aiBusy: Boolean,
  aiError: String?,
  onAi: (String, String) -> Unit
) {
  var request by remember { mutableStateOf("") }
  val presets = listOf(
    "Продолжить" to "continue",
    "Исправить" to "fix_grammar",
    "Сократить" to "shorten",
    "Расширить" to "expand",
    "Таблица" to "make_table",
    "Чек-лист" to "make_checklist",
    "Markdown" to "structure_markdown",
    "Summary" to "summarize"
  )
  Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(LurgPanelStrong).padding(14.dp)) {
    Text("AI", color = LurgText, fontSize = 22.sp, fontWeight = FontWeight.Bold)
    Spacer(Modifier.height(10.dp))
    BasicTextField(
      value = request,
      onValueChange = { request = it },
      singleLine = false,
      textStyle = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 18.sp),
      cursorBrush = SolidColor(LurgText),
      modifier = Modifier.fillMaxWidth()
        .requiredHeightIn(min = 48.dp, max = 110.dp)
        .clip(RoundedCornerShape(17.dp))
        .background(LurgGlass)
        .padding(horizontal = 14.dp, vertical = 12.dp),
      decorationBox = { inner ->
        if (request.isBlank()) Text("Что сделать?", color = LurgMuted, fontSize = 18.sp)
        inner()
      }
    )
    if (aiError != null) {
      Spacer(Modifier.height(10.dp))
      Text(aiError, color = LurgBlue, fontSize = 15.sp)
    }
    Row(
      Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(top = 12.dp),
      horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
      presets.forEach { (label, action) ->
        PanelAction(if (aiBusy) "..." else label, Modifier.width(112.dp)) {
          if (!aiBusy) onAi(action, request)
        }
      }
    }
    PanelAction("Выполнить", Modifier.fillMaxWidth().padding(top = 10.dp), fill = LurgBlue) {
      if (!aiBusy) onAi("structure_markdown", request)
    }
  }
}

@Composable
private fun TableEditorPanel(editor: VisualEditorController, close: () -> Unit) {
  var cells by remember { mutableStateOf(List(3) { row -> List(3) { col -> if (row == 0) "Колонка ${col + 1}" else "" } }) }
  Column(
    Modifier.fillMaxWidth()
      .clip(RoundedCornerShape(22.dp))
      .background(LurgPanelStrong)
      .padding(14.dp)
  ) {
    Text("Таблица", color = LurgText, fontSize = 22.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp))
    Column(Modifier.requiredHeightIn(max = 230.dp).horizontalScroll(rememberScrollState())) {
      cells.forEachIndexed { rowIndex, row ->
        Row {
          row.forEachIndexed { colIndex, value ->
            BasicTextField(
              value = value,
              onValueChange = { next ->
                cells = cells.mapIndexed { r, sourceRow ->
                  if (r != rowIndex) sourceRow else sourceRow.mapIndexed { c, source -> if (c == colIndex) next else source }
                }
              },
              textStyle = androidx.compose.ui.text.TextStyle(color = LurgText, fontSize = 16.sp),
              cursorBrush = SolidColor(LurgText),
              modifier = Modifier.width(118.dp).height(46.dp).padding(2.dp).clip(RoundedCornerShape(10.dp)).background(LurgGlass).padding(10.dp)
            )
          }
        }
      }
    }
    Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
      PanelAction("+ строка", Modifier.weight(1f)) { cells = cells + listOf(List(cells.firstOrNull()?.size ?: 2) { "" }) }
      PanelAction("+ колонка", Modifier.weight(1f)) { cells = cells.mapIndexed { index, row -> row + if (index == 0) "Колонка ${row.size + 1}" else "" } }
    }
    Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
      PanelAction("Отмена", Modifier.weight(1f)) { close() }
      PanelAction("Вставить", Modifier.weight(1f), fill = LurgBlue) {
        editor.insertMarkdown(cellsToMarkdown(cells))
        close()
      }
    }
  }
}

@Composable
private fun AiPreviewPanel(preview: AiPreview, onApply: () -> Unit, onCancel: () -> Unit) {
  Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(LurgPanelStrong).padding(16.dp)) {
    Text("AI Preview", color = LurgText, fontSize = 22.sp, fontWeight = FontWeight.Bold)
    MarkdownPreviewCard(preview.content, Modifier.padding(top = 10.dp).requiredHeightIn(max = 280.dp))
    Row(Modifier.fillMaxWidth().padding(top = 14.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
      PanelAction("Отменить", Modifier.weight(1f)) { onCancel() }
      PanelAction("Применить", Modifier.weight(1f), fill = LurgBlue) { onApply() }
    }
  }
}

@Composable
private fun MarkdownPreviewCard(markdown: String, modifier: Modifier = Modifier) {
  MarkdownDocumentPreview(markdown, modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(LurgGlass).verticalScroll(rememberScrollState()).padding(14.dp))
}

@Composable
private fun MarkdownLine(line: String) {
  val trimmed = line.trim()
  when {
    trimmed.isBlank() -> Spacer(Modifier.height(4.dp))
    trimmed.startsWith("# ") -> Text(cleanInline(trimmed.removePrefix("# ")), color = LurgText, fontSize = 26.sp, fontWeight = FontWeight.Bold)
    trimmed.startsWith("## ") -> Text(cleanInline(trimmed.removePrefix("## ")), color = LurgText, fontSize = 22.sp, fontWeight = FontWeight.Bold)
    trimmed.startsWith("### ") -> Text(cleanInline(trimmed.removePrefix("### ")), color = LurgText, fontSize = 19.sp, fontWeight = FontWeight.Bold)
    trimmed.startsWith("> ") -> Row(Modifier.fillMaxWidth()) {
      Box(Modifier.width(3.dp).height(34.dp).clip(RoundedCornerShape(2.dp)).background(LurgBlue))
      Text(cleanInline(trimmed.removePrefix("> ")), color = LurgText, fontSize = 16.sp, modifier = Modifier.padding(start = 10.dp))
    }
    trimmed.startsWith("- [x] ", ignoreCase = true) -> Text("☑ ${cleanInline(trimmed.drop(6))}", color = LurgText, fontSize = 16.sp)
    trimmed.startsWith("[x] ", ignoreCase = true) -> Text("☑ ${cleanInline(trimmed.drop(4))}", color = LurgText, fontSize = 16.sp)
    trimmed.startsWith("- [ ] ") -> Text("☐ ${cleanInline(trimmed.drop(6))}", color = LurgText, fontSize = 16.sp)
    trimmed.startsWith("[ ] ") || trimmed.startsWith("[]") -> Text("☐ ${cleanInline(trimmed.removePrefix("[ ]").removePrefix("[]").trimStart())}", color = LurgText, fontSize = 16.sp)
    trimmed.startsWith("- ") || trimmed.startsWith("* ") -> Text("• ${cleanInline(trimmed.drop(2))}", color = LurgText, fontSize = 16.sp)
    Regex("^\\d+\\.\\s+.+").matches(trimmed) -> Text(cleanInline(trimmed), color = LurgText, fontSize = 16.sp)
    trimmed.startsWith("![") -> AttachmentPreview("Изображение", extractLinkTarget(trimmed))
    trimmed.startsWith("[Видео]") -> AttachmentPreview("Видео", extractLinkTarget(trimmed))
    trimmed.startsWith("[PDF]") -> AttachmentPreview("PDF", extractLinkTarget(trimmed))
    trimmed == "---" || trimmed == "***" -> Box(Modifier.fillMaxWidth().height(1.dp).background(LurgMuted.copy(alpha = 0.35f)))
    else -> Text(cleanInline(trimmed), color = LurgText, fontSize = 16.sp)
  }
}

@Composable
private fun MarkdownTable(tableLines: List<String>) {
  val rows = tableLines
    .filterNot { it.trim().matches(Regex("^\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)+\\|?$")) }
    .map { line ->
      line.trim().trim('|').split("|").map { cleanInline(it.trim()) }
    }
    .filter { it.isNotEmpty() }
  if (rows.isEmpty()) return
  Column(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).clip(RoundedCornerShape(12.dp))) {
    rows.forEachIndexed { rowIndex, row ->
      Row {
        row.forEach { cell ->
          Box(
            Modifier.width(138.dp)
              .background(if (rowIndex == 0) LurgBlue.copy(alpha = 0.22f) else LurgPanel)
              .padding(horizontal = 10.dp, vertical = 9.dp)
          ) {
            Text(cell.ifBlank { " " }, color = LurgText, fontSize = 14.sp, fontWeight = if (rowIndex == 0) FontWeight.Bold else FontWeight.Normal)
          }
        }
      }
    }
  }
}

@Composable
private fun AttachmentPreview(kind: String, uri: String) {
  val isMedia = kind == "Изображение" || kind == "Видео"
  if (isMedia && uri.isNotBlank()) {
    MediaAttachmentPreview(kind, uri)
  } else {
    Row(
      Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(LurgPanel).padding(12.dp),
      verticalAlignment = Alignment.CenterVertically
    ) {
      Text(if (kind == "PDF") "PDF" else "FILE", color = LurgBlue, fontSize = 17.sp, fontWeight = FontWeight.Bold, modifier = Modifier.width(48.dp))
      Column(Modifier.weight(1f)) {
        Text(kind, color = LurgText, fontSize = 15.sp, fontWeight = FontWeight.Bold)
        Text(uri.ifBlank { "Локальное вложение" }, color = LurgMuted, fontSize = 12.sp, maxLines = 1)
      }
    }
  }
}

@Composable
private fun MediaAttachmentPreview(kind: String, uri: String) {
  val context = LocalContext.current
  val request = remember(uri, kind) {
    ImageRequest.Builder(context)
      .data(uri)
      .crossfade(true)
      .apply {
        if (kind == "Видео") {
          decoderFactory(VideoFrameDecoder.Factory())
        }
      }
      .build()
  }
  LaunchedEffect(uri, kind) {
    context.imageLoader.enqueue(request)
  }
  Box(
    Modifier.fillMaxWidth()
      .height(210.dp)
      .clip(RoundedCornerShape(16.dp))
      .background(LurgPanel)
  ) {
    AsyncImage(
      model = request,
      contentDescription = kind,
      contentScale = ContentScale.Crop,
      modifier = Modifier.fillMaxSize()
    )
    Box(
      Modifier.align(Alignment.BottomStart)
        .fillMaxWidth()
        .background(Color(0x99050509))
        .padding(horizontal = 13.dp, vertical = 10.dp)
    ) {
      Row(verticalAlignment = Alignment.CenterVertically) {
        Text(if (kind == "Видео") "▶" else "IMG", color = LurgText, fontSize = 14.sp, fontWeight = FontWeight.Bold, modifier = Modifier.width(42.dp))
        Column(Modifier.weight(1f)) {
          Text(kind, color = LurgText, fontSize = 15.sp, fontWeight = FontWeight.Bold)
          Text(uri, color = LurgMuted, fontSize = 11.sp, maxLines = 1)
        }
      }
    }
  }
}

private fun isTableStart(lines: List<String>, index: Int): Boolean {
  if (index + 1 >= lines.size) return false
  return lines[index].trim().startsWith("|") && lines[index + 1].contains("---")
}

private fun extractLinkTarget(line: String): String {
  return Regex("\\((.+?)\\)").find(line)?.groups?.get(1)?.value.orEmpty()
}

private data class InlineVisualStyle(
  val text: String,
  val weight: FontWeight,
  val fontStyle: FontStyle,
  val textDecoration: TextDecoration
)

private data class InlineMarks(
  val text: String,
  val bold: Boolean = false,
  val italic: Boolean = false,
  val strike: Boolean = false,
  val code: Boolean = false
)

private fun inlineStyleFor(value: String, fallbackWeight: FontWeight): InlineVisualStyle {
  val marks = parseInlineMarks(value)
  return InlineVisualStyle(
    text = marks.text,
    weight = if (marks.bold) FontWeight.Bold else fallbackWeight,
    fontStyle = if (marks.italic) FontStyle.Italic else FontStyle.Normal,
    textDecoration = if (marks.strike) TextDecoration.LineThrough else TextDecoration.None
  )
}

private fun applyInlineStyle(original: String, nextPlain: String): String {
  val marks = parseInlineMarks(original)
  return renderInlineMarks(marks.copy(text = nextPlain))
}

private fun toggleInlineMark(original: String, mark: String): String {
  val marks = parseInlineMarks(original)
  val next = when (mark) {
    "bold" -> marks.copy(bold = !marks.bold, code = false)
    "italic" -> marks.copy(italic = !marks.italic, code = false)
    "strike" -> marks.copy(strike = !marks.strike)
    "code" -> marks.copy(code = !marks.code, bold = false, italic = false, strike = false)
    else -> marks
  }
  return renderInlineMarks(next)
}

private fun parseInlineMarks(value: String): InlineMarks {
  // Do not trim editor input: a trailing space is a valid in-progress edit.
  // Formatting markers belong to the text core, while whitespace must survive round trips.
  val leadingWhitespace = value.takeWhile { it.isWhitespace() }
  val trailingWhitespace = value.drop(leadingWhitespace.length).takeLastWhile { it.isWhitespace() }
  var text = value.trim()
  var bold = false
  var italic = false
  var strike = false
  var code = false
  var changed: Boolean
  do {
    changed = false
    when {
      isWrapped(text, "~~", "~~") -> {
        strike = true
        text = text.removeSurrounding("~~")
        changed = true
      }
      isWrapped(text, "`", "`") -> {
        code = true
        text = text.removeSurrounding("`")
        changed = true
      }
      isWrapped(text, "***", "***") -> {
        bold = true
        italic = true
        text = text.removeSurrounding("***")
        changed = true
      }
      isWrapped(text, "___", "___") -> {
        bold = true
        italic = true
        text = text.removeSurrounding("___")
        changed = true
      }
      isWrapped(text, "**", "**") -> {
        bold = true
        text = text.removeSurrounding("**")
        changed = true
      }
      isWrapped(text, "__", "__") -> {
        bold = true
        text = text.removeSurrounding("__")
        changed = true
      }
      isWrapped(text, "*", "*") -> {
        italic = true
        text = text.removeSurrounding("*")
        changed = true
      }
      isWrapped(text, "_", "_") -> {
        italic = true
        text = text.removeSurrounding("_")
        changed = true
      }
    }
  } while (changed)
  return InlineMarks(text = leadingWhitespace + cleanInline(text) + trailingWhitespace, bold = bold, italic = italic, strike = strike, code = code)
}

private fun renderInlineMarks(marks: InlineMarks): String {
  var result = marks.text
  if (marks.code) return "`$result`"
  result = when {
    marks.bold && marks.italic -> "***$result***"
    marks.bold -> "**$result**"
    marks.italic -> "*$result*"
    else -> result
  }
  if (marks.strike) result = "~~$result~~"
  return result
}

private fun isWrapped(value: String, prefix: String, suffix: String): Boolean {
  return value.length >= prefix.length + suffix.length &&
    value.startsWith(prefix) &&
    value.endsWith(suffix)
}

private fun cleanInline(value: String): String {
  return value
    .replace(Regex("!\\[(.*?)]\\((.*?)\\)"), "$1")
    .replace(Regex("\\[(.*?)]\\((.*?)\\)"), "$1")
    .replace(Regex("^\\[[ xX]\\]\\s*"), "")
    .replace("**", "")
    .replace("__", "")
    .replace("~~", "")
    .replace("`", "")
    .replace(Regex("(?<!\\*)\\*(?!\\*)"), "")
    .replace(Regex("<[^>]+>"), "")
}

@Composable
private fun PanelAction(label: String, modifier: Modifier = Modifier, fill: Color = LurgGlass, onClick: () -> Unit) {
  Box(modifier.height(44.dp).lurgPressable(onClick).clip(RoundedCornerShape(16.dp)).background(fill), contentAlignment = Alignment.Center) {
    Text(label, color = LurgText, fontSize = 15.sp, fontWeight = FontWeight.Bold)
  }
}

private fun cellsToMarkdown(cells: List<List<String>>): String {
  val normalized = cells.filter { row -> row.any { it.isNotBlank() } }.ifEmpty { listOf(listOf("Колонка 1", "Колонка 2"), listOf("", "")) }
  val columns = normalized.maxOf { it.size }.coerceAtLeast(2)
  val padded = normalized.map { row -> row + List(columns - row.size) { "" } }
  val header = padded.first().mapIndexed { index, value -> value.ifBlank { "Колонка ${index + 1}" } }
  val body = padded.drop(1).ifEmpty { listOf(List(columns) { "" }) }
  return buildString {
    append("\n|")
    append(header.joinToString("|") { " ${it.replace("|", "\\|")} " })
    append("|\n|")
    append(List(columns) { " --- " }.joinToString("|"))
    append("|\n")
    body.forEach { row ->
      append("|")
      append(row.joinToString("|") { " ${it.replace("|", "\\|")} " })
      append("|\n")
    }
  }
}

@Composable
fun IslandButton(label: String, modifier: Modifier = Modifier, fill: Color = LurgGlass, onClick: () -> Unit) {
  Box(modifier.lurgPressable(onClick).clip(RoundedCornerShape(24.dp)).background(fill), contentAlignment = Alignment.Center) {
    Text(label, color = LurgText, fontSize = if (label.length > 2) 15.sp else 25.sp, fontWeight = FontWeight.Bold)
  }
}

@Composable
private fun MiniButton(label: String, onClick: () -> Unit) {
  Text(label, color = LurgText, fontSize = 25.sp, fontWeight = FontWeight.Bold, modifier = Modifier.clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 10.dp))
}

@Composable
private fun ToolbarIcon(label: String, active: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit) {
  Box(
    modifier.height(42.dp).graphicsLayer { alpha = if (active) 1f else 0.92f }
      .clip(RoundedCornerShape(23.dp)).background(if (active) LurgBlue.copy(alpha = 0.25f) else Color.Transparent)
      .clickable(onClick = onClick),
    contentAlignment = Alignment.Center
  ) {
    Text(label, color = if (active) LurgBlue else LurgText, fontSize = 23.sp, fontWeight = FontWeight.Bold)
  }
}

/** One restrained motion vocabulary for every interactive Notes surface. */
private object LurgMotion {
  const val FastMillis = 130
  private const val StandardMillis = 230
  private val FastEasing = CubicBezierEasing(.2f, 0f, 0f, 1f)
  private val StandardEasing = CubicBezierEasing(.18f, .72f, .18f, 1f)

  fun <T> press(enabled: Boolean): FiniteAnimationSpec<T> =
    if (enabled) tween(FastMillis, easing = FastEasing) else tween(0)
  fun <T> fast(enabled: Boolean): FiniteAnimationSpec<T> =
    if (enabled) tween(FastMillis, easing = FastEasing) else tween(0)
  fun <T> standard(enabled: Boolean): FiniteAnimationSpec<T> =
    if (enabled) tween(StandardMillis, easing = StandardEasing) else tween(0)
  fun <T> surfaceSpring(enabled: Boolean): FiniteAnimationSpec<T> =
    if (enabled) spring(dampingRatio = .88f, stiffness = 640f) else tween(0)
  fun <T> listSpring(enabled: Boolean): FiniteAnimationSpec<T> =
    if (enabled) spring(dampingRatio = .92f, stiffness = 760f) else tween(0)
  fun <T> checkSpring(enabled: Boolean): FiniteAnimationSpec<T> =
    if (enabled) spring(dampingRatio = .72f, stiffness = 880f) else tween(0)
  fun <T> backSpring(enabled: Boolean): FiniteAnimationSpec<T> =
    if (enabled) spring(dampingRatio = .9f, stiffness = 520f) else tween(0)
}

@Composable
private fun rememberLurgMotionEnabled(): Boolean = LocalMotionEnabled.current

/** Immediate visual acknowledgement with no fullscreen Material ripple. */
private fun Modifier.lurgPressable(onClick: () -> Unit, enabled: Boolean = true): Modifier = composed {
  val motionEnabled = rememberLurgMotionEnabled()
  val interaction = remember { MutableInteractionSource() }
  val pressed by interaction.collectIsPressedAsState()
  val scale by animateFloatAsState(
    targetValue = if (pressed && enabled) .96f else 1f,
    animationSpec = LurgMotion.press(motionEnabled), label = "lurg-press"
  )
  graphicsLayer { scaleX = scale; scaleY = scale }
    .clickable(interactionSource = interaction, indication = null, enabled = enabled, onClick = onClick)
}

val LurgVoid = Color(0xFF090A0F)
val LurgPanel = Color(0xFF15161D)
private val LurgPanelStrong = Color(0xF21B1C24)
private val LurgGlass = Color(0xDD1A1B23)
private val LurgCard = Color(0xFF18191F)
private val LurgField = Color(0xFF202128)
val LurgText = Color(0xFFF5F5F7)
val LurgMuted = Color(0xFF9898A1)
private val LurgAccent = Color(0xFFFFD60A)
private val LurgBlue = Color(0xFF2F9DEF)
