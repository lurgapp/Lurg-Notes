package pro.lurg.notes

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import pro.lurg.notes.api.AuthUser
import pro.lurg.notes.api.E2eeDeviceStatus

enum class NotesSettingsRoute(val title: String) {
  Home("Настройки"), Account("Аккаунт"), Privacy("Приватность"), Devices("Устройства и recovery"),
  Sync("Синхронизация"), Editor("Редактор"), Updates("Обновления"), About("О приложении");
  fun parent(): NotesSettingsRoute? = if (this == Home) null else Home
}

private val Surface = Color(0xFF171D24)
private val Ink = Color(0xFFF5F6F8)
private val Subtle = Color(0xFF919BA9)
private val Blue = Color(0xFF67B9FA)

@Composable
fun NotesSettingsScreen(
  user: AuthUser, checkingUpdate: Boolean, updateStatus: String?, updateAvailable: Boolean,
  onClose: () -> Unit, onLogout: () -> Unit, onGraph: () -> Unit,
  onCheckUpdate: () -> Unit, onDownloadUpdate: () -> Unit,
  e2eeStatus: String?, recoverySecret: String?, devices: List<E2eeDeviceStatus>, currentDeviceId: String,
  onSetupE2ee: () -> Unit, onApproveE2ee: (E2eeDeviceStatus) -> Unit,
  onRevokeE2ee: (E2eeDeviceStatus) -> Unit, onRecoverE2ee: (String) -> Unit,
  privateScreen: Boolean, onPrivateScreen: () -> Unit, syncStatus: String,
  onSync: () -> Unit, motionEnabled: Boolean
) {
  var routeName by rememberSaveable(user.id) { mutableStateOf(NotesSettingsRoute.Home.name) }
  val route = NotesSettingsRoute.valueOf(routeName)
  var recoveryInput by remember { mutableStateOf("") }
  var logoutPrompt by remember { mutableStateOf(false) }
  var trustPrompt by remember { mutableStateOf<E2eeDeviceStatus?>(null) }
  var revokePrompt by remember { mutableStateOf<E2eeDeviceStatus?>(null) }
  val uri = LocalUriHandler.current
  fun back() { route.parent()?.let { routeName = it.name } ?: onClose() }
  BackHandler { back() }
  Column(Modifier.fillMaxSize().background(Surface).statusBarsPadding().navigationBarsPadding().imePadding()) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
      TextButton(onClick = { back() }, modifier = Modifier.size(48.dp).semantics { contentDescription = "Назад" }) { Text("←", color = Ink, fontSize = 26.sp) }
      Text(route.title, color = Ink, fontSize = 24.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 8.dp))
    }
    AnimatedContent(route, transitionSpec = { fadeIn(tween(if (motionEnabled) 130 else 0)) togetherWith fadeOut(tween(if (motionEnabled) 90 else 0)) }, label = "settings-route", modifier = Modifier.weight(1f)) { destination ->
      Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 22.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        when (destination) {
          NotesSettingsRoute.Home -> {
            Text(user.name.ifBlank { "Lurg User" }, color = Ink, fontSize = 27.sp, fontWeight = FontWeight.Bold)
            Text(user.email, color = Subtle, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(bottom = 16.dp))
            SettingsRow("Аккаунт", "Lurg ID и личный Graph") { routeName = NotesSettingsRoute.Account.name }
            SettingsRow("Приватность", if (privateScreen) "Приватный экран включён" else "Защита экрана и облачной копии") { routeName = NotesSettingsRoute.Privacy.name }
            SettingsRow("Устройства и recovery", "Доверенные устройства E2EE") { routeName = NotesSettingsRoute.Devices.name }
            SettingsRow("Синхронизация", syncStatus) { routeName = NotesSettingsRoute.Sync.name }
            SettingsRow("Редактор", "Форматирование и управление") { routeName = NotesSettingsRoute.Editor.name }
            SettingsRow("Обновления", "Lurg Notes ${BuildConfig.VERSION_NAME}") { routeName = NotesSettingsRoute.Updates.name }
            SettingsRow("О приложении", "Документация и условия") { routeName = NotesSettingsRoute.About.name }
          }
          NotesSettingsRoute.Account -> {
            SettingsValue("Имя", user.name)
            SettingsValue("Почта", user.email)
            SettingsValue("Lurg ID", user.id)
            SettingsRow("Мой Graph", "Ваш подтверждённый личный контекст", onClick = onGraph)
            SettingsCaption("При смене аккаунта локальные заметки изолируются по Lurg ID. Они не отображаются другому пользователю.")
            SettingsAction("Выйти из аккаунта", danger = true) { logoutPrompt = true }
          }
          NotesSettingsRoute.Privacy -> {
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
              Column(Modifier.weight(1f)) { Text("Приватный экран", color = Ink, fontSize = 18.sp); SettingsCaption("Скрывает содержимое в недавних приложениях и запрещает снимки экрана.") }
              Switch(checked = privateScreen, onCheckedChange = { onPrivateScreen() }, modifier = Modifier.semantics { contentDescription = "Приватный экран" })
            }
            SettingsValue("Облачная копия", "E2EE v2 между доверенными устройствами")
            SettingsCaption("Локальный текст хранится в базе приложения без отдельного шифрования. Фотографии пока остаются только на этом устройстве.")
            SettingsValue("Обработка AI", "Только после вашего разрешения")
            SettingsCaption("AI получает выбранный текст или текст заметки. Результат открывается в предпросмотре и применяется отдельно.")
            SettingsRow("Доверенные устройства", "Настройка ключей и восстановления") { routeName = NotesSettingsRoute.Devices.name }
          }
          NotesSettingsRoute.Devices -> {
            SettingsCaption("Новое устройство получает доступ к облачной копии после подтверждения доверенным устройством или восстановления с recovery secret.")
            SettingsAction("Настроить E2EE и recovery", onClick = onSetupE2ee)
            if (recoverySecret != null) {
              Text("Сохраните recovery secret офлайн", color = Color(0xFFFFCC80), fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
              Text(recoverySecret, color = Ink, fontSize = 14.sp)
              SettingsCaption("Показывается только в этой сессии. Не отправляйте его в переписку.")
            }
            e2eeStatus?.let { SettingsCaption(it) }
            Text("Восстановление устройства", color = Ink, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            BasicTextField(value = recoveryInput, onValueChange = { recoveryInput = it }, singleLine = true,
              visualTransformation = PasswordVisualTransformation(), textStyle = androidx.compose.ui.text.TextStyle(color = Ink, fontSize = 16.sp), cursorBrush = SolidColor(Blue),
              modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Color(0xFF222B35)).padding(16.dp).semantics { contentDescription = "Recovery secret" },
              decorationBox = { inner -> if (recoveryInput.isBlank()) Text("Recovery secret", color = Subtle, fontSize = 16.sp); inner() })
            SettingsAction("Восстановить это устройство", enabled = recoveryInput.isNotBlank()) { onRecoverE2ee(recoveryInput); recoveryInput = "" }
            Text("Устройства", color = Ink, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 12.dp))
            if (devices.isEmpty()) SettingsCaption("Список появится после проверки E2EE.")
            devices.forEach { device ->
              Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Color(0xFF202831)).padding(15.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(device.name + if (device.id == currentDeviceId) " · это устройство" else "", color = Ink, fontSize = 17.sp)
                SettingsCaption("${device.status} · ${device.fingerprint.take(24)}")
                if (device.status == "pending") SettingsAction("Доверить", onClick = { trustPrompt = device })
                if (device.status == "active" && device.id != currentDeviceId) SettingsAction("Отозвать доступ", danger = true) { revokePrompt = device }
              }
            }
          }
          NotesSettingsRoute.Sync -> {
            SettingsValue("Состояние", syncStatus)
            SettingsCaption("Изменения сохраняются на устройстве сразу. Пока приложение открыто, изменения с других устройств запускают синхронизацию автоматически. После возвращения сети догружаются пропущенные изменения. Резервная проверка выполняется каждые 30 секунд; фоновую проверку планирует Android.")
            SettingsAction("Синхронизировать сейчас", onClick = onSync)
            SettingsCaption("Фотографии пока доступны только на этом устройстве. Не удаляйте приложение, если хотите сохранить их.")
          }
          NotesSettingsRoute.Editor -> {
            SettingsValue("Форматирование", "Выделите текст и нажмите Aa")
            SettingsCaption("Жирный, курсив, зачёркивание, код и ссылки применяются к выделению. H1–H3, списки, чек-листы, цитаты и таблицы — к блоку.")
            SettingsValue("Markdown", "Формат виден во время редактирования")
            SettingsCaption("Вставляйте Markdown из буфера. Заголовки и списки превращаются в блоки; завершённые inline-маркеры показываются как форматирование. Кнопка чтения открывает чистый предпросмотр.")
            SettingsValue("Сохранение", "Автоматически на устройстве")
            SettingsCaption("Стрелка и системный жест назад закрывают сначала открытое меню, затем заметку. Отмена и повтор работают в текущей сессии редактора.")
            SettingsValue("Анимации", if (motionEnabled) "С учётом системных настроек" else "Отключены в системе")
          }
          NotesSettingsRoute.Updates -> {
            SettingsValue("Установлено", "Lurg Notes ${BuildConfig.VERSION_NAME}")
            updateStatus?.let { SettingsCaption(it) }
            SettingsAction(if (checkingUpdate) "Проверяем…" else "Проверить обновления", enabled = !checkingUpdate, onClick = onCheckUpdate)
            if (updateAvailable) SettingsAction("Установить обновление", onClick = onDownloadUpdate)
            SettingsCaption("Новый выпуск приходит пушем от Lurg Notes. После отказа обновление остаётся здесь. APK проверяется по размеру, SHA-256 и подписи перед установкой.")
          }
          NotesSettingsRoute.About -> {
            Text("Lurg Notes", color = Ink, fontSize = 30.sp, fontWeight = FontWeight.Bold)
            SettingsCaption("Версия ${BuildConfig.VERSION_NAME} · Android")
            SettingsRow("Документация", "Редактор, синхронизация и приватность") { uri.openUri("https://lurg.pro/docs/lurg-notes") }
            SettingsRow("Политика конфиденциальности", "lurg.pro") { uri.openUri("https://lurg.pro/privacy") }
            SettingsRow("Сайт Notes", "notes.lurg.pro") { uri.openUri("https://notes.lurg.pro") }
          }
        }
        Spacer(Modifier.height(20.dp))
      }
    }
  }
  if (logoutPrompt) SettingsConfirmation("Выйти из аккаунта?", "Локальная копия сохранится, но будет скрыта до повторного входа в этот Lurg ID.", { logoutPrompt = false }, { logoutPrompt = false; onLogout() })
  trustPrompt?.let { device -> SettingsConfirmation("Доверить устройству?", "${device.name} получит доступ к вашей облачной копии заметок. Сверьте отпечаток: ${device.fingerprint}", { trustPrompt = null }, { trustPrompt = null; onApproveE2ee(device) }) }
  revokePrompt?.let { device -> SettingsConfirmation("Отозвать доступ?", "${device.name} больше не сможет синхронизировать облачную копию.", { revokePrompt = null }, { revokePrompt = null; onRevokeE2ee(device) }) }
}

