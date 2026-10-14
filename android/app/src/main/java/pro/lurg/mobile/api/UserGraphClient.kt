package pro.lurg.notes.api

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class UserGraphNode(val id: String, val label: String, val type: String)
data class UserGraphEdge(val id: String, val sourceId: String, val targetId: String, val relation: String)
data class UserGraph(val nodes: List<UserGraphNode>, val edges: List<UserGraphEdge>)

class UserGraphClient(context: Context) {
  private val auth = AuthClient(context)
  private val client = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(18, TimeUnit.SECONDS).build()

  suspend fun load(userId: String): UserGraph = withContext(Dispatchers.IO) {
    val token = auth.token() ?: throw IllegalStateException("Требуется вход в Lurg ID")
    val request = Request.Builder().url("${AuthClient.API_URL}/graph/$userId")
      .header("Authorization", "Bearer $token").get().build()
    client.newCall(request).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException(if (response.code == 401) "Сессия Lurg ID истекла" else "Не удалось загрузить Graph")
      val json = JSONObject(response.body?.string().orEmpty())
      val nodes = json.optJSONArray("nodes")
      val edges = json.optJSONArray("edges")
      UserGraph(
        nodes = List(nodes?.length() ?: 0) { index ->
          val node = nodes!!.getJSONObject(index)
          UserGraphNode(node.optString("id"), node.optString("label"), node.optString("type"))
        },
        edges = List(edges?.length() ?: 0) { index ->
          val edge = edges!!.getJSONObject(index)
          UserGraphEdge(edge.optString("id"), edge.optString("source_id"), edge.optString("target_id"), edge.optString("relation"))
        }
      )
    }
  }
}
