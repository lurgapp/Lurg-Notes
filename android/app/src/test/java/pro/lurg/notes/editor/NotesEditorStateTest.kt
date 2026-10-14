package pro.lurg.notes.editor

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import org.junit.Assert.*
import org.junit.Test
import pro.lurg.notes.BlockDocumentController
import pro.lurg.notes.NotesSettingsRoute
import pro.lurg.notes.data.DocumentBlock
import pro.lurg.notes.data.NotesRepository

class NotesEditorStateTest {
  private fun block(id: String = "stable", text: String = "One two three", type: String = "paragraph") = DocumentBlock(id, "1000", type, text)

  @Test fun `inline actions touch selected text only and preserve stable identity with undo redo`() {
    val saved = mutableListOf<List<DocumentBlock>>()
    val controller = BlockDocumentController(listOf(block())) { saved += it }
    controller.select("stable", TextFieldValue("One two three", TextRange(4, 7)))
    controller.formatInline("bold")
    assertEquals("One **two** three", controller.blocks.single().text)
    assertEquals("stable", controller.blocks.single().id)
    assertEquals(TextRange(6, 9), controller.selection)
    controller.formatInline("bold")
    assertEquals("One two three", controller.blocks.single().text)
    controller.undo(); assertEquals("One **two** three", controller.blocks.single().text)
    controller.redo(); assertEquals("One two three", controller.blocks.single().text)
    assertEquals(4, saved.size)
  }

  @Test fun `typing a formatted paragraph never strips unrelated partial markdown`() {
    val controller = BlockDocumentController(listOf(block(text = "One **two** three"))) {}
    controller.edit("stable", "One **two** three!")
    assertEquals("One **two** three!", controller.blocks.single().text)
    controller.undo(); assertEquals("One **two** three", controller.blocks.single().text)
  }

  @Test fun `code lines remain a single stable block and empty list enter returns to paragraph`() {
    val code = BlockDocumentController(listOf(block(type = "code"))) {}
    code.edit("stable", "line1\nline2")
    assertEquals(1, code.blocks.size)
    assertEquals("line1\nline2", code.blocks.single().text)
    val list = BlockDocumentController(listOf(block(text = "", type = "checklist"))) {}
    list.edit("stable", "\n")
    assertEquals("paragraph", list.blocks.single().type)
    assertEquals("stable", list.blocks.single().id)
  }

  @Test fun `numbered runs increment and restart after another block in UI and projection`() {
    val blocks = listOf(block("a", "A", "numbered"), block("b", "B", "numbered"), block("c", "Text"), block("d", "C", "numbered"))
    val controller = BlockDocumentController(blocks) {}
    assertEquals(2, controller.numberedIndex("b")); assertEquals(1, controller.numberedIndex("d"))
    assertEquals("1. A\n2. B\nText\n1. C", NotesRepository.blocksToMarkdown(blocks))
  }

  @Test fun `every settings subpage returns home and home closes without a phantom destination`() {
    NotesSettingsRoute.entries.filter { it != NotesSettingsRoute.Home }.forEach { assertEquals(NotesSettingsRoute.Home, it.parent()) }
    assertNull(NotesSettingsRoute.Home.parent())
  }
}
