package pro.lurg.notes.editor

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.ClickableText
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val Ink = Color(0xFFF5F6F8)
private val Link = Color(0xFF67B9FA)
private val Muted = Color(0xFF9099A7)

fun InlineDocument.annotated(): AnnotatedString = AnnotatedString.Builder(text).apply {
  runs.forEach { run ->
    val style = when (run.kind) {
      "bold" -> SpanStyle(fontWeight = FontWeight.Bold)
      "italic" -> SpanStyle(fontStyle = FontStyle.Italic)
      "boldItalic" -> SpanStyle(fontWeight = FontWeight.Bold, fontStyle = FontStyle.Italic)
      "strike" -> SpanStyle(textDecoration = TextDecoration.LineThrough)
      "code" -> SpanStyle(fontFamily = FontFamily.Monospace, background = Color(0xFF26313D), color = Color(0xFFD5E7F6))
      "link" -> SpanStyle(color = Link, textDecoration = TextDecoration.Underline)
      else -> SpanStyle()
    }
    addStyle(style, run.start, run.end)
    if (run.kind == "link") addStringAnnotation("URL", run.target.orEmpty(), run.start, run.end)
  }
}.toAnnotatedString()

class InlineMarkdownTransformation : VisualTransformation {
  override fun filter(text: AnnotatedString): TransformedText {
    val parsed = InlineMarkdown.parse(text.text)
    return TransformedText(parsed.annotated(), object : OffsetMapping {
      override fun originalToTransformed(offset: Int) = parsed.originalToVisible(offset)
      override fun transformedToOriginal(offset: Int) = parsed.visibleToOriginal(offset)
    })
  }
}

@Composable
fun InlineMarkdownText(source: String, modifier: Modifier = Modifier, style: TextStyle = TextStyle(color = Ink, fontSize = 17.sp)) {
  val parsed = remember(source) { InlineMarkdown.parse(source).annotated() }
  val uri = LocalUriHandler.current
  ClickableText(parsed, modifier = modifier, style = style, onClick = { offset ->
    parsed.getStringAnnotations("URL", offset, offset).firstOrNull()?.let { runCatching { uri.openUri(it.item) } }
  })
}

/** Read-only preview also used for AI results; code is literal and never fetches remote images. */
@Composable
fun MarkdownDocumentPreview(source: String, modifier: Modifier = Modifier) {
  val blocks = remember(source) { previewBlocks(source) }
  Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
    blocks.forEach { block ->
      when (block.kind) {
        "space" -> Spacer(Modifier.height(4.dp))
        "code" -> Text(block.text, color = Ink, fontSize = 14.sp, fontFamily = FontFamily.Monospace,
          modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Color(0xFF202A34)).horizontalScroll(rememberScrollState()).padding(14.dp))
        "divider" -> Box(Modifier.fillMaxWidth().height(1.dp).background(Muted.copy(alpha = .35f)))
        "quote" -> Row { Box(Modifier.width(3.dp).height(32.dp).background(Link)); InlineMarkdownText(block.text, Modifier.padding(start = 12.dp), TextStyle(color = Muted, fontSize = 17.sp, lineHeight = 25.sp)) }
        "h1", "h2", "h3" -> InlineMarkdownText(block.text, style = TextStyle(color = Ink, fontSize = when (block.kind) { "h1" -> 28.sp; "h2" -> 23.sp; else -> 20.sp }, fontWeight = FontWeight.Bold))
        "table" -> {
          val rows = MarkdownTables.rows(block.text)
          Column(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) { rows.forEachIndexed { index, row ->
            Row(Modifier.background(if (index == 0) Color(0xFF202A34) else Color.Transparent).padding(vertical = 8.dp)) {
              row.forEach { InlineMarkdownText(it, Modifier.width(155.dp).padding(horizontal = 10.dp), TextStyle(color = Ink, fontSize = 15.sp, fontWeight = if (index == 0) FontWeight.SemiBold else FontWeight.Normal)) }
            }
          } }
        }
        else -> InlineMarkdownText(block.text, style = TextStyle(color = Ink, fontSize = 17.sp, lineHeight = 25.sp))
      }
    }
  }
}

data class PreviewBlock(val kind: String, val text: String)
fun previewBlocks(source: String): List<PreviewBlock> {
  val lines = source.lines()
  val blocks = mutableListOf<PreviewBlock>()
  var index = 0
  while (index < lines.size) {
    val line = lines[index].trim()
    when {
      line.startsWith("```") || line.startsWith("~~~") -> {
        val fence = line.take(3)
        val code = mutableListOf<String>(); index++
        while (index < lines.size && !lines[index].trim().startsWith(fence)) code += lines[index++]
        blocks += PreviewBlock("code", code.joinToString("\n"))
      }
      line.startsWith("|") && index + 1 < lines.size && lines[index + 1].contains("---") -> {
        val rows = mutableListOf<String>()
        while (index < lines.size && lines[index].trim().startsWith("|")) rows += lines[index++]
        blocks += PreviewBlock("table", rows.joinToString("\n")); index--
      }
      line.isBlank() -> blocks += PreviewBlock("space", "")
      line == "---" || line == "***" -> blocks += PreviewBlock("divider", "")
      line.startsWith("### ") -> blocks += PreviewBlock("h3", line.drop(4))
      line.startsWith("## ") -> blocks += PreviewBlock("h2", line.drop(3))
      line.startsWith("# ") -> blocks += PreviewBlock("h1", line.drop(2))
      line.startsWith("> ") -> blocks += PreviewBlock("quote", line.drop(2))
      line.startsWith("- [x] ", true) -> blocks += PreviewBlock("text", "☑ ${line.drop(6)}")
      line.startsWith("- [ ] ") -> blocks += PreviewBlock("text", "☐ ${line.drop(6)}")
      line.startsWith("- ") || line.startsWith("* ") -> blocks += PreviewBlock("text", "• ${line.drop(2)}")
      else -> blocks += PreviewBlock("text", lines[index])
    }
    index++
  }
  return blocks
}
