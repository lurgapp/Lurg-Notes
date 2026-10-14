package pro.lurg.notes.sync

import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.serialization.json.*
import okhttp3.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import pro.lurg.notes.lcp.*
import pro.lurg.notes.security.NotesSessionSnapshot

class NotesRealtimeSyncTest {
  private val codec = NotesLcpCodec()
  private fun event(owner: String, subscription: String = "sub-notes", seq: Long = 1, extra: Boolean = false): String = codec.encode("event", body = buildJsonObject {
    put("subscription_id", JsonPrimitive(subscription)); put("seq", JsonPrimitive(seq)); put("name", JsonPrimitive("notes.updated"))
    put("payload", buildJsonObject { put("owner_id", JsonPrimitive(owner)); put("name", JsonPrimitive("notes.updated")); if (extra) put("content", JsonPrimitive("private")) })
  })
  @Test fun `private invalidations reject wrong owner subscription payload and version`() {
    assertTrue(NotesInvalidationPolicy.accepts(codec.decode(event("a")), "a", "sub-notes"))
    assertFalse(NotesInvalidationPolicy.accepts(codec.decode(event("b")), "a", "sub-notes"))
    assertFalse(NotesInvalidationPolicy.accepts(codec.decode(event("a", "other")), "a", "sub-notes"))
    assertFalse(NotesInvalidationPolicy.accepts(codec.decode(event("a", extra = true)), "a", "sub-notes"))
    assertFalse(NotesInvalidationPolicy.accepts(codec.decode(event("a", seq = 0)), "a", "sub-notes"))
    try { codec.decode("{\"v\":2,\"type\":\"event\"}"); fail("Unsupported protocol accepted") } catch (_: IllegalArgumentException) { }
  }

