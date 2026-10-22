package com.agentcommandcenter.chatgpt

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient

class ReplyStreamTest {
  private val secret = "private-error-prompt-token-do-not-display"
  private fun event(type: String) = JSONObject().put("type", type)
  private fun delta(text: String) = event("response.output_text.delta").put("delta", text)
  private fun terminal() = event("response.completed").put("response", JSONObject().put("status", "completed"))
  private fun record(value: JSONObject, ending: String = "\n") = "data: $value$ending$ending"
  private fun read(raw: String, check: () -> Unit = {}) = ChatProtocol.read(200, null, raw.reader(), "req_fixture", check)
  private fun failure(raw: String, check: () -> Unit = {}): ChatFailure {
    try { read(raw, check); fail("Expected stream failure") } catch (e: ChatFailure) { return e }
    throw AssertionError("Expected ChatFailure")
  }
  private fun full(text: String) = terminal().put("response", JSONObject().put("status", "completed").put("output", JSONArray().put(JSONObject().put("type", "message").put("role", "assistant").put("content", JSONArray().put(JSONObject().put("type", "output_text").put("text", text))))))

  @Test fun compactTerminalReturnsAccumulatedDeltasAfterCompletion() {
    assertEquals("Hello 👋", read(record(delta("Hello ")) + record(delta("👋")) + record(terminal())))
  }
  @Test fun devkitTypeOnlyCompletionDoesNotRequireRepeatedSnapshot() {
    assertEquals("Hello", read(record(delta("Hello")) + record(event("response.completed"))))
  }
  @Test fun fullSnapshotIsAuthoritativeAndNeverDuplicatedWithDeltas() {
    assertEquals("Final", read(record(delta("Partial")) + record(full("Final"))))
    assertEquals("Final", read(record(full("Final"))))
  }
  @Test fun failedIncompleteAndContradictoryStatusNeverPromoteDeltas() {
    val prefix = record(delta("partial"))
    for (type in listOf("response.failed", "response.incomplete", "error")) {
      val e = failure(prefix + record(event(type)) + record(terminal()))
      assertEquals(if (type == "response.incomplete") "INCOMPLETE" else "FAILED", e.reason)
    }
    for (status in listOf("failed", "incomplete", "in_progress", secret)) {
      val e = failure(prefix + record(terminal().put("response", JSONObject().put("status", status))))
      assertEquals("COMPLETE_STATUS", e.reason); assertFalse(e.userMessage().contains(secret))
    }
  }
  @Test fun sseHandlesBomCommentsMultilineJsonAndAllLineEndings() {
    for (ending in listOf("\n", "\r", "\r\n")) {
      val raw = "\uFEFF: heartbeat$ending$ending" + "event: response.output_text.delta${ending}data: {\"type\": \"response.output_text.delta\",${ending}data: \"delta\": \"Hello 👋\"}$ending$ending" + record(terminal(), ending)
      assertEquals("Hello 👋", read(raw))
    }
  }
  @Test fun eofFlushesValidCompletedEventOnceButCannotCompletePartialOutput() {
    val prefix = record(delta("Hello"))
    for (suffix in listOf("", "\n", "\r", "\r\n")) assertEquals("Hello", read(prefix + "data: ${terminal()}$suffix"))
    for (suffix in listOf("", "data: [DONE]", "data: [DONE]\n\n")) assertEquals("INTERRUPTED", failure(prefix + suffix).reason)
  }
  @Test fun malformedEventsUseSpecificPrivateDiagnostics() {
    val e = failure("data: {\"message\":\"$secret\n\n")
    assertEquals("EVENT_JSON", e.reason); assertFalse(e.userMessage().contains(secret)); assertNull(e.cause)
    assertEquals("req_fixture", e.requestId)
    assertEquals("EVENT_JSON", failure("data: {\"type\":\"response.completed\"").reason)
    assertEquals("EVENT_TEXT", failure(record(event("response.output_text.delta").put("delta", JSONObject().put("secret", secret)))).reason)
  }
  @Test fun terminalStructureAndEmptyOutputAreDistinctFromJsonErrors() {
    assertEquals("EMPTY_OUTPUT", failure(record(terminal())).reason)
    assertEquals("COMPLETE_SHAPE", failure(record(event("response.completed").put("response", secret))).reason)
    assertEquals("COMPLETE_OUTPUT", failure(record(terminal().put("response", JSONObject().put("output", secret)))).reason)
    assertEquals("COMPLETE_OUTPUT", failure(record(full("Hello").apply { getJSONObject("response").getJSONArray("output").getJSONObject(0).put("content", secret) })).reason)
  }
  @Test fun everyBufferLimitRemainsBoundedIncludingBlankLines() {
    assertEquals("LINE_LIMIT", failure("x".repeat(700001)).reason)
    assertEquals("EVENT_LIMIT", failure("data: " + "x".repeat(350000) + "\ndata: " + "x".repeat(350000) + "\n\n").reason)
    assertEquals("STREAM_LIMIT", failure("\n".repeat(2000001)).reason)
    assertEquals("OUTPUT_LIMIT", failure(record(delta("x".repeat(60000))) + record(delta("x".repeat(40001)))).reason)
  }
  @Test fun namedAndUntypedErrorsCannotBeHiddenByLaterCompletion() {
    val error = JSONObject().put("code", "server_error").put("message", secret)
    val raw = record(delta("partial")) + "event: error\ndata: $error\n\n" + record(terminal())
    val e = failure(raw); assertEquals("server_error", e.providerCode); assertFalse(e.userMessage().contains(secret))
    assertEquals("FAILED", failure(record(JSONObject().put("error", error)) + record(full("must not return"))).reason)
    assertEquals("FAILED", failure(record(delta("partial")) + record(terminal().put("response", JSONObject().put("error", error)))).reason)
  }
  @Test fun cancellationAndNetworkFailureNeverReturnBufferedPartialText() {
    assertEquals("CANCELLED", failure(record(delta("partial")) + record(terminal())) { throw ChatFailure(ChatStage.STREAM, "CANCELLED") }.reason)
    val raw = record(delta("partial")); var offset = 0
    val reader = object : java.io.Reader() {
      override fun read(chars: CharArray, start: Int, length: Int): Int {
        if (offset == raw.length) throw java.io.IOException(secret)
        chars[start] = raw[offset++]; return 1
      }
      override fun close() {}
    }
    try { ChatProtocol.read(200, null, reader, null); fail("Expected network failure") }
    catch (e: ChatFailure) { assertEquals("NETWORK", e.reason); assertFalse(e.userMessage().contains(secret)) }
  }
  @Test fun actualChunkedHttpHandlesSplitUtf8MixedNewlinesAndCompactEofCompletion() {
    val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")); server.soTimeout = 5000
    val worker = Executors.newSingleThreadExecutor()
    val client = OkHttpClient.Builder().retryOnConnectionFailure(false).followRedirects(false).callTimeout(5, TimeUnit.SECONDS).build()
    try {
      val served = worker.submit<Boolean> {
        server.accept().use { socket ->
          socket.soTimeout = 5000
          val input = socket.getInputStream().bufferedReader(Charsets.UTF_8); val headers = mutableListOf<String>()
          while (true) { val line = requireNotNull(input.readLine()); if (line.isEmpty()) break; headers.add(line) }
          val length = headers.single { it.startsWith("Content-Length:", ignoreCase = true) }.substringAfter(':').trim().toInt()
          val body = CharArray(length); var n = 0
          while (n < length) { val count = input.read(body, n, length - n); check(count > 0); n += count }
          val bytes = ("\uFEFF: heartbeat\r\n\r\n" + record(delta("Hello 👋"), "\r") + "data: ${terminal()}").toByteArray(Charsets.UTF_8)
          socket.getOutputStream().buffered().apply {
            write("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n".toByteArray(Charsets.US_ASCII))
            for (b in bytes) { write("1\r\n".toByteArray()); write(byteArrayOf(b)); write("\r\n".toByteArray()) }
            write("0\r\n\r\n".toByteArray()); flush()
          }
          headers.any { it.equals("Accept: text/event-stream", ignoreCase = true) }
        }
      }
      val request = ChatProtocol.streamRequest(ChatProtocol.requestBody("fixture-model", """[{"role":"user","text":"one"}]"""), "fixture-token")
        .newBuilder().url("http://127.0.0.1:${server.localPort}/responses").build()
      client.newCall(request).execute().use { response ->
        assertNull(response.header("Content-Type"))
        assertEquals("Hello 👋", ChatProtocol.read(response.code, response.header("Content-Type"), response.body!!.charStream(), null))
      }
      assertTrue(served.get(5, TimeUnit.SECONDS))
    } finally { server.close(); worker.shutdownNow(); client.dispatcher.executorService.shutdownNow(); client.connectionPool.evictAll() }
  }
}
