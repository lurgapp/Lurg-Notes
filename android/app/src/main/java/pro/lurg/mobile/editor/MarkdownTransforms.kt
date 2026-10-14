package pro.lurg.notes.editor

import kotlin.math.max
import kotlin.math.min

data class TextEditResult(
  val text: String,
  val selectionStart: Int,
  val selectionEnd: Int
)

object MarkdownTransforms {
  fun wrap(text: String, startRaw: Int, endRaw: Int, prefix: String, suffix: String = prefix): TextEditResult {
    val start = min(startRaw, endRaw).coerceIn(0, text.length)
    val end = max(startRaw, endRaw).coerceIn(0, text.length)
    if (start == end) {
      val next = text.substring(0, start) + prefix + suffix + text.substring(start)
      val cursor = start + prefix.length
      return TextEditResult(next, cursor, cursor)
    }
    val selected = text.substring(start, end)
    val alreadyWrapped = start >= prefix.length &&
      text.substring(start - prefix.length, start) == prefix &&
      end + suffix.length <= text.length &&
      text.substring(end, end + suffix.length) == suffix
    if (alreadyWrapped) {
      val next = text.removeRange(end, end + suffix.length).removeRange(start - prefix.length, start)
      return TextEditResult(next, start - prefix.length, end - prefix.length)
    }
    val next = text.substring(0, start) + prefix + selected + suffix + text.substring(end)
    return TextEditResult(next, start + prefix.length, end + prefix.length)
  }

  fun linePrefix(text: String, startRaw: Int, endRaw: Int, prefix: String): TextEditResult {
    val range = lineRange(text, startRaw, endRaw)
    val source = text.substring(range.first, range.second)
    val replacement = source.split("\n").joinToString("\n") { line ->
      prefix + stripBlockPrefix(line)
    }
    val next = text.replaceRange(range.first, range.second, replacement)
    return TextEditResult(next, range.first, range.first + replacement.length)
  }

  fun heading(text: String, start: Int, end: Int, level: Int): TextEditResult {
    return linePrefix(text, start, end, "#".repeat(level) + " ")
  }

  fun numbered(text: String, startRaw: Int, endRaw: Int): TextEditResult {
    val range = lineRange(text, startRaw, endRaw)
    val replacement = text.substring(range.first, range.second)
      .split("\n")
      .mapIndexed { index, line -> "${index + 1}. ${stripBlockPrefix(line)}" }
      .joinToString("\n")
    val next = text.replaceRange(range.first, range.second, replacement)
    return TextEditResult(next, range.first, range.first + replacement.length)
  }

  fun codeBlock(text: String, startRaw: Int, endRaw: Int): TextEditResult {
    val start = min(startRaw, endRaw).coerceIn(0, text.length)
    val end = max(startRaw, endRaw).coerceIn(0, text.length)
    val selected = text.substring(start, end)
    val insert = if (selected.isBlank()) "```\n\n```" else "```\n$selected\n```"
    val next = text.replaceRange(start, end, insert)
    val innerStart = start + 4
    val innerEnd = if (selected.isBlank()) innerStart else innerStart + selected.length
    return TextEditResult(next, innerStart, innerEnd)
  }

  fun table(columns: Int, rows: Int): String {
    val cols = columns.coerceIn(2, 6)
    val bodyRows = rows.coerceIn(1, 8)
    val header = "|" + (1..cols).joinToString("|") { " Колонка $it " } + "|"
    val divider = "|" + (1..cols).joinToString("|") { " --- " } + "|"
    val body = (1..bodyRows).joinToString("\n") {
      "|" + (1..cols).joinToString("|") { " " } + "|"
    }
    return "\n$header\n$divider\n$body\n"
  }

  private fun lineRange(text: String, startRaw: Int, endRaw: Int): Pair<Int, Int> {
    val start = min(startRaw, endRaw).coerceIn(0, text.length)
    val end = max(startRaw, endRaw).coerceIn(0, text.length)
    val lineStart = text.lastIndexOf('\n', max(0, start - 1)).let { if (it < 0) 0 else it + 1 }
    val lineEnd = text.indexOf('\n', end).let { if (it < 0) text.length else it }
    return lineStart to lineEnd
  }

  private fun stripBlockPrefix(line: String): String {
    return line
      .replace(Regex("^#{1,6}\\s+"), "")
      .replace(Regex("^>\\s*"), "")
      .replace(Regex("^[-*]\\s+\\[[ xX]\\]\\s+"), "")
      .replace(Regex("^[-*]\\s+"), "")
      .replace(Regex("^\\d+\\.\\s+"), "")
  }
}
