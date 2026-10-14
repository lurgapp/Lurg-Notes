package pro.lurg.notes

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import pro.lurg.notes.api.AuthUser
import pro.lurg.notes.api.UserGraph
import pro.lurg.notes.api.UserGraphClient
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

@Composable
fun UserGraphScreen(user: AuthUser, onClose: () -> Unit) {
  BackHandler(onBack = onClose)
  val context = androidx.compose.ui.platform.LocalContext.current
  var graph by remember { mutableStateOf<UserGraph?>(null) }
  var error by remember { mutableStateOf<String?>(null) }
  var loading by remember { mutableStateOf(true) }
  var scale by remember { mutableFloatStateOf(1f) }
  var pan by remember { mutableStateOf(Offset.Zero) }
  var size by remember { mutableStateOf(IntSize.Zero) }

  LaunchedEffect(user.id) {
    loading = true
    error = null
    runCatching { withContext(Dispatchers.IO) { UserGraphClient(context).load(user.id) } }
      .onSuccess { graph = it }
      .onFailure { error = it.message ?: "Не удалось загрузить Graph" }
    loading = false
  }

  Box(Modifier.fillMaxSize().background(LurgVoid).statusBarsPadding().navigationBarsPadding()) {
    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp, vertical = 16.dp)) {
      Row(verticalAlignment = Alignment.CenterVertically) {
        IslandButton("←", modifier = Modifier.width(54.dp).height(50.dp)) { onClose() }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
          Text("Мой Graph", color = LurgText, fontSize = 28.sp)
          Text("Личный контекст Lurg", color = LurgMuted, fontSize = 14.sp)
        }
        IslandButton("⊙", modifier = Modifier.width(54.dp).height(50.dp)) { scale = 1f; pan = Offset.Zero }
      }
      Spacer(Modifier.height(18.dp))
      Box(
        Modifier.fillMaxSize().clip(RoundedCornerShape(24.dp)).background(LurgPanel)
          .onSizeChanged { size = it }
          .pointerInput(graph?.nodes?.size) {
            detectTransformGestures { _, gesturePan, gestureZoom, _ ->
              // Compose may report an unspecified centroid during a pointer handoff.
              // Never place it into graphicsLayer coordinates: it causes a fatal hit-test.
              val safeZoom = gestureZoom.takeIf { it.isFinite() }?.coerceIn(0.985f, 1.015f) ?: 1f
              val safePan = if (gesturePan.x.isFinite() && gesturePan.y.isFinite()) gesturePan else Offset.Zero
              val maxX = size.width.coerceAtLeast(1) * .75f
              val maxY = size.height.coerceAtLeast(1) * .75f
              pan = Offset(
                x = (pan.x + safePan.x).coerceIn(-maxX, maxX),
                y = (pan.y + safePan.y).coerceIn(-maxY, maxY)
              )
              scale = (scale * safeZoom).coerceIn(0.7f, 2.2f)
            }
          },
        contentAlignment = Alignment.Center
      ) {
        when {
          loading -> Text("Загружаем Graph…", color = LurgMuted, fontSize = 16.sp)
          error != null -> Text(error.orEmpty(), color = Color(0xFFFF8CAB), fontSize = 16.sp, modifier = Modifier.padding(24.dp))
          graph?.nodes.isNullOrEmpty() -> Text("В Graph пока нет подтверждённых фактов", color = LurgMuted, fontSize = 16.sp, modifier = Modifier.padding(24.dp))
          else -> GraphCanvas(graph!!, size, scale, pan)
        }
      }
    }
  }
}

@Composable
private fun GraphCanvas(graph: UserGraph, size: IntSize, scale: Float, pan: Offset) {
  val points = remember(graph, size) {
    val center = Offset(size.width / 2f, size.height / 2f)
    val radius = min(size.width, size.height).coerceAtLeast(300) * .29f
    graph.nodes.mapIndexed { index, node ->
      val angle = (-PI / 2 + index * 2 * PI / graph.nodes.size).toFloat()
      node.id to center + Offset(cos(angle) * radius, sin(angle) * radius)
    }.toMap()
  }
  Canvas(Modifier.fillMaxSize().graphicsLayer { scaleX = scale; scaleY = scale; translationX = pan.x; translationY = pan.y }) {
    graph.edges.forEach { edge ->
      val from = points[edge.sourceId] ?: return@forEach
      val to = points[edge.targetId] ?: return@forEach
      drawLine(Color(0x668B5CF6), from, to, strokeWidth = 2f)
    }
    points.forEach { (id, point) ->
      val node = graph.nodes.first { it.id == id }
      drawCircle(Color(0xFF151120), radius = 28f, center = point)
      drawCircle(Color(0xFF9D7CFF), radius = 28f, center = point, style = Stroke(2.2f))
      drawContext.canvas.nativeCanvas.drawText(node.label.take(22), point.x - 48f, point.y + 52f,
        android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply { color = android.graphics.Color.WHITE; textSize = 24f })
    }
  }
}
