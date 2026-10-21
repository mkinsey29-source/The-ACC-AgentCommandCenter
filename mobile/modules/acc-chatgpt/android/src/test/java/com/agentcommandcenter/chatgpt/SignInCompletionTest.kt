package com.agentcommandcenter.chatgpt

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

class SignInCompletionTest {
  private val secret = "must-never-appear-in-diagnostics"
  private fun response() = JSONObject().put("scope", "chatgpt.tokens.use.direct resource.invoke offline_access")
    .put("token_type", "Bearer").put("access_token", secret).put("refresh_token", secret).put("expires_in", 3600)

  @Test fun completionRequiresEveryValidationBeforeSaving() {
    val events = mutableListOf<String>()
    SignInCompletion.run(
      register = { events.add("registration"); "registration-id" },
      exchange = { events.add("token"); response() },
      loadKeys = { events.add("keys"); JSONObject() },
      validateIdentity = { _, _ -> events.add("identity"); JSONObject().put("sub", "verified") },
      store = { id, tokens, scopes, claims ->
        assertEquals("registration-id", id); assertEquals(secret, tokens.getString("access_token"))
        assertTrue("chatgpt.tokens.use.direct" in scopes); assertEquals("verified", claims.getString("sub"))
        events.add("storage")
      },
    )
    assertEquals(listOf("registration", "token", "keys", "identity", "storage"), events)
  }

  @Test fun failuresKeepTheirActualStageAndNeverSaveUnverifiedCredentials() {
    for (stage in listOf(SignInStage.REGISTRATION, SignInStage.TOKEN, SignInStage.PERMISSIONS, SignInStage.KEYS, SignInStage.IDENTITY, SignInStage.STORAGE)) {
      var stored = false
      try {
        SignInCompletion.run(
          register = { if (stage == SignInStage.REGISTRATION) error(secret); "registration-id" },
          exchange = { if (stage == SignInStage.TOKEN) error(secret); if (stage == SignInStage.PERMISSIONS) JSONObject() else response() },
          loadKeys = { if (stage == SignInStage.KEYS) error(secret); JSONObject() },
          validateIdentity = { _, _ -> if (stage == SignInStage.IDENTITY) error(secret); JSONObject() },
          store = { _, _, _, _ -> if (stage == SignInStage.STORAGE) error(secret); stored = true },
        )
        fail("Expected failed stage $stage")
      } catch (e: SignInFailure) {
        assertEquals(stage, e.stage)
        assertEquals("ACC-SIGNIN-${stage.name}-VALIDATION", e.diagnostic)
        assertFalse(e.userMessage().contains(secret)); assertNull(e.cause)
      }
      assertFalse(stored)
    }
  }

  @Test fun onlyKnownOAuthCodesCanReachTheUI() {
    for (body in listOf("{\"error\":\"invalid_grant\",\"error_description\":\"$secret\"}", "{\"error\":{\"code\":\"invalid_grant\",\"message\":\"$secret\"}}")) {
      val e = SignInFailure(SignInStage.TOKEN, SignInHttpFailure.from(400, body))
      assertEquals("ACC-SIGNIN-TOKEN-HTTP400-INVALID_GRANT", e.diagnostic)
      assertFalse(e.userMessage().contains(secret))
    }
    for (body in listOf(secret, "{\"error\":\"$secret\"}", "{\"error\":{\"code\":\"$secret\"}}")) {
      val e = SignInFailure(SignInStage.TOKEN, SignInHttpFailure.from(403, body))
      assertEquals("ACC-SIGNIN-TOKEN-HTTP403", e.diagnostic)
      assertFalse(e.userMessage().contains(secret))
    }
  }

  @Test fun networkDiagnosticsAreStaticAndDoNotExposeExceptionMessages() {
    for ((error, expected) in listOf(SocketTimeoutException(secret) to "TIMEOUT", UnknownHostException(secret) to "DNS", SSLException(secret) to "TLS", java.io.IOException(secret) to "NETWORK")) {
      val e = SignInFailure(SignInStage.KEYS, error)
      assertEquals("ACC-SIGNIN-KEYS-$expected", e.diagnostic)
      assertFalse(e.userMessage().contains(secret)); assertNull(e.cause)
    }
  }
}
