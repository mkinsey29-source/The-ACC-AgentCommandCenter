package com.agentcommandcenter.chatgpt

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.SocketTimeoutException

class ChatProtocolTest {
  private val secret = "private-prompt-or-token-must-not-be-shown"
  private fun failure(block: () -> Unit): ChatFailure {
    try { block(); fail("Expected failure") } catch (e: ChatFailure) { return e }
    throw AssertionError("Expected ChatFailure")
  }
  private fun rejected(block: () -> Unit) { try { block(); fail("Expected rejection") } catch (_: Exception) {} }
  private fun completed() = JSONObject().put("type", "response.completed").put("response", JSONObject().put("status", "completed").put("output", JSONArray().put(JSONObject().put("type", "message").put("role", "assistant").put("content", JSONArray().put(JSONObject().put("type", "output_text").put("text", "Reply 👋"))))))
  @Test fun exactRequestUsesOnlySupportedFieldsAndAlternatingHistory() {
    val body = JSONObject(ChatProtocol.requestBody("catalog-model", """[{"role":"user","text":"one"},{"role":"assistant","text":"two"},{"role":"user","text":"three"}]"""))
    assertEquals(setOf("model", "input", "store", "stream"), body.keys().asSequence().toSet())
    assertEquals("catalog-model", body.getString("model")); assertFalse(body.getBoolean("store")); assertTrue(body.getBoolean("stream"))
    assertEquals("three", body.getJSONArray("input").getJSONObject(2).getString("content"))
    rejected { ChatProtocol.requestBody("m", """[{"role":"assistant","text":"wrong"}]""") }
    rejected { ChatProtocol.requestBody("m", """[{"role":"user","text":"one"},{"role":"assistant","text":"two"}]""") }
    rejected { ChatProtocol.requestBody("m", JSONArray().put(JSONObject().put("role", "user").put("text", "x".repeat(50001))).toString()) }
  }
  @Test fun structuredHttpErrorsKeepCodesWithoutDescriptionsOrPrompts() {
    val cases = mapOf(403 to "subscription_sharing_user_not_eligible", 429 to "subscription_sharing_usage_limit_exceeded", 503 to "subscription_sharing_usage_unavailable", 400 to "subscription_sharing_unsupported_capability", 401 to "subscription_sharing_invalid_user")
    for ((status, code) in cases) {
      val payload = JSONObject().put("error", JSONObject().put("code", code).put("param", "model").put("message", secret))
      val e = failure { ChatProtocol.read(status, "application/json", payload.toString().reader(), "req_fixture_123") }
      assertEquals(code, e.providerCode); assertEquals("HTTP$status", e.reason); assertEquals("ERROR", e.shape)
      assertEquals("req_fixture_123", e.requestId); assertFalse(e.userMessage().contains(secret)); assertNull(e.cause)
    }
  }
  @Test fun admissionAndUnknownErrorsCannotLeakArbitraryFields() {
    val e = failure { ChatProtocol.read(403, "application/json", JSONObject().put("detail", secret).toString().reader(), secret) }
    assertEquals("ACC-CHAT-RESPONSE-HTTP403-DETAIL", e.diagnostic); assertNull(e.requestId); assertFalse(e.userMessage().contains(secret))
    val unknown = failure { ChatProtocol.read(400, null, JSONObject().put("error", JSONObject().put("code", secret).put("param", secret).put("message", secret)).toString().reader(), null) }
    assertNull(unknown.providerCode); assertNull(unknown.param); assertFalse(unknown.userMessage().contains(secret))
    assertEquals("OTHER", failure { ChatProtocol.read(502, "text/html", secret.reader(), null) }.shape)
  }
  @Test fun errorBodyReadIsBounded() {
    var count = 0
    val reader = object : java.io.Reader() {
      override fun read(chars: CharArray, offset: Int, length: Int): Int { java.util.Arrays.fill(chars, offset, offset + length, 'x'); count += length; return length }
      override fun close() {}
    }
    failure { ChatProtocol.read(503, "application/json", reader, null) }
    assertEquals(4096, count)
  }
  @Test fun terminalStreamErrorsPreserveProviderFailureAndNeverReturnPartialText() {
    val payload = JSONObject().put("type", "response.failed").put("response", JSONObject().put("error", JSONObject().put("code", "subscription_sharing_usage_limit_exceeded").put("message", secret)))
    val prefix = "data: {\"type\":\"response.output_text.delta\",\"delta\":\"partial\"}\n\n"
    val e = failure { ChatProtocol.read(200, "text/event-stream", (prefix + "data: $payload\n\n" + "data: ${completed()}\n\n").reader(), "req_fixture") }
    assertEquals("subscription_sharing_usage_limit_exceeded", e.providerCode); assertEquals(ChatStage.STREAM, e.stage)
    assertFalse(e.userMessage().contains(secret)); assertEquals("req_fixture", e.requestId)
    assertEquals("INCOMPLETE", failure { ChatProtocol.read(200, "text/event-stream", "data: {\"type\":\"response.incomplete\"}\n\n".reader(), null) }.reason)
    assertEquals("INTERRUPTED", failure { ChatProtocol.read(200, "text/event-stream", prefix.reader(), null) }.reason)
  }
  @Test fun successfulReplyNeedsTerminalCompletionAndEventStreamContentType() {
    assertEquals("Reply 👋", ChatProtocol.read(200, "Text/Event-Stream; charset=utf-8", "data: ${completed()}\n\n".reader(), null))
    assertEquals("CONTENT_TYPE", failure { ChatProtocol.read(200, "application/json", completed().toString().reader(), null) }.reason)
    val e = failure { ChatProtocol.read(200, "text/event-stream", "data: {\"type\":\"error\",\"code\":\"server_error\",\"message\":\"$secret\"}\n\n".reader(), null) }
    assertEquals("server_error", e.providerCode); assertFalse(e.userMessage().contains(secret))
  }
  @Test fun timeoutAndCancellationRemainDistinctAndSafe() {
    val e = ChatFailure.wrap(ChatStage.REQUEST, SocketTimeoutException(secret))
    assertEquals("ACC-CHAT-REQUEST-TIMEOUT", e.diagnostic); assertFalse(e.userMessage().contains(secret))
    assertEquals("CANCELLED", ChatFailure.wrap(ChatStage.REQUEST, java.io.IOException(secret), cancelled = true).reason)
    assertEquals("CANCELLED", failure { ChatProtocol.read(200, "text/event-stream", "data: ${completed()}\n\n".reader(), null) { throw ChatFailure(ChatStage.STREAM, "CANCELLED") } }.reason)
  }
}
