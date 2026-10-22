package com.agentcommandcenter.chatgpt

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.SocketTimeoutException
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient

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
  @Test fun streamRequestExplicitlyNegotiatesEventsWithoutChangingPayloadOrBilling() {
    val body = ChatProtocol.requestBody("catalog-model", """[{"role":"user","text":"one"}]""")
    val request = ChatProtocol.streamRequest(body, "fixture-token")
    assertEquals("https://api.openai.com/v1/responses", request.url.toString())
    assertEquals("POST", request.method)
    assertEquals("text/event-stream", request.header("Accept"))
    assertEquals("Bearer fixture-token", request.header("Authorization"))
    assertNull(request.header("OpenAI-Organization")); assertNull(request.header("OpenAI-Project"))
    val bytes = okio.Buffer(); requireNotNull(request.body).writeTo(bytes)
    assertEquals(body, bytes.readUtf8()); assertEquals("application/json; charset=utf-8", request.body!!.contentType().toString())
  }
  @Test fun missingOrBlankContentTypeAcceptsOnlyCompletedEvents() {
    for (contentType in listOf(null, "", "   ")) {
      assertEquals("Reply 👋", ChatProtocol.read(200, contentType, "data: ${completed()}\n\n".reader(), null))
    }
    assertEquals("Reply 👋", ChatProtocol.read(200, "TEXT/EVENT-STREAM; charset=UTF-8", ": keepalive\r\n\r\ndata: ${completed()}\r\n\r\n".reader(), null))
  }
  @Test fun missingContentTypeNeverPromotesJsonHtmlPartialOrFailedOutput() {
    val partial = "data: {\"type\":\"response.output_text.delta\",\"delta\":\"partial\"}\n\n"
    for (raw in listOf("", "<html>$secret</html>\n", completed().toString(), partial, partial + "data: [DONE]\n\n")) {
      val e = failure { ChatProtocol.read(200, null, raw.reader(), "req_fixture") }
      assertEquals("INTERRUPTED", e.reason); assertFalse(e.userMessage().contains(secret))
    }
    val e = failure { ChatProtocol.read(200, null, "data: {\"type\":\"error\",\"code\":\"server_error\",\"message\":\"$secret\"}\n\n".reader(), "req_fixture") }
    assertEquals("server_error", e.providerCode); assertFalse(e.userMessage().contains(secret))
    assertEquals("CANCELLED", failure { ChatProtocol.read(200, null, "data: ${completed()}\n\n".reader(), null) { throw ChatFailure(ChatStage.STREAM, "CANCELLED") } }.reason)
  }
  @Test fun conflictingContentTypeIsClassifiedWithoutReadingOrExposingRawHeader() {
    val reader = object : java.io.Reader() {
      override fun read(chars: CharArray, offset: Int, length: Int): Int = throw AssertionError("Must not read conflicting media type")
      override fun close() {}
    }
    for ((mime, shape) in listOf("application/json" to "JSON", "text/html; charset=utf-8" to "HTML", "text/plain" to "TEXT", secret to "OTHER")) {
      val e = failure { ChatProtocol.read(200, mime, reader, secret) }
      assertEquals("ACC-CHAT-RESPONSE-CONTENT_TYPE-$shape", e.diagnostic)
      assertFalse(e.userMessage().contains(secret)); assertNull(e.requestId)
    }
  }
  @Test fun actualHttpResponseWithoutContentTypeCompletesOneRequest() {
    // Reproduce the documented direct-route response at the OkHttp/Reader boundary.
    // Only the fixture destination changes; production headers/body builder is used.
    val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")); server.soTimeout = 5000
    val worker = Executors.newSingleThreadExecutor()
    val client = OkHttpClient.Builder().retryOnConnectionFailure(false).followRedirects(false)
      .callTimeout(5, TimeUnit.SECONDS).build()
    try {
      val received = worker.submit<List<String>> {
        server.accept().use { socket ->
          socket.soTimeout = 5000
          val input = socket.getInputStream().bufferedReader(Charsets.UTF_8)
          val headers = mutableListOf<String>()
          while (true) { val line = requireNotNull(input.readLine()); if (line.isEmpty()) break; headers.add(line) }
          val length = headers.single { it.startsWith("Content-Length:", ignoreCase = true) }.substringAfter(':').trim().toInt()
          val body = CharArray(length); var count = 0
          while (count < length) { val n = input.read(body, count, length - count); check(n > 0); count += n }
          check(JSONObject(String(body)).getBoolean("stream"))
          val payload = "data: ${completed()}\n\n".toByteArray(Charsets.UTF_8)
          socket.getOutputStream().apply {
            write("HTTP/1.1 200 OK\r\nContent-Length: ${payload.size}\r\nx-request-id: req_fixture_http\r\nConnection: close\r\n\r\n".toByteArray(Charsets.US_ASCII))
            write(payload); flush()
          }
          headers
        }
      }
      val request = ChatProtocol.streamRequest(ChatProtocol.requestBody("catalog-model", """[{"role":"user","text":"one"}]"""), "fixture-token")
        .newBuilder().url("http://127.0.0.1:${server.localPort}/responses").build()
      client.newCall(request).execute().use { response ->
        assertEquals(200, response.code); assertNull(response.header("Content-Type"))
        assertEquals("Reply 👋", ChatProtocol.read(response.code, response.header("Content-Type"), response.body!!.charStream(), response.header("x-request-id")))
      }
      assertTrue(received.get(5, TimeUnit.SECONDS).any { it.equals("Accept: text/event-stream", ignoreCase = true) })
    } finally {
      server.close(); worker.shutdownNow(); client.dispatcher.executorService.shutdownNow(); client.connectionPool.evictAll()
    }
  }
}
