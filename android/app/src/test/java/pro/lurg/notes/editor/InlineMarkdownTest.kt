package pro.lurg.notes.editor

import org.junit.Assert.*
import org.junit.Test

class InlineMarkdownTest {
  @Test fun `partial and nested styles preserve plain text and emoji cursor offsets`() {
    val source = "Hello **bold _inside_** and `*literal*` 👋"
    val parsed = InlineMarkdown.parse(source)
    assertEquals("Hello bold inside and *literal* 👋", parsed.text)
    assertEquals(setOf("bold", "italic", "code"), parsed.runs.map { it.kind }.toSet())
    for (offset in 0..source.length) assertTrue(parsed.originalToVisible(offset) in 0..parsed.text.length)
    for (offset in 0..parsed.text.length) {
      val raw = parsed.visibleToOriginal(offset)
      assertTrue(raw in 0..source.length)
      assertEquals(offset, parsed.originalToVisible(raw))
    }
  }

  @Test fun `raw mappings keep insertion inside format and unsafe links visible`() {
    val parsed = InlineMarkdown.parse("**word**")
    assertEquals(2, parsed.visibleToOriginal(0))
    assertEquals(6, parsed.visibleToOriginal(4))
    assertEquals("[bad](javascript:alert(1))", InlineMarkdown.parse("[bad](javascript:alert(1))").text)
    val safe = InlineMarkdown.parse("[wiki](https://example.com/a_(b))")
    assertEquals("wiki", safe.text)
    assertEquals("https://example.com/a_(b)", safe.runs.single().target)
  }

  @Test fun `unfinished escaped and ordinary punctuation survives editing`() {
    assertEquals("**typing", InlineMarkdown.parse("**typing").text)
    assertEquals("word_name *literal*", InlineMarkdown.parse("word_name \\*literal\\*").text)
    assertEquals("bold italic", InlineMarkdown.parse("***bold italic***").text)
    assertEquals("boldItalic", InlineMarkdown.parse("***bold italic***").runs.single().kind)
    assertEquals("_name_", InlineMarkdown.parse("`_name_`").text)
  }

  @Test fun `fenced previews keep literal markdown and unfinished streamed code`() {
    val blocks = previewBlocks("# Title\n```kotlin\n**literal**\n`still code`")
    assertEquals(PreviewBlock("h1", "Title"), blocks.first())
    assertEquals(PreviewBlock("code", "**literal**\n`still code`"), blocks.last())
  }
}