  @Test fun `real websocket subscribes privately coalesces bursts and fences logout and owner switch`() = runBlocking {
    val server = MockWebServer()
    val sockets = Channel<WebSocket>(Channel.UNLIMITED)
    val tokens = Channel<String>(Channel.UNLIMITED)
    val a = NotesSessionSnapshot("owner-a", "synthetic-token-a")
    val b = NotesSessionSnapshot("owner-b", "synthetic-token-b")
    val current = AtomicReference<NotesSessionSnapshot?>(a)
    fun response(id: String, payload: JsonObject) = codec.encode("response", id, buildJsonObject { put("payload", payload) })
    repeat(3) {
      server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) { sockets.trySend(webSocket) }
        override fun onMessage(webSocket: WebSocket, text: String) {
          val frame = codec.decode(text)
          when (frame["type"]?.jsonPrimitive?.content) {
            "hello" -> webSocket.send(codec.encode("hello_ack", body = buildJsonObject { put("resume", JsonPrimitive("new")) }))
            "request" -> when (frame["method"]?.jsonPrimitive?.content) {
              "session.authenticate" -> {
                tokens.trySend(frame["payload"]!!.jsonObject["access_token"]!!.jsonPrimitive.content)
                webSocket.send(response(frame["id"]!!.jsonPrimitive.content, buildJsonObject { put("session_id", JsonPrimitive("sid")); put("resume_token", JsonPrimitive("resume")) }))
              }
              "realtime.subscribe" -> {
                val payload = frame["payload"]!!.jsonObject
                assertEquals("notes.events", payload["topic"]!!.jsonPrimitive.content)
                assertTrue(payload["filter"]!!.jsonObject.isEmpty())
                webSocket.send(response(frame["id"]!!.jsonPrimitive.content, buildJsonObject { put("subscription_id", JsonPrimitive("sub-notes")) }))
              }
            }
          }
        }
      }))
    }
    server.start()
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val pulls = Channel<Unit>(Channel.UNLIMITED)
    val client = NotesRealtimeSync(scope, current::get, { current.get() == it }, { true }, { pulls.trySend(Unit) }, server.url("/connect").toString().replace("http:", "ws:"))
    try {
      client.setForeground(true)
      val socketA = withTimeout(4000) { sockets.receive() }
      assertEquals(a.accessToken, withTimeout(4000) { tokens.receive() })
      withTimeout(4000) { pulls.receive() } // subscribed catch-up pull
      repeat(20) { socketA.send(event(a.ownerId, seq = it + 1L)) }
      withTimeout(4000) { pulls.receive() }
      delay(250); assertTrue("Burst created extra sync work", pulls.tryReceive().isFailure)
      socketA.send(event(b.ownerId, seq = 21)); socketA.send(event(a.ownerId, extra = true, seq = 22))
      delay(250); assertTrue(pulls.tryReceive().isFailure)
      current.set(b); client.changed()
      withTimeout(4000) { sockets.receive() }
      assertEquals(b.accessToken, withTimeout(4000) { tokens.receive() })
      withTimeout(4000) { pulls.receive() }
      socketA.send(event(a.ownerId, seq = 23))
      delay(250); assertTrue("Old connection crossed account boundary", pulls.tryReceive().isFailure)
      client.setSessionAllowed(false)
      withTimeout(4000) { while (client.state.value != NotesRealtimeState.Offline) delay(20) }
      delay(250); assertTrue(pulls.tryReceive().isFailure)
      client.setForeground(false)
    } finally { scope.cancel(); server.shutdown() }
  }

  @Test fun `resume keeps acknowledgements only for the exact token and logout discards it`() = runBlocking {
    val server = MockWebServer()
    val sockets = Channel<WebSocket>(Channel.UNLIMITED)
    val hellos = Channel<JsonObject>(Channel.UNLIMITED)
    val tokens = Channel<String>(Channel.UNLIMITED)
    val current = AtomicReference(NotesSessionSnapshot("owner-a", "synthetic-token-a"))
    fun response(id: String, payload: JsonObject) = codec.encode("response", id, buildJsonObject { put("payload", payload) })
    repeat(4) { connection ->
      server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) { sockets.trySend(webSocket) }
        override fun onMessage(webSocket: WebSocket, text: String) {
          val frame = codec.decode(text)
          when (frame["type"]?.jsonPrimitive?.content) {
            "hello" -> {
              hellos.trySend(frame)
              webSocket.send(codec.encode("hello_ack", body = buildJsonObject {
                put("resume", JsonPrimitive(if (connection == 1) "resumed" else "new"))
                if (connection == 1) { put("session_id", JsonPrimitive("sid-0")); put("resume_token", JsonPrimitive("rotated-resume")) }
              }))
            }
            "request" -> when (frame["method"]?.jsonPrimitive?.content) {
              "session.authenticate" -> {
                tokens.trySend(frame["payload"]!!.jsonObject["access_token"]!!.jsonPrimitive.content)
                webSocket.send(response(frame["id"]!!.jsonPrimitive.content, buildJsonObject {
                  put("session_id", JsonPrimitive("sid-$connection")); put("resume_token", JsonPrimitive("resume-$connection"))
                }))
              }
              "realtime.subscribe" -> webSocket.send(response(frame["id"]!!.jsonPrimitive.content, buildJsonObject { put("subscription_id", JsonPrimitive("sub-notes")) }))
            }
          }
        }
      }))
    }
    server.start()
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val pulls = Channel<Unit>(Channel.UNLIMITED)
    val client = NotesRealtimeSync(scope, current::get, { current.get() == it }, { true }, { pulls.trySend(Unit) }, server.url("/connect").toString().replace("http:", "ws:"))
    try {
      client.setForeground(true)
      val firstSocket = withTimeout(4000) { sockets.receive() }
      assertFalse(withTimeout(4000) { hellos.receive() }.containsKey("resume_token"))
      assertEquals("synthetic-token-a", withTimeout(4000) { tokens.receive() })
      withTimeout(4000) { pulls.receive() }
      firstSocket.send(event("owner-a", seq = 7))
      withTimeout(4000) { pulls.receive() }
      client.setForeground(false)
      withTimeout(4000) { while (client.state.value != NotesRealtimeState.Offline) delay(20) }
      client.setForeground(true)
      val resumedSocket = withTimeout(4000) { sockets.receive() }
      val resumedHello = withTimeout(4000) { hellos.receive() }
      assertEquals("resume-0", resumedHello["resume_token"]!!.jsonPrimitive.content)
      assertEquals("sid-0", resumedHello["resume_session_id"]!!.jsonPrimitive.content)
      assertEquals(7L, resumedHello["last_acked_seq"]!!.jsonPrimitive.long)
      withTimeout(4000) { pulls.receive() } // authoritative catch-up always runs after resume
      resumedSocket.send(event("owner-a", seq = 7))
      delay(250)
      assertTrue("Acknowledged replay created duplicate work", pulls.tryReceive().isFailure)
      assertTrue("Resume unnecessarily reauthenticated", tokens.tryReceive().isFailure)

      current.set(current.get().copy(accessToken = "synthetic-token-rotated")); client.changed()
      withTimeout(4000) { sockets.receive() }
      assertFalse("Access-token rotation reused a prior session", withTimeout(4000) { hellos.receive() }.containsKey("resume_token"))
      assertEquals("synthetic-token-rotated", withTimeout(4000) { tokens.receive() })
      withTimeout(4000) { pulls.receive() }
      client.setSessionAllowed(false)
      withTimeout(4000) { while (client.state.value != NotesRealtimeState.Offline) delay(20) }
      client.setSessionAllowed(true)
      withTimeout(4000) { sockets.receive() }
      assertFalse("Logout retained resume credentials", withTimeout(4000) { hellos.receive() }.containsKey("resume_token"))
      assertEquals("synthetic-token-rotated", withTimeout(4000) { tokens.receive() })
      withTimeout(4000) { pulls.receive() }
    } finally { scope.cancel(); server.shutdown() }
  }
}
