package com.agentcommandcenter.chatgpt

import org.junit.Assert.*
import org.junit.Test
import org.json.JSONObject
import org.json.JSONArray
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.interfaces.RSAPublicKey

class OAuthCoreTest {
  private val keys = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
  private val client = "oaiapp_test_registration"
  private fun claims() = JSONObject().put("iss", OAuthCore.ISSUER).put("sub", "test-subject").put("aud", client).put("nonce", "test-nonce").put("exp", 2000).put("iat", 900)
  private fun jwks(): JSONObject {
    val pub = keys.public as RSAPublicKey
    return JSONObject().put("keys", JSONArray().put(JSONObject().put("kid", "test-key").put("kty", "RSA").put("alg", "RS256").put("use", "sig")
      .put("n", OAuthCore.b64(pub.modulus.toByteArray())).put("e", OAuthCore.b64(pub.publicExponent.toByteArray()))))
  }
  private fun token(c: JSONObject = claims(), alg: String = "RS256"): String {
    val header = OAuthCore.b64(JSONObject().put("alg", alg).put("kid", "test-key").toString().toByteArray())
    val body = OAuthCore.b64(c.toString().toByteArray())
    val input = "$header.$body"
    val sig = Signature.getInstance("SHA256withRSA").apply { initSign(keys.private); update(input.toByteArray()) }.sign()
    return "$input.${OAuthCore.b64(sig)}"
  }
  private fun rejects(block: () -> Unit) { try { block(); fail("Expected rejection") } catch (_: Exception) {} }
  @Test fun pkceMatchesRfc7636() {
    assertEquals("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", OAuthCore.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"))
    assertNotEquals(OAuthCore.random(), OAuthCore.random())
  }
  @Test fun callbackStateAndIssuedClientAreBound() {
    assertEquals("code" to client, OAuthCore.callback("/auth/callback?state=state&code=code&client_id=$client", 1455, "state", null))
    assertEquals("code" to client, OAuthCore.callback("/auth/callback?state=state&code=code", 1455, "state", client))
    for (target in listOf("/wrong?state=state&code=code&client_id=$client", "/auth/callback?state=wrong&code=code&client_id=$client", "/auth/callback?state=state&state=other&code=code&client_id=$client", "/auth/callback?state=state&code=code", "/auth/callback?state=state&error=access_denied", "https://example.com/auth/callback?state=state&code=code&client_id=$client")) rejects { OAuthCore.callback(target, 1455, "state", null) }
    rejects { OAuthCore.callback("/auth/callback?state=state&code=code&client_id=oaiapp_other_client", 1455, "state", client) }
    rejects { OAuthCore.callback("/auth/callback?state=state&code=code&client_id=dynamic_agent_client", 1455, "state", null) }
  }
  @Test fun signatureAndOidcClaimsAreRequired() {
    assertEquals("test-subject", OAuthCore.validateId(token(), jwks(), client, "test-nonce", now = 1000).getString("sub"))
    for ((field, value) in listOf("iss" to "https://evil.example", "sub" to "", "aud" to "other", "nonce" to "other", "exp" to 1000, "iat" to 3000, "nbf" to 1001)) rejects { OAuthCore.validateId(token(claims().put(field, value)), jwks(), client, "test-nonce", now = 1000) }
    rejects { OAuthCore.validateId(token(alg = "none"), jwks(), client, "test-nonce", now = 1000) }
    rejects { OAuthCore.validateId(token().dropLast(20) + "A".repeat(20), jwks(), client, "test-nonce", now = 1000) }
    rejects { OAuthCore.validateId(token(), jwks(), client, "test-nonce", "other-subject", 1000) }
  }
  @Test fun multiAudienceNeedsAuthorizedParty() {
    val c = claims().put("aud", JSONArray().put(client).put("other"))
    rejects { OAuthCore.validateId(token(c), jwks(), client, "test-nonce", now = 1000) }
    OAuthCore.validateId(token(c.put("azp", client)), jwks(), client, "test-nonce", now = 1000)
  }
  @Test fun identityAloneDoesNotAuthorizePlanUse() {
    val response = JSONObject().put("scope", "openid profile email").put("token_type", "Bearer").put("access_token", "fixture-access").put("refresh_token", "fixture-refresh").put("expires_in", 3600)
    rejects { OAuthCore.scopes(response) }
    response.put("scope", "chatgpt.tokens.use.direct resource.invoke offline_access")
    assertTrue(OAuthCore.scopes(response).contains("chatgpt.tokens.use.direct"))
    rejects { OAuthCore.scopes(JSONObject(response.toString()).put("token_type", "Basic")) }
    rejects { OAuthCore.scopes(JSONObject(response.toString()).put("refresh_token", "")) }
  }
  private fun completed(text: String = "Hello tablet 👋"): JSONObject = JSONObject().put("type", "response.completed").put("response", JSONObject().put("status", "completed").put("output", JSONArray().put(JSONObject().put("type", "message").put("role", "assistant").put("content", JSONArray().put(JSONObject().put("type", "output_text").put("text", text))))))
  @Test fun streamRequiresSuccessfulTerminalEvent() {
    val prefix = "event: response.output_text.delta\r\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"partial\"}\r\n\r\n"
    assertEquals("Hello tablet 👋", OAuthCore.readStream((prefix + "data: ${completed()}\r\n\r\n").reader()))
    for (ending in listOf("", "data: [DONE]\n\n", "data: {\"type\":\"response.failed\"}\n\n", "data: {\"type\":\"response.incomplete\"}\n\n", "data: {\"type\":\"error\"}\n\n")) rejects { OAuthCore.readStream((prefix + ending).reader()) }
    rejects { OAuthCore.readStream(("data: ${completed("")}\n\n").reader()) }
    rejects { OAuthCore.readStream(("data: ${completed().put("response", JSONObject().put("status", "incomplete"))}\n\n").reader()) }
  }
  @Test fun streamLimitsAndCancellationAreEnforced() {
    rejects { OAuthCore.readStream(("data: " + "x".repeat(700001) + "\n\n").reader()) }
    rejects { OAuthCore.readStream(("data: ${completed()}\n\n").reader()) { error("cancelled") } }
  }
}
