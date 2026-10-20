package com.agentcommandcenter.chatgpt

import org.json.JSONObject
import okio.ByteString.Companion.toByteString
import okio.ByteString.Companion.decodeBase64
import java.math.BigInteger
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.RSAPublicKeySpec
import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder

internal object OAuthCore {
  const val ISSUER = "https://auth.openai.com"
  const val RESOURCE = "https://api.openai.com/v1"
  fun b64(bytes: ByteArray) = bytes.toByteString().base64Url().trimEnd('=')
  fun decode(value: String): ByteArray = value.decodeBase64()?.toByteArray() ?: error("Invalid encoding")
  fun random() = b64(ByteArray(32).also { SecureRandom().nextBytes(it) })
  fun challenge(verifier: String) = b64(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)))
  fun encode(value: String): String = URLEncoder.encode(value, "UTF-8")
  fun form(values: Map<String, String>) = values.entries.joinToString("&") { encode(it.key) + "=" + encode(it.value) }
  fun query(raw: String): Map<String, String> {
    require(raw.length <= 8192)
    val result = mutableMapOf<String, String>()
    for (part in raw.split('&')) {
      val pair = part.split('=', limit = 2)
      val key = URLDecoder.decode(pair[0], "UTF-8")
      require(!result.containsKey(key))
      result[key] = URLDecoder.decode(pair.getOrElse(1) { "" }, "UTF-8")
    }
    return result
  }
  fun callback(target: String, port: Int, state: String, previous: String?): Pair<String, String> {
    val uri = URI(target)
    require(uri.rawPath == "/auth/callback" && uri.fragment == null && uri.scheme == null && uri.rawAuthority == null)
    val q = query(uri.rawQuery ?: "")
    require(MessageDigest.isEqual((q["state"] ?: "").toByteArray(), state.toByteArray()))
    if (q.containsKey("error")) error("Sign-in was declined. Return to ACC to try again.")
    val client = q["client_id"] ?: previous ?: error("Registration did not return a client ID")
    require(client != "dynamic_agent_client" && client.matches(Regex("[A-Za-z0-9_-]{8,240}")))
    require(previous == null || previous == client)
    val code = q["code"] ?: error("No authorization code")
    require(code.isNotBlank() && code.length <= 4096)
    require(port in 1..65535)
    return code to client
  }
  fun validateId(token: String, jwks: JSONObject, client: String, nonce: String?, subject: String? = null, now: Long = System.currentTimeMillis() / 1000): JSONObject {
    require(token.length <= 32768)
    val parts = token.split('.')
    require(parts.size == 3)
    val header = JSONObject(String(decode(parts[0]), Charsets.UTF_8))
    require(header.optString("alg") == "RS256" && !header.has("crit"))
    val keys = jwks.getJSONArray("keys")
    val matches = (0 until keys.length()).map { keys.getJSONObject(it) }.filter { it.optString("kid") == header.getString("kid") }
    require(matches.size == 1)
    val key = matches.single()
    require(key.getString("kty") == "RSA" && key.optString("use", "sig") == "sig" && key.optString("alg", "RS256") == "RS256")
    val publicKey = KeyFactory.getInstance("RSA").generatePublic(RSAPublicKeySpec(BigInteger(1, decode(key.getString("n"))), BigInteger(1, decode(key.getString("e")))))
    val signature = Signature.getInstance("SHA256withRSA")
    signature.initVerify(publicKey); signature.update((parts[0] + "." + parts[1]).toByteArray(Charsets.US_ASCII))
    require(signature.verify(decode(parts[2])))
    val claims = JSONObject(String(decode(parts[1]), Charsets.UTF_8))
    require(claims.getString("iss") == ISSUER && claims.getLong("exp") > now && claims.getLong("iat") <= now + 60)
    if (claims.has("nbf")) require(claims.getLong("nbf") <= now)
    val aud = claims.get("aud")
    val audiences = if (aud is String) listOf(aud) else if (aud is org.json.JSONArray) (0 until aud.length()).map { aud.getString(it) } else emptyList()
    require(client in audiences && (audiences.size == 1 || claims.optString("azp") == client))
    require(claims.getString("sub").isNotBlank())
    if (nonce != null) require(claims.getString("nonce") == nonce)
    if (subject != null) require(claims.getString("sub") == subject)
    return claims
  }
  fun scopes(response: JSONObject): List<String> {
    val granted = response.getString("scope").split(Regex("\\s+"))
    require("chatgpt.tokens.use.direct" in granted && "resource.invoke" in granted)
    require(response.getString("token_type").equals("Bearer", ignoreCase = true))
    require(response.getString("access_token").isNotBlank() && response.getString("refresh_token").isNotBlank())
    require(response.getLong("expires_in") in 1..604800)
    return granted
  }
  fun readStream(reader: java.io.Reader, checkActive: () -> Unit = {}): String {
    var total = 0
    var event = StringBuilder()
    while (true) {
      checkActive()
      val part = StringBuilder()
      var eof = false
      while (true) {
        val c = reader.read()
        if (c == -1) { eof = true; break }
        if (c == 10) break
        part.append(c.toChar()); require(part.length <= 700000)
      }
      if (eof) error("Response interrupted")
      val line = part.toString().trimEnd('\r')
      total += line.length; require(total <= 2000000)
      if (line.isEmpty() && event.isNotEmpty()) {
        val data = event.toString().trimEnd('\n'); event = StringBuilder()
        if (data == "[DONE]") error("Response did not complete")
        val e = JSONObject(data)
        when (e.optString("type")) {
          "response.completed" -> { checkActive(); return completed(e) }
          "response.failed", "response.incomplete", "error" -> error("Provider response did not complete")
        }
      } else if (line.startsWith("data:")) {
        event.append(line.removePrefix("data:").trimStart(' ')).append('\n'); require(event.length <= 700000)
      }
    }
  }
  fun completed(event: JSONObject): String {
    require(event.getString("type") == "response.completed")
    val response = event.getJSONObject("response")
    require(response.getString("status") == "completed")
    val output = response.getJSONArray("output")
    val text = StringBuilder()
    for (i in 0 until output.length()) {
      val item = output.getJSONObject(i)
      if (item.optString("type") != "message" || item.optString("role") != "assistant") continue
      val content = item.getJSONArray("content")
      for (j in 0 until content.length()) {
        val part = content.getJSONObject(j)
        if (part.optString("type") == "output_text") text.append(part.getString("text"))
      }
    }
    require(text.isNotBlank() && text.length <= 100000)
    return text.toString()
  }
}
