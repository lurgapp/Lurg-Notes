package pro.lurg.notes

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.Espresso
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import pro.lurg.notes.api.AuthClient
import pro.lurg.notes.data.*
import java.io.File

/** Runs only in the separate debug package against a disposable loopback API. */
@RunWith(AndroidJUnit4::class)
class NativeNotesEditorUiTest {
  @get:Rule val compose = createEmptyComposeRule()
  private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
  private var scenario: ActivityScenario<MainActivity>? = null
  private lateinit var repo: NotesRepository
  private lateinit var note: NoteEntity
  private lateinit var owner: String

  @Before fun setup(): Unit = runBlocking {
    check(BuildConfig.DEBUG && context.packageName == "pro.lurg.notes.uitest")
    check(BuildConfig.API_BASE_URL.startsWith("http://127.0.0.1:"))
    val credentialsFile = File(context.filesDir, "audit-credentials.json")
    check(credentialsFile.exists()) { "Disposable fixture is required" }
    val credentials = JSONObject(credentialsFile.readText())
    owner = AuthClient(context).login(credentials.getString("email"), credentials.getString("password")).id
    context.getSharedPreferences("lurg_notes_flags", 0).edit().putBoolean("onboarding_completed", true).putBoolean("private_screen", false).commit()
    val app = context.applicationContext as LurgNotesApplication
    repo = NotesRepository(context, app.database.notesDao(), app.database)
    note = repo.createNote(owner)
    repo.saveDocument(note.id, owner, listOf(
      DocumentBlock("ui-title", "1000", "heading1", "UI editor fixture"),
      DocumentBlock("ui-text", "2000", "paragraph", "One **bold** and _italic_ text"),
      DocumentBlock("ui-number1", "3000", "numbered", "First"),
      DocumentBlock("ui-number2", "4000", "numbered", "Second"),
      DocumentBlock("ui-code", "5000", "code", "val literal = **source**\nsecond line")
    ))
    scenario = ActivityScenario.launch(MainActivity::class.java)
    waitText("UI editor fixture")
  }
  @After fun cleanup() { scenario?.close(); if (::repo.isInitialized && ::note.isInitialized && ::owner.isInitialized) runBlocking { repo.deleteNote(note.id, owner) } }
  private fun waitText(text: String) { compose.waitUntil(15000) { runCatching { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }.getOrDefault(false) } }
  private fun screenshot(name: String) {
    compose.waitForIdle()
    android.os.SystemClock.sleep(250) // allow WindowManager secure/IME flags to reach the rendered frame
    val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
    checkNotNull(bitmap)
    File(context.getExternalFilesDir(null), "$name.png").outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
    bitmap.recycle()
  }

  @Test fun settingsEveryRouteHasVisibleAndSystemBackAndPrivateSwitchPersists() {
    compose.onNodeWithText("Настройки").performClick()
    screenshot("notes-settings-home-20261010")
    val routes = listOf("Аккаунт", "Приватность", "Устройства и recovery", "Синхронизация", "Редактор", "Обновления", "О приложении")
    routes.forEachIndexed { index, title ->
      compose.onNodeWithText(title).performScrollTo().performClick()
      compose.onNodeWithContentDescription("Назад").assertExists()
      if (title == "Приватность") {
        compose.onNodeWithContentDescription("Приватный экран").performClick()
        assertTrue(context.getSharedPreferences("lurg_notes_flags", 0).getBoolean("private_screen", false))
        compose.onNodeWithContentDescription("Приватный экран").performClick()
        screenshot("notes-settings-privacy-20261010")
      }
      if (index % 2 == 0) compose.onNodeWithContentDescription("Назад").performClick() else Espresso.pressBack()
      compose.onNodeWithText("Аккаунт").assertExists()
    }
    compose.onNodeWithContentDescription("Назад").performClick()
    compose.onNodeWithText("UI editor fixture").assertExists()
  }

  @Test fun editorInlineMappingMenusUndoAutosaveAndActivityRestart(): Unit = runBlocking {
    compose.onNodeWithText("UI editor fixture").performClick()
    compose.waitUntil(10000) { compose.onAllNodesWithContentDescription("Текст заметки").fetchSemanticsNodes().isNotEmpty() }
    val text = compose.onAllNodesWithContentDescription("Текст заметки")[0]
    text.performTextReplacement("One **bold** and _italic_ text changed")
    screenshot("notes-editor-live-20261010")
    compose.onNodeWithContentDescription("Форматирование").performClick()
    compose.onNodeWithContentDescription("Назад из форматирования").assertExists()
    Espresso.pressBack() // keyboard may consume the first press on some Android builds.
    compose.waitForIdle()
    if (compose.onAllNodesWithContentDescription("Назад из форматирования").fetchSemanticsNodes().isNotEmpty()) compose.onNodeWithContentDescription("Назад из форматирования").performClick()
    compose.onNodeWithContentDescription("Списки и блоки").performClick()
    compose.onNodeWithText("С номерами").performClick()
    compose.onNodeWithContentDescription("Отменить").performClick()
    compose.onNodeWithContentDescription("Читать").performClick()
    compose.onNodeWithText("One bold and italic text changed").assertExists()
    compose.onNodeWithText("2. Second").assertExists()
    screenshot("notes-editor-reading-20261010")
    compose.onNodeWithContentDescription("Редактировать").performClick()
    compose.onNodeWithContentDescription("Готово").performClick()
    waitText("UI editor fixture")
    scenario!!.recreate()
    waitText("UI editor fixture")
    val dao = (context.applicationContext as LurgNotesApplication).database.notesDao()
    withTimeout(5000) { while (JSONObject(dao.blocks(note.id)[1].contentJson).getString("text") != "One **bold** and _italic_ text changed") delay(50) }
    val persisted = dao.blocks(note.id)
    assertEquals(listOf("ui-title", "ui-text", "ui-number1", "ui-number2", "ui-code"), persisted.map { it.id })
    assertEquals("One **bold** and _italic_ text changed", JSONObject(persisted[1].contentJson).getString("text"))
    assertEquals("val literal = **source**\nsecond line", JSONObject(persisted.last().contentJson).getString("text"))
  }
}
