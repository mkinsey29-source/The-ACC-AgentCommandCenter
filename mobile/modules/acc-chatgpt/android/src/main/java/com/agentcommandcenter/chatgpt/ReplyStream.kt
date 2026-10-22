package com.agentcommandcenter.chatgpt

import java.io.Reader
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

// Wire framing and reply assembly only. No credentials, raw errors or prompt logging.
internal object ReplyStream {
  private const val MAX_RECORD = 700000
  private const val MAX_STREAM = 2000000
  private const val MAX_TEXT = 100000
  private fun reject(reason: String): Nothing = throw ChatFailure(ChatStage.STREAM, reason)

  fun read(reader: Reader, checkActive: () -> Unit = {}): String {
    var total = 0
    var skipLF = false
    val line = StringBuilder()
    var data = StringBuilder()
    var eventName: String? = null
    var hasData = false
    val text = StringBuilder()

    fun dispatch(): String? {
      if (!hasData && eventName == null) return null
      val raw = data.toString().removeSuffix("\n")
      val namedError = eventName == "error"
      data = StringBuilder(); eventName = null; hasData = false
      if (raw == "[DONE]") reject("INTERRUPTED")
      val value = try { JSONTokener(raw).nextValue() } catch (_: Exception) { reject("EVENT_JSON") }
      if (namedError && value !is JSONObject) reject("FAILED")
      // Match the devkit: valid scalar/array records carry no response event.
      // They cannot supply reply text or a completion marker.
      val event = value as? JSONObject ?: return null
      // The SDK treats a named error or top-level error payload as failure even
      // when the JSON omits its type field. Never let a later completion hide it.
      if (namedError || (event.has("error") && !event.isNull("error"))) {
        throw ChatProtocol.failedEvent(JSONObject().put("type", "error").put("error", event.opt("error") ?: event))
      }
      when (event.optString("type")) {
        "response.output_text.delta" -> {
          val delta = event.opt("delta") as? String ?: reject("EVENT_TEXT")
          if (text.length + delta.length > MAX_TEXT) reject("OUTPUT_LIMIT")
          text.append(delta)
        }
        "response.failed", "response.incomplete", "error" -> throw ChatProtocol.failedEvent(event)
        "response.completed" -> {
          checkActive()
          return completed(event, text.toString())
        }
      }
      return null
    }

    fun consumeLine(): String? {
      checkActive()
      val value = line.toString(); line.setLength(0)
      if (value.isEmpty()) return dispatch()
      if (value.startsWith(':')) return null
      val field = value.substringBefore(':')
      val content = value.substringAfter(':', "").removePrefix(" ")
      when (field) {
        "event" -> eventName = content.takeIf { it.isNotEmpty() }
        "data" -> {
          if (data.length + content.length + 1 > MAX_RECORD) reject("EVENT_LIMIT")
          hasData = true; data.append(content).append('\n')
        }
      }
      return null
    }

    while (true) {
      if (total % 1024 == 0) checkActive()
      val c = reader.read()
      if (c == -1) {
        // Match the SDK's single EOF flush. An unterminated *completed* event
        // can finish; EOF after deltas, [DONE] or invalid JSON cannot.
        if (line.isNotEmpty()) consumeLine()?.let { return it }
        dispatch()?.let { return it }
        reject("INTERRUPTED")
      }
      total++; if (total > MAX_STREAM) reject("STREAM_LIMIT")
      if (total == 1 && c == 0xFEFF) continue
      if (skipLF) { skipLF = false; if (c == 10) continue }
      if (c == 10 || c == 13) {
        skipLF = c == 13
        consumeLine()?.let { return it }
      } else {
        if (line.length >= MAX_RECORD) reject("LINE_LIMIT")
        line.append(c.toChar())
      }
    }
  }

  fun completed(event: JSONObject, streamedText: String = ""): String {
    if (event.optString("type") != "response.completed") reject("COMPLETE_TYPE")
    val response = event.optJSONObject("response")
    if (event.has("response") && response == null) reject("COMPLETE_SHAPE")
    if (response != null && response.has("error") && !response.isNull("error")) {
      throw ChatProtocol.failedEvent(JSONObject().put("type", "response.failed").put("response", response))
    }
    if (response != null && response.has("status") && response.optString("status") != "completed") reject("COMPLETE_STATUS")
    val output = response?.optJSONArray("output")
    if (response?.has("output") == true && output == null) reject("COMPLETE_OUTPUT")
    val finalText = StringBuilder()
    if (output != null) for (i in 0 until output.length()) {
      val item = output.optJSONObject(i) ?: reject("COMPLETE_OUTPUT")
      if (item.optString("type") != "message" || item.optString("role") != "assistant") continue
      val content: JSONArray = item.optJSONArray("content") ?: reject("COMPLETE_OUTPUT")
      for (j in 0 until content.length()) {
        val part = content.optJSONObject(j) ?: reject("COMPLETE_OUTPUT")
        if (part.optString("type") != "output_text") continue
        val value = part.opt("text") as? String ?: reject("COMPLETE_OUTPUT")
        if (finalText.length + value.length > MAX_TEXT) reject("OUTPUT_LIMIT")
        finalText.append(value)
      }
    }
    // The official devkit returns accumulated output_text deltas only after
    // response.completed; the terminal event need not repeat the full output.
    val result = if (finalText.isNotBlank()) finalText.toString() else streamedText
    if (result.isBlank()) reject("EMPTY_OUTPUT")
    if (result.length > MAX_TEXT) reject("OUTPUT_LIMIT")
    return result
  }
}
