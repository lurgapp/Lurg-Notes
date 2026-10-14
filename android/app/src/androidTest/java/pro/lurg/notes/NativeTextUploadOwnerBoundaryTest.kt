package pro.lurg.notes

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import pro.lurg.notes.api.NotesAiClient
import pro.lurg.notes.api.NotesGraphClient
import pro.lurg.notes.security.SecureTokenStore
import java.util.UUID

/** Every request is answered by an application interceptor; no network is used. */
@RunWith(AndroidJUnit4::class)
class NativeTextUploadOwnerBoundaryTest {
  private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

  @Test fun previousOwnerCannotStartTextUploads(): Unit = runBlocking {
    val store = SecureTokenStore(context)
    val a = UUID.randomUUID().toString(); val b = UUID.randomUUID().toString()
    store.setSession("owner-b-token", "owner-b-refresh", b)
    var requests = 0
    val client = OkHttpClient.Builder().addInterceptor {
      requests += 1
      throw AssertionError("An old owner's request reached HTTP")
    }.build()
    try {
      try {
        NotesAiClient(context, client).runAction(a, UUID.randomUUID().toString(), "summarize", "Synthetic owner A text", "", "", 1)
        fail("Old owner AI upload accepted")
      } catch (_: IllegalStateException) { }
      try {
        NotesGraphClient(context, client).extract(a, UUID.randomUUID().toString(), "Synthetic title", "Synthetic owner A text", 1)
        fail("Old owner Graph upload accepted")
      } catch (_: IllegalStateException) { }
      assertEquals(0, requests)
      assertEquals("owner-b-token", store.getTokenForOwner(b))
    } finally { store.clear() }
  }

  @Test fun aiResponseAfterAccountSwitchIsDiscarded(): Unit = runBlocking {
    val store = SecureTokenStore(context)
    val a = UUID.randomUUID().toString(); val b = UUID.randomUUID().toString()
    store.setSession("owner-a-token", "owner-a-refresh", a)
    var requests = 0
    val client = OkHttpClient.Builder().addInterceptor { chain ->
      requests += 1
      assertEquals("Bearer owner-a-token", chain.request().header("Authorization"))
      // Switch after HTTP has started, before the successful response returns.
      store.setSession("owner-b-token", "owner-b-refresh", b)
      Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
        .body("{\"action\":\"summarize\",\"result\":{\"content\":\"Synthetic response\"}}".toResponseBody("application/json".toMediaType())).build()
    }.build()
    try {
      try {
        NotesAiClient(context, client).runAction(a, UUID.randomUUID().toString(), "summarize", "Synthetic owner A text", "", "", 1)
        fail("Previous session's AI response accepted")
      } catch (_: IllegalStateException) { }
      assertEquals(1, requests)
      assertEquals("owner-b-token", store.getTokenForOwner(b))
    } finally { store.clear() }
  }

  @Test fun graphResponseAfterAccountSwitchIsDiscarded(): Unit = runBlocking {
    val store = SecureTokenStore(context)
    val a = UUID.randomUUID().toString(); val b = UUID.randomUUID().toString()
    store.setSession("owner-a-token", "owner-a-refresh", a)
    var requests = 0
    val client = OkHttpClient.Builder().addInterceptor { chain ->
      requests += 1
      assertEquals("Bearer owner-a-token", chain.request().header("Authorization"))
      val code: Int
      val body: String
      if (chain.request().url.encodedPath.endsWith("/graph-candidates")) {
        store.setSession("owner-b-token", "owner-b-refresh", b)
        code = 200; body = "{\"candidates\":[]}"
      } else {
        code = 201; body = "{}"
      }
      Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(code).message("OK")
        .body(body.toResponseBody("application/json".toMediaType())).build()
    }.build()
    try {
      try {
        NotesGraphClient(context, client).extract(a, UUID.randomUUID().toString(), "Synthetic title", "Synthetic owner A text", 1)
        fail("Previous session's Graph response accepted")
      } catch (_: IllegalStateException) { }
      assertEquals(2, requests)
      assertEquals("owner-b-token", store.getTokenForOwner(b))
    } finally { store.clear() }
  }
}
