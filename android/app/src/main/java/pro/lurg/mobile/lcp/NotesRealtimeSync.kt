package pro.lurg.notes.lcp

import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.*
import okhttp3.*
import pro.lurg.notes.security.NotesSessionSnapshot

enum class NotesRealtimeState { Offline, Connecting, Connected }

/** Foreground-only invalidations; HTTP's durable cursor remains the source of truth.
 * Every callback is fenced by connection epoch and the exact authenticated owner/token pair.
 * Frames are processed by one bounded actor, never concurrent listener coroutines.
 */
class NotesRealtimeSync(
  private val scope: CoroutineScope,
  private val snapshot: () -> NotesSessionSnapshot?,
  private val isCurrent: (NotesSessionSnapshot) -> Boolean,
  private val isOnline: () -> Boolean,
  private val requestSync: () -> Unit,
  private val url: String,
  private val http: OkHttpClient = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).pingInterval(20, TimeUnit.SECONDS).build(),
) {
  private sealed interface Signal {
    data object Reconcile : Signal
    data class Open(val epoch: Long, val socket: WebSocket) : Signal
    data class Frame(val epoch: Long, val text: String) : Signal
    data class Failed(val epoch: Long) : Signal
  }
  private val signals = Channel<Signal>(64)
  private val codec = NotesLcpCodec()
  @Volatile private var foreground = false
  @Volatile private var sessionAllowed = true
  @Volatile private var epoch = 0L
  private var socket: WebSocket? = null
  private var owner: NotesSessionSnapshot? = null
  private var resumeOwner: NotesSessionSnapshot? = null
  private var sessionId: String? = null
  private var resumeToken: String? = null
  private var subscriptionId: String? = null
  private var highestSeq = 0L
  private var authenticateId: String? = null
  private var subscribeId: String? = null
  private var reconnectJob: Job? = null
  private var handshakeJob: Job? = null
  private var invalidationJob: Job? = null
  private var attempts = 0
  private val mutableState = MutableStateFlow(NotesRealtimeState.Offline)
  val state: StateFlow<NotesRealtimeState> = mutableState

  init {
    scope.launch(Dispatchers.IO) {
      for (signal in signals) {
        try {
          when (signal) {
            Signal.Reconcile -> reconcile()
            is Signal.Open -> if (valid(signal.epoch)) hello(signal.socket) else signal.socket.cancel()
            is Signal.Frame -> if (valid(signal.epoch)) handle(signal.text)
            is Signal.Failed -> if (signal.epoch == epoch) failed()
          }
        } catch (cancelled: CancellationException) { throw cancelled
        } catch (_: Exception) { failed() }
      }
    }
  }

  fun setForeground(active: Boolean) { foreground = active; changed() }
  fun setSessionAllowed(active: Boolean) { sessionAllowed = active; changed() }
  fun changed() { if (signals.trySend(Signal.Reconcile).isFailure) scope.launch { signals.send(Signal.Reconcile) } }

  private fun valid(connectionEpoch: Long): Boolean = connectionEpoch == epoch && foreground && sessionAllowed && isOnline() && owner?.let(isCurrent) == true

  private fun reconcile() {
    val session = if (foreground && sessionAllowed && isOnline()) snapshot() else null
    if (!sessionAllowed) clearResume()
    if (session == null || url.isBlank()) { disconnect(); return }
    if (owner != session) {
      disconnect()
      if (resumeOwner != session) clearResume()
      owner = session
    }
    if (socket != null || reconnectJob?.isActive == true) return
    connect()
  }

  private fun connect() {
    val session = owner ?: return
    if (!foreground || !sessionAllowed || !isOnline() || !isCurrent(session)) return
    mutableState.value = NotesRealtimeState.Connecting
    val connectionEpoch = ++epoch
    socket = http.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
      private val overflow = java.util.concurrent.atomic.AtomicBoolean(false)
      private fun offer(signal: Signal, webSocket: WebSocket) {
        if (connectionEpoch != epoch) return
        if (signals.trySend(signal).isFailure && overflow.compareAndSet(false, true)) {
          // Dropped invalidations must recover via a new subscription plus HTTP pull.
          // The actor still sees the fenced failure even under overload.
          webSocket.cancel()
          scope.launch { signals.send(Signal.Failed(connectionEpoch)) }
        }
      }
      override fun onOpen(webSocket: WebSocket, response: Response) { offer(Signal.Open(connectionEpoch, webSocket), webSocket) }
      override fun onMessage(webSocket: WebSocket, text: String) {
        if (text.length > 65_536) { webSocket.cancel(); offer(Signal.Failed(connectionEpoch), webSocket) }
        else offer(Signal.Frame(connectionEpoch, text), webSocket)
      }
      override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { offer(Signal.Failed(connectionEpoch), webSocket) }
      override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { offer(Signal.Failed(connectionEpoch), webSocket) }
    })
    handshakeJob?.cancel()
    handshakeJob = scope.launch { delay(20_000); signals.send(Signal.Failed(connectionEpoch)) }
  }

  private fun hello(webSocket: WebSocket) {
    val session = owner ?: return
    webSocket.send(codec.encode("hello", body = buildJsonObject {
      put("client", buildJsonObject { put("platform", JsonPrimitive("android")); put("app", JsonPrimitive("notes")) })
      put("capabilities", buildJsonObject { put("required", JsonArray(emptyList())); put("optional", JsonArray(emptyList())) })
      if (resumeOwner == session && sessionId != null && resumeToken != null) {
        put("resume_session_id", JsonPrimitive(sessionId!!)); put("resume_token", JsonPrimitive(resumeToken!!)); put("last_acked_seq", JsonPrimitive(highestSeq))
      }
    }))
  }

  private fun request(method: String, payload: JsonObject = buildJsonObject {}): String {
    val id = UUID.randomUUID().toString()
    check(socket?.send(codec.encode("request", id, buildJsonObject { put("method", JsonPrimitive(method)); put("payload", payload) })) == true)
    return id
  }

  private fun handle(raw: String) {
    val frame = codec.decode(raw)
    when (frame["type"]?.jsonPrimitive?.content) {
      "hello_ack" -> {
        if (frame["resume"]?.jsonPrimitive?.content == "resumed") {
          sessionId = frame["session_id"]?.jsonPrimitive?.content ?: sessionId
          resumeToken = frame["resume_token"]?.jsonPrimitive?.content ?: resumeToken
          if (subscriptionId != null) connected()
          else subscribeId = request("realtime.subscribe", buildJsonObject { put("topic", JsonPrimitive("notes.events")); put("filter", buildJsonObject {}) })
        } else {
          clearResume()
          val session = owner ?: return
          authenticateId = request("session.authenticate", buildJsonObject { put("access_token", JsonPrimitive(session.accessToken)) })
        }
      }
      "response" -> {
        val payload = frame["payload"]?.jsonObject ?: return
        when (frame["id"]?.jsonPrimitive?.content) {
          authenticateId -> {
            if (authenticateId == null) return
            authenticateId = null
            sessionId = payload["session_id"]?.jsonPrimitive?.content
            resumeToken = payload["resume_token"]?.jsonPrimitive?.content
            resumeOwner = owner
            subscribeId = request("realtime.subscribe", buildJsonObject { put("topic", JsonPrimitive("notes.events")); put("filter", buildJsonObject {}) })
          }
          subscribeId -> {
            if (subscribeId == null) return
            subscribeId = null
            subscriptionId = payload["subscription_id"]?.jsonPrimitive?.content ?: error("Missing subscription")
            connected()
          }
        }
      }
      "event" -> {
        val session = owner ?: return
        val subscription = subscriptionId ?: return
        if (!NotesInvalidationPolicy.accepts(frame, session.ownerId, subscription) || !isCurrent(session)) return
        val seq = frame["seq"]!!.jsonPrimitive.long
        if (seq > highestSeq) {
          highestSeq = seq
          schedulePull()
        }
        socket?.send(codec.encode("ack", body = buildJsonObject { put("seq", JsonPrimitive(highestSeq)) }))
      }
      "error" -> {
        val code = frame["error"]?.jsonObject?.get("code")?.jsonPrimitive?.content
        if (code in setOf("LCP_AUTH_INVALID", "LCP_STALE_OWNER", "LCP_SESSION_EXPIRED", "LCP_SESSION_REVOKED")) clearResume()
        failed()
      }
    }
  }

  private fun connected() {
    handshakeJob?.cancel(); handshakeJob = null; attempts = 0
    mutableState.value = NotesRealtimeState.Connected
    schedulePull() // repairs missed/offline/replay-expired invalidations using HTTP cursor
  }

  private fun schedulePull() {
    if (invalidationJob?.isActive == true) return
    val connectionEpoch = epoch
    val session = owner ?: return
    invalidationJob = scope.launch {
      delay(180)
      if (valid(connectionEpoch) && isCurrent(session)) requestSync()
    }
  }

  private fun failed() {
    disconnect(keepOwner = true)
    val session = owner ?: return
    if (!foreground || !sessionAllowed || !isOnline() || !isCurrent(session)) return
    val retry = minOf(30_000L, 1_000L shl attempts.coerceAtMost(5)); attempts++
    val connectionEpoch = epoch
    reconnectJob = scope.launch {
      delay(retry)
      if (connectionEpoch == epoch) { reconnectJob = null; changed() }
    }
  }

  private fun disconnect(keepOwner: Boolean = false) {
    ++epoch; socket?.cancel(); socket = null
    handshakeJob?.cancel(); handshakeJob = null
    reconnectJob?.cancel(); reconnectJob = null
    invalidationJob?.cancel(); invalidationJob = null
    authenticateId = null; subscribeId = null
    mutableState.value = NotesRealtimeState.Offline
    if (!keepOwner) owner = null
  }
  private fun clearResume() { resumeOwner = null; sessionId = null; resumeToken = null; subscriptionId = null; highestSeq = 0 }
}
