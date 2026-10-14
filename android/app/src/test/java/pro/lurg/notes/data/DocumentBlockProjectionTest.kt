package pro.lurg.notes.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DocumentBlockProjectionTest {
  @Test fun `block projection preserves document order and image position`() {
    val markdown = NotesRepository.blocksToMarkdown(listOf(
      DocumentBlock("a", "1000.0000000000", "paragraph", "LEGO 10307 — Eiffel Tower"),
      DocumentBlock("b", "1500.0000000000", "image", uri = "content://local/eiffel.jpg", width = 1600, height = 900),
      DocumentBlock("c", "2000.0000000000", "checklist", "Проверить цену", checked = true),
      DocumentBlock("d", "3000.0000000000", "code", "set = 10307")
    ))
    assertTrue(markdown.indexOf("Eiffel") < markdown.indexOf("eiffel.jpg"))
    assertTrue(markdown.indexOf("eiffel.jpg") < markdown.indexOf("Проверить"))
    assertTrue(markdown.contains("- [x] Проверить цену"))
    assertTrue(markdown.contains("```\nset = 10307\n```"))
  }

  @Test fun `empty document always has a durable paragraph`() {
    assertEquals("", NotesRepository.blocksToMarkdown(listOf(DocumentBlock("a", "1000", "paragraph"))))
  }

  @Test fun `projection covers every supported document markdown block`() {
    val markdown = NotesRepository.blocksToMarkdown(listOf(
      DocumentBlock("h1", "1000", "heading1", "Title"),
      DocumentBlock("h2", "2000", "heading2", "Section"),
      DocumentBlock("h3", "3000", "heading3", "Detail"),
      DocumentBlock("b", "4000", "bullet", "Bullet"),
      DocumentBlock("n", "5000", "numbered", "First"),
      DocumentBlock("todo", "6000", "checklist", "Done", checked = false),
      DocumentBlock("done", "7000", "checklist", "Done", checked = true),
      DocumentBlock("quote", "8000", "quote", "One\nTwo"),
      DocumentBlock("code", "9000", "code", "val x = 1"),
      DocumentBlock("rule", "10000", "divider"),
      DocumentBlock("image", "11000", "image", uri = "content://image"),
      DocumentBlock("file", "12000", "file", uri = "content://file.pdf")
    ))
    assertTrue(markdown.contains("# Title\n## Section\n### Detail"))
    assertTrue(markdown.contains("- Bullet\n1. First\n- [ ] Done\n- [x] Done"))
    assertTrue(markdown.contains("> One\n> Two\n```\nval x = 1\n```\n---"))
    assertTrue(markdown.contains("![Изображение](content://image)\n[PDF](content://file.pdf)"))
  }
}
