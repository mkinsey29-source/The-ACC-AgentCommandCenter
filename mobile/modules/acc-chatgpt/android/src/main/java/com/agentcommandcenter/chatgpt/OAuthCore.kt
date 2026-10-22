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
    signInRequire(header.optString("alg") == "RS256" && !header.has("crit"), SignInCheck.ALGORITHM)
    val keys = jwks.getJSONArray("keys")
    val matches = (0 until keys.length()).map { keys.getJSONObject(it) }.filter { it.optString("kid") == header.getString("kid") }
    signInRequire(matches.size == 1, SignInCheck.KEY)
    val key = matches.single()
    signInRequire(key.getString("kty") == "RSA" && key.optString("use", "sig") == "sig" && key.optString("alg", "RS256") == "RS256", SignInCheck.KEY)
    val publicKey = KeyFactory.getInstance("RSA").generatePublic(RSAPublicKeySpec(BigInteger(1, decode(key.getString("n"))), BigInteger(1, decode(key.getString("e")))))
    val signature = Signature.getInstance("SHA256withRSA")
    signature.initVerify(publicKey); signature.update((parts[0] + "." + parts[1]).toByteArray(Charsets.US_ASCII))
    signInRequire(signature.verify(decode(parts[2])), SignInCheck.SIGNATURE)
    val claims = JSONObject(String(decode(parts[1]), Charsets.UTF_8))
    signInRequire(claims.getString("iss") == ISSUER, SignInCheck.ISSUER)
    signInRequire(claims.getLong("exp") > now, SignInCheck.EXPIRY)
    signInRequire(claims.getLong("iat") <= now + 60, SignInCheck.CLOCK)
    if (claims.has("nbf")) signInRequire(claims.getLong("nbf") <= now, SignInCheck.CLOCK)
    val aud = claims.get("aud")
    val audiences = if (aud is String) listOf(aud) else if (aud is org.json.JSONArray) (0 until aud.length()).map { aud.getString(it) } else emptyList()
    signInRequire(client in audiences && (audiences.size == 1 || claims.optString("azp") == client), SignInCheck.AUDIENCE)
    signInRequire(claims.getString("sub").isNotBlank(), SignInCheck.SUBJECT)
    if (nonce != null) signInRequire(claims.optString("nonce") == nonce, SignInCheck.NONCE)
    if (subject != null) signInRequire(claims.getString("sub") == subject, SignInCheck.SUBJECT)
    return claims
  }
  fun scopes(response: JSONObject): List<String> {
    val granted = response.getString("scope").split(Regex("\\s+"))
    signInRequire("chatgpt.tokens.use.direct" in granted && "resource.invoke" in granted, SignInCheck.PLAN_SCOPE)
    signInRequire(response.getString("token_type").equals("Bearer", ignoreCase = true), SignInCheck.TOKEN_TYPE)
    signInRequire(response.getString("access_token").isNotBlank() && response.getString("refresh_token").isNotBlank(), SignInCheck.TOKEN_FIELDS)
    signInRequire(response.getLong("expires_in") in 1..604800, SignInCheck.TOKEN_EXPIRY)
    return granted
  }
  fun readStream(reader: java.io.Reader, checkActive: () -> Unit = {}): String = ReplyStream.read(reader, checkActive)
  fun completed(event: JSONObject): String = ReplyStream.completed(event)
}
