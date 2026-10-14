package pro.lurg.notes.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import pro.lurg.notes.MainActivity
import pro.lurg.notes.R
import pro.lurg.notes.api.PushRegistrationClient

class LurgNotesMessagingService : FirebaseMessagingService() {
  override fun onNewToken(token: String) {
    super.onNewToken(token)
    CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
      runCatching { PushRegistrationClient(applicationContext).register(token) }
    }
  }

  override fun onMessageReceived(message: RemoteMessage) {
    super.onMessageReceived(message)
    // The fixed Notes manifest is the authority; push payloads never select an APK.
    if (message.data["type"] != "app_update") return
    if (message.data["app_id"]?.let { it != "notes" } == true ||
      message.data["package_name"]?.let { it != "pro.lurg.notes" } == true) return
    val title = message.notification?.title ?: message.data["title"] ?: "Lurg Notes"
    val body = message.notification?.body ?: message.data["body"] ?: "Доступно обновление приложения"
    val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    val channelId = "lurg_notes_updates"
    if (android.os.Build.VERSION.SDK_INT >= 26) manager.createNotificationChannel(NotificationChannel(channelId, "Обновления Lurg Notes", NotificationManager.IMPORTANCE_DEFAULT))
    val intent = android.content.Intent(this, MainActivity::class.java).apply {
      flags = android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP
      putExtra("open_update", message.data["type"] == "app_update")
      putExtra("app_id", "notes")
      putExtra("package_name", "pro.lurg.notes")
      message.data["version_code"]?.let { putExtra("version_code", it) }
    }
    val pending = android.app.PendingIntent.getActivity(this, 100, intent,
      android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE)
    manager.notify(100, NotificationCompat.Builder(this, channelId)
      .setSmallIcon(R.mipmap.ic_launcher)
      .setContentTitle(title).setContentText(body).setAutoCancel(true).setContentIntent(pending).build())
  }
}