@Composable private fun SettingsRow(title: String, subtitle: String, onClick: () -> Unit) {
  Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).clickable(onClick = onClick).padding(vertical = 14.dp, horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
    Column(Modifier.weight(1f)) { Text(title, color = Ink, fontSize = 18.sp); Text(subtitle, color = Subtle, fontSize = 13.sp, modifier = Modifier.padding(top = 4.dp)) }
    Text("›", color = Subtle, fontSize = 26.sp, modifier = Modifier.padding(start = 12.dp))
  }
}
@Composable private fun SettingsValue(label: String, value: String) { Column(Modifier.padding(vertical = 6.dp)) { Text(label, color = Subtle, fontSize = 13.sp); Text(value, color = Ink, fontSize = 18.sp, modifier = Modifier.padding(top = 5.dp)) } }
@Composable private fun SettingsCaption(text: String) { Text(text, color = Subtle, fontSize = 14.sp, lineHeight = 21.sp) }
@Composable private fun SettingsAction(label: String, danger: Boolean = false, enabled: Boolean = true, onClick: () -> Unit) {
  TextButton(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).clip(RoundedCornerShape(13.dp)).background(Color(0xFF222B35))) { Text(label, color = if (!enabled) Subtle else if (danger) Color(0xFFFF8797) else Blue, fontSize = 16.sp) }
}
@Composable private fun SettingsConfirmation(title: String, body: String, cancel: () -> Unit, confirm: () -> Unit) {
  AlertDialog(onDismissRequest = cancel, title = { Text(title) }, text = { Text(body) }, confirmButton = { TextButton(onClick = confirm) { Text("Да") } }, dismissButton = { TextButton(onClick = cancel) { Text("Отмена") } }, containerColor = Surface, titleContentColor = Ink, textContentColor = Ink)
}
