package pro.lurg.notes.editor

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Typeface
import android.text.Editable
import android.text.Spanned
import android.text.TextWatcher
import android.text.style.BackgroundColorSpan
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StrikethroughSpan
import android.text.style.StyleSpan
import android.text.style.TypefaceSpan
import android.view.ActionMode
import android.view.Menu
import android.view.MenuItem
import android.view.inputmethod.InputMethodManager
import androidx.appcompat.widget.AppCompatEditText
import java.util.ArrayDeque

class LurgMarkdownEditText(context: Context) : AppCompatEditText(context) {
  var onMarkdownChanged: ((String) -> Unit)? = null
  var onSelectionChangedCallback: (() -> Unit)? = null
  private val undoStack = ArrayDeque<String>()
  private val redoStack = ArrayDeque<String>()
  private var rendering = false
  private var applying = false

  init {
    setTextColor(0xFFF8F4FF.toInt())
    setHintTextColor(0x668F879E)
    textSize = 19f
    typeface = Typeface.create("sans", Typeface.NORMAL)
    setLineSpacing(dp(5).toFloat(), 1f)
    setPadding(dp(24), dp(132), dp(24), dp(210))
    background = null
    minLines = 30
    isLongClickable = true
    customSelectionActionModeCallback = noSystemActionMode()
    customInsertionActionModeCallback = noSystemActionMode()
    addTextChangedListener(object : TextWatcher {
      override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {
        if (!applying && !rendering) pushUndo(s?.toString().orEmpty())
      }
      override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
      override fun afterTextChanged(s: Editable?) {
        if (!applying && !rendering) {
          redoStack.clear()
          renderMarkdown()
          onMarkdownChanged?.invoke(markdown())
        }
      }
    })
  }

  fun setMarkdown(markdown: String) {
    applying = true
    setText(markdown)
    setSelection(text?.length ?: 0)
    applying = false
    renderMarkdown()
  }

  fun markdown(): String = text?.toString().orEmpty()

