package com.agentcommandcenter.chatgpt

import org.json.JSONArray
import org.json.JSONObject
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.io.Reader
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

internal enum class ChatStage { INPUT, ACCOUNT, REQUEST, RESPONSE, STREAM }
internal class ChatFailure(
  val stage: ChatStage,
  val reason: String,
  val shape: String? = null,
  val providerCode: String? = null,
  val param: String? = null,
  val requestId: String? = null,
) : Exception() {
  val diagnostic: String get() = listOfNotNull("ACC-CHAT", stage.name, reason, shape, providerCode?.uppercase(java.util.Locale.ROOT), param?.uppercase(java.util.Locale.ROOT)).joinToString("-")
  fun userMessage(): String {
    val action = when (providerCode) {
      "subscription_sharing_usage_limit_exceeded", "rate_limit_exceeded" -> "ChatGPT refused this request because of a usage limit. Check Manage usage before trying again."
      "subscription_sharing_user_not_eligible" -> "ChatGPT plan access is unavailable for this account or workspace. Check app access in ChatGPT settings. Repeating sign-in may not help."
      "subscription_sharing_unsupported_capability" -> "ChatGPT rejected part of ACC's request. This needs an app fix."
      "chatpass_v2_scope_not_authorized", "chatpass_v2_invalid_authorization_context", "subscription_sharing_route_not_supported" -> "ChatGPT rejected this connection's permission or route. This needs an integration check."
      "subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable" -> "ChatGPT could not check account availability. Keep your sign-in and try later."
      else -> when (reason) {
        "HTTP401" -> "ChatGPT did not accept this account credential. Check Connections before trying again."
        "HTTP403" -> "ChatGPT refused this request at its permission or policy check. The diagnostic identifies the returned error category."
        "HTTP429" -> "ChatGPT refused this request because of a limit. Check Manage usage."
        "HTTP503" -> "ChatGPT routing is unavailable. Keep your sign-in and try later."
        "TIMEOUT" -> "The reply timed out. Its outcome is unknown."
        "CANCELLED" -> "The reply was stopped. Its outcome may be incomplete."
        "INTERRUPTED" -> "The reply stream ended before completion. Its outcome is unknown."
        "INCOMPLETE" -> "ChatGPT returned an incomplete response."
        "CONTENT_TYPE" -> "ChatGPT returned a reply format ACC could not use. Keep your sign-in and report this diagnostic."
        else -> "ChatGPT did not complete the reply."
      }
    }
    return "$action Diagnostic: $diagnostic." + (requestId?.let { " Request: $it." } ?: "") + " ACC has not resent the message or switched billing."
  }
  companion object {
    fun wrap(stage: ChatStage, error: Exception, cancelled: Boolean = false): ChatFailure = if (cancelled) ChatFailure(stage, "CANCELLED") else if (error is ChatFailure) error else ChatFailure(stage, when (error) {
      is SignInHttpFailure -> "HTTP${error.status}"
      is SocketTimeoutException -> "TIMEOUT"
      is UnknownHostException -> "DNS"
      is SSLException -> "TLS"
      is IOException -> "NETWORK"
      else -> "VALIDATION"
    })
  }
}

internal object ChatProtocol {
  fun streamRequest(body: String, token: String): Request = Request.Builder()
    .url(OAuthCore.RESOURCE + "/responses")
    .header("Accept", "text/event-stream")
    .header("Authorization", "Bearer $token")
    .post(body.toRequestBody("application/json".toMediaType()))
    .build()
  private val codes = setOf("subscription_sharing_user_not_eligible", "subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable", "subscription_sharing_unsupported_capability", "subscription_sharing_route_not_supported", "subscription_sharing_invalid_user", "subscription_sharing_user_unavailable", "chatpass_v2_scope_not_authorized", "chatpass_v2_invalid_authorization_context", "rate_limit_exceeded", "model_not_found", "invalid_api_key", "invalid_request_error", "server_error", "content_filter")
  private val params = setOf("model", "input", "store", "stream", "instructions", "tools", "service_tier")
  fun requestBody(model: String, rawMessages: String): String {
    require(model.length in 1..160)
    val messages = JSONArray(rawMessages); require(messages.length() in 1..39 && messages.length() % 2 == 1)
    var length = 0
    val input = JSONArray()
    for (i in 0 until messages.length()) {
      val m = messages.getJSONObject(i); val role = m.getString("role"); val text = m.getString("text")
      require(role == if (i % 2 == 0) "user" else "assistant"); require(text.isNotBlank() && text.length <= 100000)
      length += text.length; input.put(JSONObject().put("role", role).put("content", text))
    }
    require(length <= 50000)
    return JSONObject().put("model", model).put("input", input).put("store", false).put("stream", true).toString()
  }
  fun safeRequestId(raw: String?): String? = raw?.takeIf { it.matches(Regex("req_[A-Za-z0-9_-]{1,100}")) }
  private fun providerFailure(stage: ChatStage, reason: String, payload: JSONObject?, requestId: String?): ChatFailure {
    val error = payload?.optJSONObject("error")
    val shape = when { error != null -> "ERROR"; payload?.has("detail") == true -> "DETAIL"; else -> "OTHER" }
    return ChatFailure(stage, reason, shape, error?.optString("code")?.takeIf { it in codes }, error?.optString("param")?.takeIf { it in params }, safeRequestId(requestId))
  }
  fun read(status: Int, contentType: String?, reader: Reader, requestId: String?, checkActive: () -> Unit = {}): String {
    if (status != 200) {
      val raw = StringBuilder(); val buffer = CharArray(1024)
      while (raw.length < 4096) { val n = reader.read(buffer, 0, minOf(buffer.size, 4096 - raw.length)); if (n < 0) break; raw.append(buffer, 0, n) }
      val payload = try { JSONObject(raw.toString()) } catch (_: Exception) { null }
      throw providerFailure(ChatStage.RESPONSE, "HTTP$status", payload, requestId)
    }
    val mediaType = contentType?.substringBefore(';')?.trim()?.lowercase(java.util.Locale.ROOT)?.takeIf { it.isNotEmpty() }
    // The official direct route can omit Content-Type on a valid SSE response.
    // Missing metadata is not success: the parser still requires response.completed.
    // An explicit conflicting type is rejected without interpreting its body as a reply.
    if (mediaType != null && mediaType != "text/event-stream") {
      val shape = when (mediaType) { "application/json" -> "JSON"; "text/html" -> "HTML"; "text/plain" -> "TEXT"; else -> "OTHER" }
      throw ChatFailure(ChatStage.RESPONSE, "CONTENT_TYPE", shape, requestId = safeRequestId(requestId))
    }
    try { return OAuthCore.readStream(reader, checkActive) }
    catch (e: Exception) {
      val failure = ChatFailure.wrap(ChatStage.STREAM, e)
      throw ChatFailure(failure.stage, failure.reason, failure.shape, failure.providerCode, failure.param, safeRequestId(requestId))
    }
  }
  fun failedEvent(event: JSONObject): ChatFailure {
    val type = event.optString("type")
    val payload = if (type == "error") {
      if (event.optJSONObject("error") != null) event else JSONObject().put("error", event)
    } else event.optJSONObject("response")
    return providerFailure(ChatStage.STREAM, if (type == "response.incomplete") "INCOMPLETE" else "FAILED", payload, null)
  }
}
