package pro.lurg.notes.lcp

import kotlinx.serialization.json.*

/** The same LCP v1 envelope used by the main native client. */
internal class NotesLcpCodec {
  private val json = Json { ignoreUnknownKeys = true }
  fun encode(type: String, id: String? = null, body: JsonObject = buildJsonObject {}): String = json.encodeToString(JsonObject.serializer(), buildJsonObject {
    put("v", JsonPrimitive(1)); put("type", JsonPrimitive(type)); if (id != null) put("id", JsonPrimitive(id)); body.forEach { (key, value) -> put(key, value) }
  })
  fun decode(raw: String): JsonObject = json.parseToJsonElement(raw).jsonObject.also { require(it["v"]?.jsonPrimitive?.intOrNull == 1) }
}

/** Strictly metadata-only signals. Ciphertext and note text travel through the existing HTTP sync. */
internal object NotesInvalidationPolicy {
  fun accepts(frame: JsonObject, ownerId: String, subscriptionId: String): Boolean {
    if (frame["type"]?.jsonPrimitive?.content != "event" || frame["subscription_id"]?.jsonPrimitive?.content != subscriptionId) return false
    if (frame["name"]?.jsonPrimitive?.content !in setOf("notes.updated", "notes.deleted")) return false
    val payload = frame["payload"]?.jsonObject ?: return false
    if (payload["owner_id"]?.jsonPrimitive?.content != ownerId) return false
    if (payload["name"]?.jsonPrimitive?.content != frame["name"]?.jsonPrimitive?.content) return false
    if (payload.keys.any { it !in setOf("owner_id", "name") }) return false
    return frame["seq"]?.jsonPrimitive?.longOrNull?.let { it > 0 } == true
  }
}
