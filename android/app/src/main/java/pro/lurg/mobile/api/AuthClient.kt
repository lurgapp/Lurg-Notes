package pro.lurg.notes.api

import android.content.Context
import com.tencent.mmkv.MMKV
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import pro.lurg.notes.security.SecureTokenStore
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

class AuthClient(context: Context) {
  private val appContext = context.applicationContext
  private val tokens = SecureTokenStore(context)
  private val identity = appContext.getSharedPreferences("lurg_notes_identity", Context.MODE_PRIVATE)
  private val client = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(20, TimeUnit.SECONDS).build()

  init { identity.getString("owner_id", null)?.takeIf(String::isNotBlank)?.let(tokens::bindLegacyOwnerIfMissing) }

  fun hasToken(): Boolean = tokens.getToken() != null
  fun token(): String? = tokens.getToken()
  fun saveToken(token: String) = tokens.setToken(token)

  fun clear() {
    tokens.clear()
    identity.edit().clear().apply()
    runCatching {
      MMKV.initialize(appContext)
      MMKV.mmkvWithID("lurg-session").clearAll()
    }
  }

  suspend fun me(): AuthUser? = withContext(Dispatchers.IO) {
    migrateLegacyMmkvToken()
    val token = tokens.getToken() ?: return@withContext null
    when (val result = getMe(token)) {
      is MeResult.User -> acceptCurrentUser(token, result.user)
      MeResult.Unauthorized -> refresh(token)?.let { refreshed ->
        (getMe(refreshed) as? MeResult.User)?.user?.let { acceptCurrentUser(refreshed, it) }
      }
    }
  }

  /**
   * Offline mode may reveal only data that is already isolated under this
   * device's prior authenticated owner.  This is an identifier, never a token.
   */
  fun cachedUser(): AuthUser? = tokens.getToken()?.let {
    tokens.ownerId()?.takeIf(String::isNotBlank)?.let { ownerId ->
      AuthUser(ownerId, "", "")
    }
  }

  suspend fun logout() = withContext(Dispatchers.IO) {
    val refreshToken = refreshLock.withLock { tokens.getRefreshToken().also { clear() } }
    if (refreshToken != null) runCatching {
      val request = Request.Builder().url("$AUTH_API/logout").addHeader(APP_HEADER, APP_NAME)
        .post(JSONObject().put("refresh_token", refreshToken).toString().toRequestBody(JSON)).build()
      client.newBuilder().callTimeout(3, TimeUnit.SECONDS).build().newCall(request).execute().close()
    }
  }

  suspend fun login(email: String, password: String): AuthUser = withContext(Dispatchers.IO) {
    refreshLock.withLock { sessionRequest("/email/login", JSONObject().put("email", email.trim()).put("password", password)).user }
  }

  suspend fun requestRegistration(name: String, email: String, password: String) = withContext(Dispatchers.IO) {
    request("/email/register/request-code", JSONObject().put("name", name.trim()).put("email", email.trim()).put("password", password), false).use { response ->
      if (!response.isSuccessful) throw IllegalStateException(errorMessage(response.body?.string().orEmpty(), "Не удалось начать регистрацию"))
    }
  }

  suspend fun confirmRegistration(email: String, code: String): AuthUser = withContext(Dispatchers.IO) {
    refreshLock.withLock { sessionRequest("/email/register/verify", JSONObject().put("email", email.trim()).put("code", code.trim())).user }
  }

  private suspend fun refresh(failedToken: String): String? = refreshLock.withLock {
    tokens.getToken()?.takeIf { it != failedToken }?.let { return@withLock it }
    val refreshToken = tokens.getRefreshToken() ?: return@withLock null
    try { sessionRequest("/refresh", JSONObject().put("refresh_token", refreshToken)).accessToken }
    catch (error: NotesAuthHttpException) { if (error.code in listOf(401, 403)) { clear(); null } else throw error }
  }

  private suspend fun acceptCurrentUser(expectedToken: String, user: AuthUser): AuthUser? = refreshLock.withLock {
    if (tokens.getToken() == expectedToken) user.also(::cacheUser) else cachedUser()
  }

  private fun sessionRequest(path: String, payload: JSONObject): AuthSession {
    return request(path, payload, false).use { response ->
      val raw = response.body?.string().orEmpty()
      if (!response.isSuccessful) throw NotesAuthHttpException(response.code, errorMessage(raw, "Не удалось войти в Lurg ID"))
      val json = JSONObject(raw)
      val accessToken = json.optString("access_token")
      val refreshToken = json.optString("refresh_token")
      val user = json.optJSONObject("user")
      if (accessToken.isBlank() || refreshToken.isBlank() || user == null) throw IllegalStateException("Lurg ID не вернул сессию")
      AuthSession(accessToken, refreshToken, parseUser(user)).also {
        tokens.setSession(it.accessToken, it.refreshToken, it.user.id)
        cacheUser(it.user)
      }
    }
  }

  private fun getMe(token: String): MeResult {
    val request = Request.Builder().url("$AUTH_API/me").addHeader("Authorization", "Bearer $token").addHeader(APP_HEADER, APP_NAME).get().build()
    client.newCall(request).execute().use { response ->
      if (response.code == 401) return MeResult.Unauthorized
      if (!response.isSuccessful) throw IllegalStateException("Не удалось проверить сессию Lurg ID")
      return MeResult.User(parseUser(JSONObject(response.body?.string().orEmpty())))
    }
  }

  private fun request(path: String, payload: JSONObject, authenticated: Boolean): okhttp3.Response {
    val builder = Request.Builder().url("$AUTH_API$path").addHeader(APP_HEADER, APP_NAME)
      .post(payload.toString().toRequestBody(JSON))
    if (authenticated) tokens.getToken()?.let { builder.addHeader("Authorization", "Bearer $it") }
    return client.newCall(builder.build()).execute()
  }

  private fun parseUser(json: JSONObject) = AuthUser(json.optString("id"), json.optString("email"), json.optString("name"))

  private fun cacheUser(user: AuthUser) {
    if (user.id.isNotBlank()) {
      tokens.bindOwner(user.id)
      identity.edit().putString("owner_id", user.id).commit()
    }
  }

  private fun errorMessage(raw: String, fallback: String): String = runCatching {
    val detail = JSONObject(raw).opt("detail")
    when (detail) {
      is JSONObject -> detail.optString("message")
      is String -> detail
      else -> ""
    }
  }.getOrDefault("").ifBlank { fallback }

  private fun migrateLegacyMmkvToken() {
    if (tokens.getToken() != null) return
    runCatching {
      MMKV.initialize(appContext)
      MMKV.mmkvWithID("lurg-session")?.decodeString("token")?.takeIf { it.isNotBlank() }?.let(tokens::setToken)
    }
  }

  private sealed interface MeResult {
    data class User(val user: AuthUser) : MeResult
    data object Unauthorized : MeResult
  }

  private data class AuthSession(val accessToken: String, val refreshToken: String, val user: AuthUser)

  companion object {
    private val refreshLock = Mutex()
    val AUTH_API = pro.lurg.notes.BuildConfig.AUTH_API_URL
    val API_URL = pro.lurg.notes.BuildConfig.API_BASE_URL
    const val APP_HEADER = "X-Lurg-App-Name"
    const val APP_NAME = "lurg_notes"
    private val JSON = "application/json; charset=utf-8".toMediaType()
  }
}

data class AuthUser(val id: String, val email: String, val name: String)

class NotesAuthHttpException(val code: Int, message: String) : IllegalStateException(message)