  fun focusKeyboard() {
    requestFocus()
    post {
      (context.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
        .showSoftInput(this, InputMethodManager.SHOW_IMPLICIT)
    }
  }

  fun undo() {
    val previous = undoStack.pollLast() ?: return
    redoStack.addLast(markdown())
    applyText(TextEditResult(previous, previous.length, previous.length))
  }

  fun redo() {
    val next = redoStack.pollLast() ?: return
    undoStack.addLast(markdown())
    applyText(TextEditResult(next, next.length, next.length))
  }

  fun copySelection() {
    val selected = selectedText()
    if (selected.isBlank()) return
    clipboard().setPrimaryClip(ClipData.newPlainText("Lurg Notes", selected))
  }

  fun cutSelection() {
    val selected = selectedText()
    if (selected.isBlank()) return
    clipboard().setPrimaryClip(ClipData.newPlainText("Lurg Notes", selected))
    replaceSelection("")
  }

  fun pasteClipboard() {
    val clip = clipboard().primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString().orEmpty()
    if (clip.isNotBlank()) replaceSelection(clip)
  }

  fun selectAllText() = setSelection(0, text?.length ?: 0)
  fun bold() = transform { t, s, e -> MarkdownTransforms.wrap(t, s, e, "**") }
  fun italic() = transform { t, s, e -> MarkdownTransforms.wrap(t, s, e, "*") }
  fun strike() = transform { t, s, e -> MarkdownTransforms.wrap(t, s, e, "~~") }
  fun inlineCode() = transform { t, s, e -> MarkdownTransforms.wrap(t, s, e, "`") }
  fun link() = transform { t, s, e -> MarkdownTransforms.wrap(t, s, e, "[", "](https://)") }
  fun heading(level: Int) = transform { t, s, e -> MarkdownTransforms.heading(t, s, e, level) }
  fun paragraph() = transform { t, s, e -> MarkdownTransforms.linePrefix(t, s, e, "") }
  fun quote() = transform { t, s, e -> MarkdownTransforms.linePrefix(t, s, e, "> ") }
  fun bullet() = transform { t, s, e -> MarkdownTransforms.linePrefix(t, s, e, "- ") }
  fun checklist() = transform { t, s, e -> MarkdownTransforms.linePrefix(t, s, e, "- [ ] ") }
  fun numbered() = transform { t, s, e -> MarkdownTransforms.numbered(t, s, e) }
  fun codeBlock() = transform { t, s, e -> MarkdownTransforms.codeBlock(t, s, e) }
  fun insertTable(cols: Int, rows: Int) = replaceSelection(MarkdownTransforms.table(cols, rows))
  fun insertLatex() = replaceSelection("\n$$\nE = mc^2\n$$\n")
  fun insertEmoji(value: String) = replaceSelection(value)
  fun insertMarkdown(value: String) = replaceSelection(value)
  fun replaceSelectedOrAll(value: String) {
    val source = markdown()
    val start = minOf(selectionStart, selectionEnd).coerceIn(0, source.length)
    val end = maxOf(selectionStart, selectionEnd).coerceIn(0, source.length)
    if (start == end) {
      applyText(TextEditResult(value, value.length, value.length))
    } else {
      applyText(TextEditResult(source.replaceRange(start, end, value), start, start + value.length))
    }
  }

  fun selectedTextOrAll(): String {
    val selected = selectedText()
    return selected.ifBlank { markdown() }
  }

  private fun transform(block: (String, Int, Int) -> TextEditResult) {
    applyText(block(markdown(), selectionStart, selectionEnd))
  }

  private fun applyText(result: TextEditResult) {
    applying = true
    setText(result.text)
    val start = result.selectionStart.coerceIn(0, result.text.length)
    val end = result.selectionEnd.coerceIn(0, result.text.length)
    setSelection(start, end)
    applying = false
    renderMarkdown()
    onMarkdownChanged?.invoke(markdown())
  }

  private fun replaceSelection(replacement: String) {
    val source = markdown()
    val start = minOf(selectionStart, selectionEnd).coerceIn(0, source.length)
    val end = maxOf(selectionStart, selectionEnd).coerceIn(0, source.length)
    applyText(TextEditResult(source.replaceRange(start, end, replacement), start + replacement.length, start + replacement.length))
  }

  private fun selectedText(): String {
    val source = markdown()
    val start = minOf(selectionStart, selectionEnd).coerceIn(0, source.length)
    val end = maxOf(selectionStart, selectionEnd).coerceIn(0, source.length)
    return source.substring(start, end)
  }

  private fun pushUndo(value: String) {
    if (undoStack.peekLast() == value) return
    undoStack.addLast(value)
    while (undoStack.size > 120) undoStack.removeFirst()
  }

  override fun onSelectionChanged(selStart: Int, selEnd: Int) {
    super.onSelectionChanged(selStart, selEnd)
    post { onSelectionChangedCallback?.invoke() }
  }

  fun renderMarkdown() {
    val editable = text ?: return
    if (rendering) return
    rendering = true
    editable.getSpans(0, editable.length, Any::class.java)
      .filter {
        it is StyleSpan || it is RelativeSizeSpan || it is StrikethroughSpan ||
          it is TypefaceSpan || it is ForegroundColorSpan || it is BackgroundColorSpan
      }
      .forEach { editable.removeSpan(it) }
    val raw = editable.toString()
    Regex("(?m)^# (.+)$").findAll(raw).forEach { span(editable, it.range.first, it.range.last + 1, StyleSpan(Typeface.BOLD), RelativeSizeSpan(1.45f)) }
    Regex("(?m)^## (.+)$").findAll(raw).forEach { span(editable, it.range.first, it.range.last + 1, StyleSpan(Typeface.BOLD), RelativeSizeSpan(1.25f)) }
    Regex("(?m)^### (.+)$").findAll(raw).forEach { span(editable, it.range.first, it.range.last + 1, StyleSpan(Typeface.BOLD), RelativeSizeSpan(1.12f)) }
    Regex("\\*\\*(.+?)\\*\\*").findAll(raw).forEach { span(editable, it.groups[1]!!.range.first, it.groups[1]!!.range.last + 1, StyleSpan(Typeface.BOLD)) }
    Regex("(?<!\\*)\\*(?!\\*)(.+?)(?<!\\*)\\*(?!\\*)").findAll(raw).forEach { span(editable, it.groups[1]!!.range.first, it.groups[1]!!.range.last + 1, StyleSpan(Typeface.ITALIC)) }
    Regex("~~(.+?)~~").findAll(raw).forEach { span(editable, it.groups[1]!!.range.first, it.groups[1]!!.range.last + 1, StrikethroughSpan()) }
    Regex("`([^`]+)`").findAll(raw).forEach { span(editable, it.groups[1]!!.range.first, it.groups[1]!!.range.last + 1, TypefaceSpan("monospace"), ForegroundColorSpan(0xFFDCC9FF.toInt())) }
    Regex("(?m)^> .+$").findAll(raw).forEach { span(editable, it.range.first, it.range.last + 1, ForegroundColorSpan(0xFFDCC9FF.toInt())) }
    Regex("(?m)^\\|.+\\|$").findAll(raw).forEach { span(editable, it.range.first, it.range.last + 1, TypefaceSpan("monospace"), BackgroundColorSpan(0x228B5CF6), ForegroundColorSpan(0xFFF6F1FF.toInt())) }
    Regex("(?m)^[-*]\\s+\\[[ xX]\\]\\s+.+$").findAll(raw).forEach { span(editable, it.range.first, it.range.last + 1, ForegroundColorSpan(0xFFEFE7FF.toInt())) }
    rendering = false
  }

  private fun span(editable: Editable, start: Int, end: Int, vararg spans: Any) {
    spans.forEach { editable.setSpan(it, start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE) }
  }

  private fun clipboard(): ClipboardManager {
    return context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
  }

  private fun noSystemActionMode(): ActionMode.Callback {
    return object : ActionMode.Callback {
      override fun onCreateActionMode(mode: ActionMode?, menu: Menu?): Boolean {
        menu?.clear()
        return true
      }
      override fun onPrepareActionMode(mode: ActionMode?, menu: Menu?): Boolean {
        menu?.clear()
        return true
      }
      override fun onActionItemClicked(mode: ActionMode?, item: MenuItem?): Boolean = true
      override fun onDestroyActionMode(mode: ActionMode?) = Unit
    }
  }

  private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()
}
