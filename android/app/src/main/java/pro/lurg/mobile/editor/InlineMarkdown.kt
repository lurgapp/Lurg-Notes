package pro.lurg.notes.editor

/** UTF-16 offsets match Android/Compose selections, including emoji. No source text is rewritten. */
data class InlineRun(val start: Int, val end: Int, val kind: String, val target: String? = null)
data class InlineDocument(
  val text: String,
  val runs: List<InlineRun>,
  private val rawToVisible: IntArray,
  private val visibleToRaw: IntArray
) {
  fun originalToVisible(offset: Int) = rawToVisible[offset.coerceIn(0, rawToVisible.lastIndex)]
  fun visibleToOriginal(offset: Int) = visibleToRaw[offset.coerceIn(0, visibleToRaw.lastIndex)]
}

/** Small inline grammar: nested emphasis, strike, code, safe links and backslash escapes.
 * Unclosed syntax stays visible while typing. Work is bounded by a depth limit.
 */
object InlineMarkdown {
  fun parse(raw: String): InlineDocument {
    // Imported oversized lines stay editable without a quadratic parser pass.
    // Their full source remains intact and is never silently truncated.
    if (raw.length > 100_000) return InlineDocument(raw, emptyList(), IntArray(raw.length + 1) { it }, IntArray(raw.length + 1) { it })
    val hidden = BooleanArray(raw.length)
    val sourceRuns = mutableListOf<InlineRun>()
    fun hide(start: Int, end: Int) { for (index in start until end) hidden[index] = true }
    fun escaped(index: Int): Boolean {
      var slash = index - 1
      while (slash >= 0 && raw[slash] == '\\') slash--
      return (index - slash - 1) % 2 == 1
    }
    fun closing(mark: String, start: Int, end: Int): Int {
      var at = raw.indexOf(mark, start)
      while (at >= 0 && at + mark.length <= end) {
        if (!escaped(at)) return at
        at = raw.indexOf(mark, at + 1)
      }
      return -1
    }
    fun scan(start: Int, end: Int, depth: Int) {
      if (depth > 12) return
      var index = start
      while (index < end) {
        if (raw[index] == '\\' && index + 1 < end && raw[index + 1] in "\\`*_~[]()") {
          hide(index, index + 1); index += 2; continue
        }
        if (raw[index] == '[' && !escaped(index)) {
          val labelEnd = closing("](", index + 1, end)
          if (labelEnd >= 0) {
            // Balanced parentheses are supported in URLs, e.g. Wikipedia links.
            var close = labelEnd + 2
            var balance = 1
            while (close < end && balance > 0) {
              if (!escaped(close)) when (raw[close]) { '(' -> balance++; ')' -> balance-- }
              if (balance > 0) close++
            }
            if (balance == 0) {
              val target = raw.substring(labelEnd + 2, close).trim()
              if (safeLink(target)) {
                hide(index, index + 1); hide(labelEnd, close + 1)
                sourceRuns += InlineRun(index + 1, labelEnd, "link", target)
                scan(index + 1, labelEnd, depth + 1)
                index = close + 1; continue
              }
            }
          }
        }
        val mark = when {
          raw.startsWith("***", index) -> "***"
          raw.startsWith("___", index) -> "___"
          raw.startsWith("**", index) -> "**"
          raw.startsWith("__", index) -> "__"
          raw.startsWith("~~", index) -> "~~"
          raw[index] == '`' -> "`"
          raw[index] == '*' -> "*"
          raw[index] == '_' && (index == 0 || !raw[index - 1].isLetterOrDigit()) -> "_"
          else -> null
        }
        if (mark != null && !escaped(index)) {
          val close = closing(mark, index + mark.length, end)
          if (close > index + mark.length && !raw[index + mark.length].isWhitespace() && !raw[close - 1].isWhitespace()) {
            hide(index, index + mark.length); hide(close, close + mark.length)
            val kind = when (mark) { "***", "___" -> "boldItalic"; "**", "__" -> "bold"; "~~" -> "strike"; "`" -> "code"; else -> "italic" }
            sourceRuns += InlineRun(index + mark.length, close, kind)
            if (kind != "code") scan(index + mark.length, close, depth + 1)
            index = close + mark.length; continue
          }
        }
        index++
      }
    }
    scan(0, raw.length, 0)
    val rawToVisible = IntArray(raw.length + 1)
    val visibleToRaw = mutableListOf<Int>()
    val display = StringBuilder()
    raw.forEachIndexed { index, character ->
      rawToVisible[index] = display.length
      if (!hidden[index]) { visibleToRaw += index; display.append(character) }
    }
    rawToVisible[raw.length] = display.length
    // End-of-text maps before closing marks, so typing continues the current format.
    visibleToRaw += if (visibleToRaw.isEmpty()) raw.length else visibleToRaw.last() + 1
    val runs = sourceRuns.map { it.copy(start = rawToVisible[it.start], end = rawToVisible[it.end]) }.filter { it.end > it.start }
    return InlineDocument(display.toString(), runs, rawToVisible, visibleToRaw.toIntArray())
  }

  fun safeLink(target: String): Boolean = (target.startsWith("https://", true) || target.startsWith("http://", true) || target.startsWith("mailto:", true)) &&
    target.none { it.isWhitespace() || it.isISOControl() }
}
