package com.agentcommandcenter.chatgpt

import org.json.JSONObject
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

// Only fixed local categories cross the native bridge. Never expose remote error
// descriptions, exception messages, callback URLs, tokens or account claims.
internal enum class SignInStage { CALLBACK, REGISTRATION, TOKEN, PERMISSIONS, KEYS, IDENTITY, STORAGE }
internal enum class SignInCheck { ALGORITHM, KEY, SIGNATURE, ISSUER, EXPIRY, CLOCK, AUDIENCE, SUBJECT, NONCE, PLAN_SCOPE, TOKEN_TYPE, TOKEN_FIELDS, TOKEN_EXPIRY }
internal class SignInValidationFailure(val check: SignInCheck) : Exception()
internal fun signInRequire(valid: Boolean, check: SignInCheck) { if (!valid) throw SignInValidationFailure(check) }
internal class SignInHttpFailure private constructor(val status: Int, val oauth: String?) : Exception() {
  companion object {
    private val allowed = setOf("invalid_grant", "invalid_client", "invalid_request", "invalid_scope", "access_denied", "unauthorized_client", "unsupported_grant_type", "temporarily_unavailable", "server_error")
    fun from(status: Int, body: String): SignInHttpFailure {
      val value = try { JSONObject(body).opt("error") } catch (_: Exception) { null }
      val code = if (value is String) value else (value as? JSONObject)?.optString("code")
      return SignInHttpFailure(status, code?.takeIf { it in allowed })
    }
  }
}
internal class SignInFailure(val stage: SignInStage, failure: Exception) : Exception() {
  val diagnostic: String
  init {
    val reason = when (failure) {
      is SignInValidationFailure -> failure.check.name
      is SignInHttpFailure -> "HTTP${failure.status}" + (failure.oauth?.let { "-" + it.uppercase(java.util.Locale.ROOT) } ?: "")
      is SocketTimeoutException -> "TIMEOUT"
      is UnknownHostException -> "DNS"
      is SSLException -> "TLS"
      is IOException -> "NETWORK"
      else -> "VALIDATION"
    }
    diagnostic = "ACC-SIGNIN-${stage.name}-$reason"
  }
  fun userMessage(): String {
    val action = when (stage) {
      SignInStage.PERMISSIONS -> "ChatGPT plan permission could not be verified. Retry sign-in and allow plan access if offered."
      SignInStage.IDENTITY -> "Account verification did not pass. Check the device date and time, then retry sign-in."
      SignInStage.STORAGE -> "The verified account could not be saved on this device. Existing saved data was preserved."
      else -> "Sign-in stopped at ${stage.name.lowercase(java.util.Locale.ROOT)}. Retry with Continue with ChatGPT; unfinished registrations can be reused."
    }
    return "$action Diagnostic: $diagnostic. Share only this diagnostic, never the browser address or login codes."
  }
}
internal object SignInCompletion {
  fun <T> step(stage: SignInStage, block: () -> T): T = try { block() } catch (e: Exception) { throw SignInFailure(stage, e) }

  fun run(
    register: () -> String,
    exchange: () -> JSONObject,
    loadKeys: () -> JSONObject,
    validateIdentity: (JSONObject, JSONObject) -> JSONObject,
    store: (String, JSONObject, List<String>, JSONObject) -> Unit,
  ) {
    val id = step(SignInStage.REGISTRATION, register)
    val response = step(SignInStage.TOKEN, exchange)
    val scopes = step(SignInStage.PERMISSIONS) { OAuthCore.scopes(response) }
    val keys = step(SignInStage.KEYS, loadKeys)
    val claims = step(SignInStage.IDENTITY) { validateIdentity(response, keys) }
    step(SignInStage.STORAGE) { store(id, response, scopes, claims) }
  }
}
