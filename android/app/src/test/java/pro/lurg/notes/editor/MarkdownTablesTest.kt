package pro.lurg.notes.editor

import org.junit.Assert.*
import org.junit.Test
import pro.lurg.notes.BlockDocumentController
import pro.lurg.notes.data.DocumentBlock

class MarkdownTablesTest {
  @Test fun `escaped separators and formatting survive table cell editing`() {
    val source = "| Name | Value |\n| --- | --- |\n| A\\|B | **bold** |"
    assertEquals(listOf("A|B", "**bold**"), MarkdownTables.rows(source).last())
    val controller = BlockDocumentController(listOf(DocumentBlock("stable-table", "1000", "table", source))) {}
    controller.updateTableCell("stable-table", 1, 1, "new|value")
    assertEquals("stable-table", controller.blocks.single().id)
    assertEquals(listOf(listOf("Name", "Value"), listOf("A|B", "new|value")), MarkdownTables.rows(controller.blocks.single().text))
    controller.undo()
    assertEquals(source, controller.blocks.single().text)
  }

  @Test fun `separator detection retains literal dashes and escaped inline markup`() {
    val rows = listOf(listOf("H1", "H2"), listOf("---", "value"), listOf("\\*literal\\*", "path\\folder"), listOf("literal\\|separator", "backslash\\\\"))
    assertEquals(rows, MarkdownTables.rows(MarkdownTables.serialize(rows)))
    assertEquals(listOf("A", "B"), MarkdownTables.cells("A | B"))
    assertEquals(listOf("", ""), MarkdownTables.cells("|  |  |"))
    assertEquals(listOf("A|", "B"), MarkdownTables.cells("| A\\| | B |"))
  }
}
