package pro.lurg.notes.editor

import org.junit.Assert.assertEquals
import org.junit.Test

class MarkdownTransformsTest {
  @Test fun `bold italic strike code and link preserve the selected range`() {
    assertEquals("**text**", MarkdownTransforms.wrap("text", 0, 4, "**").text)
    assertEquals("*text*", MarkdownTransforms.wrap("text", 0, 4, "*").text)
    assertEquals("~~text~~", MarkdownTransforms.wrap("text", 0, 4, "~~").text)
    assertEquals("`text`", MarkdownTransforms.wrap("text", 0, 4, "`").text)
    assertEquals("[text](https://)", MarkdownTransforms.wrap("text", 0, 4, "[", "](https://)").text)
  }

  @Test fun `reapplying an inline mark removes it and leaves selection valid`() {
    val result = MarkdownTransforms.wrap("**text**", 2, 6, "**")
    assertEquals("text", result.text)
    assertEquals(0, result.selectionStart)
    assertEquals(4, result.selectionEnd)
  }

  @Test fun `block transforms replace old prefixes across selected lines`() {
    assertEquals("## One\n## Two", MarkdownTransforms.heading("- One\n- Two", 0, 11, 2).text)
    assertEquals("> One\n> Two", MarkdownTransforms.linePrefix("One\nTwo", 0, 7, "> ").text)
    assertEquals("1. One\n2. Two", MarkdownTransforms.numbered("One\nTwo", 0, 7).text)
    assertEquals("- [ ] One\n- [ ] Two", MarkdownTransforms.linePrefix("One\nTwo", 0, 7, "- [ ] ").text)
  }

  @Test fun `code blocks and tables use valid bounded markdown shapes`() {
    assertEquals("```\ncode\n```", MarkdownTransforms.codeBlock("code", 0, 4).text)
    assertEquals("```\n\n```", MarkdownTransforms.codeBlock("", 0, 0).text)
    val table = MarkdownTransforms.table(columns = 99, rows = 99)
    assertEquals(6, table.lineSequence().first { it.startsWith("|") }.trim('|').split('|').size)
    assertEquals(10, table.lineSequence().count { it.startsWith("|") })
  }
}
