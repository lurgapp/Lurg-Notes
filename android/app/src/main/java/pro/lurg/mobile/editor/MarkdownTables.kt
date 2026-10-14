package pro.lurg.notes.editor

/** Preserve inline Markdown while interpreting escaped cell separators. */
object MarkdownTables {
  fun cells(line: String): List<String> {
    val source = line.trim()
    val cells = mutableListOf<String>()
    val cell = StringBuilder()
    var index = 0
    while (index < source.length) {
      val character = source[index]
      if (character == '\\' && source.getOrNull(index + 1) in setOf('\\', '|')) {
        cell.append(source[index + 1]); index += 2; continue
      }
      if (character == '|') { cells += cell.toString().trim(); cell.clear() }
      else cell.append(character)
      index++
    }
    cells += cell.toString().trim()
    if (source.startsWith('|')) cells.removeAt(0)
    if (source.endsWith('|') && source.dropLast(1).takeLastWhile { it == '\\' }.length % 2 == 0 && cells.isNotEmpty()) cells.removeAt(cells.lastIndex)
    return cells
  }

  fun rows(markdown: String): List<List<String>> = markdown.lineSequence()
    .filter(String::isNotBlank).map(::cells)
    .filter { row -> row.isNotEmpty() && !row.all { it.matches(Regex("^:?-{3,}:?$")) } }.toList()

  fun serialize(rows: List<List<String>>): String {
    val columns = rows.maxOfOrNull { it.size }?.coerceAtLeast(1) ?: 1
    val normalized = rows.ifEmpty { listOf(List(columns) { "" }) }.map { it + List(columns - it.size) { "" } }
    fun line(cells: List<String>) = "| ${cells.joinToString(" | ") { it.replace("\\", "\\\\").replace("|", "\\|").replace('\n', ' ') }} |"
    return buildList {
      add(line(normalized.first()))
      add(line(List(columns) { "---" }))
      normalized.drop(1).forEach { add(line(it)) }
    }.joinToString("\n")
  }
}
