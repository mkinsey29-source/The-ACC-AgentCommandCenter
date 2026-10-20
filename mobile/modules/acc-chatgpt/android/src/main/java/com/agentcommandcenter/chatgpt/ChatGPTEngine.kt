package com.agentcommandcenter.chatgpt

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.json.JSONArray
import org.json.JSONObject
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.KeyStore
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicReference
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

// Credentials stay in Android native memory and a Keystore-encrypted private record.
// Nothing below logs URLs, callback query strings, tokens, response errors or prompts.
internal class ChatGPTEngine(private val context: Context) {
  private val lock = Any()
  private val client = OkHttpClient.Builder().retryOnConnectionFailure(false).followRedirects(false).followSslRedirects(false)
    .connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).callTimeout(180, TimeUnit.SECONDS).build()
  private val prefs = context.getSharedPreferences("acc.chatgpt.v1", Context.MODE_PRIVATE)
  private var attempt: Attempt? = null
  private var loginMessage = ""
  private val activeChat = AtomicReference<ChatCall?>(null)
  private val listenerExecutor = Executors.newSingleThreadExecutor()
  private data class Attempt(val id: String, val server: ServerSocket, val state: String, val nonce: String, val verifier: String, val previous: String?, val subject: String?, val until: Long)
  private data class ChatCall(val id: String, @Volatile var cancelled: Boolean = false, @Volatile var connection: Call? = null)
  private data class Reply(val status: Int, val text: String)

  private fun key(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getKey("acc.chatgpt.v1", null) as? SecretKey)?.let { return it }
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    generator.init(KeyGenParameterSpec.Builder("acc.chatgpt.v1", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
      .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setRandomizedEncryptionRequired(true).build())
    return generator.generateKey()
  }
  private fun load(): JSONObject {
    val raw = prefs.getString("encrypted", null) ?: return JSONObject().put("accounts", JSONArray())
    val encrypted = JSONObject(raw)
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, OAuthCore.decode(encrypted.getString("iv"))))
    return JSONObject(String(cipher.doFinal(OAuthCore.decode(encrypted.getString("data"))), Charsets.UTF_8))
  }
  private fun save(data: JSONObject) {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, key())
    val encrypted = JSONObject().put("iv", OAuthCore.b64(cipher.iv)).put("data", OAuthCore.b64(cipher.doFinal(data.toString().toByteArray(Charsets.UTF_8))))
    check(prefs.edit().putString("encrypted", encrypted.toString()).commit())
  }
  private fun account(data: JSONObject, id: String): JSONObject {
    val accounts = data.getJSONArray("accounts")
    return (0 until accounts.length()).map { accounts.getJSONObject(it) }.single { it.getString("id") == id }
  }
  private fun selected(): JSONObject = synchronized(lock) {
    val data = load(); account(data, data.getString("active"))
  }
  fun status(): String = synchronized(lock) {
    val data = load(); val accounts = data.getJSONArray("accounts"); val result = JSONArray()
    for (i in 0 until accounts.length()) {
      val a = accounts.getJSONObject(i)
      result.put(JSONObject().put("id", a.getString("id")).put("label", a.getString("label")).put("signedIn", a.has("access_token")))
    }
    JSONObject().put("accounts", result).put("active", data.optString("active"))
      .put("signingIn", attempt != null).put("message", loginMessage).toString()
  }
  fun choose(id: String) = synchronized(lock) {
    check(activeChat.get() == null && attempt == null)
    val data = load(); account(data, id); data.put("active", id); save(data)
  }
  fun begin(id: String?): String = synchronized(lock) {
    check(attempt == null && activeChat.get() == null)
    val data = load()
    if (!data.has("host")) { data.put("host", "urn:uuid:" + UUID.randomUUID()); save(data) }
    val old = id?.let { account(data, it) }
    if (old == null) check(data.getJSONArray("accounts").length() < 10)
    val server = ServerSocket(0, 8, InetAddress.getByName("127.0.0.1"))
    server.soTimeout = 1000
    val a = Attempt(UUID.randomUUID().toString(), server, OAuthCore.random(), OAuthCore.random(), OAuthCore.random(), old?.getString("client_id"), old?.optString("subject")?.takeIf { it.isNotBlank() }, System.currentTimeMillis() + 300000)
    attempt = a; loginMessage = "Waiting for the OpenAI sign-in page. Return to ACC after authorizing."
    val params = mutableMapOf("client_id" to (a.previous ?: "dynamic_agent_client"), "ext_agent_host_id" to data.getString("host"),
      "response_type" to "code", "redirect_uri" to "http://127.0.0.1:${server.localPort}/auth/callback", "scope" to "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
      "resource" to OAuthCore.RESOURCE, "state" to a.state, "nonce" to a.nonce, "code_challenge_method" to "S256", "code_challenge" to OAuthCore.challenge(a.verifier))
    if (a.previous == null) params["agent_name_hint"] = "ACC"
    // No ID-token hint in a URL: the browser can show its account selector.
    listenerExecutor.execute { listen(a, id) }
    JSONObject().put("url", OAuthCore.ISSUER + "/api/accounts/authorize?" + OAuthCore.form(params)).toString()
  }
  fun cancelSignIn() = synchronized(lock) {
    attempt?.server?.close(); attempt = null; loginMessage = "Sign-in cancelled."
  }
  private fun line(socket: Socket, budget: IntArray): String {
    val bytes = ArrayList<Byte>()
    while (true) {
      val value = socket.getInputStream().read()
      require(value != -1 && --budget[0] >= 0)
      if (value == 10) break
      bytes.add(value.toByte())
    }
    return bytes.toByteArray().toString(Charsets.US_ASCII).trimEnd('\r')
  }
  private fun page(socket: Socket, success: Boolean) {
    val body = if (success) "Sign-in received. Return to ACC to finish connecting." else "This sign-in request was not accepted. Return to ACC."
    val headers = "HTTP/1.1 ${if (success) "200 OK" else "400 Bad Request"}\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nContent-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\nContent-Length: ${body.toByteArray().size}\r\n\r\n"
    socket.getOutputStream().write((headers + body).toByteArray(Charsets.UTF_8))
  }
  private fun listen(a: Attempt, accountId: String?) {
    try {
      while (System.currentTimeMillis() < a.until && synchronized(lock) { attempt === a }) {
        val socket = try { a.server.accept() } catch (_: java.net.SocketTimeoutException) { continue }
        var callback: Pair<String, String>? = null
        socket.use {
          try {
            it.soTimeout = 2000
            val budget = intArrayOf(16384)
            val request = line(it, budget).split(' ')
            require(request.size == 3 && request[0] == "GET" && request[2] == "HTTP/1.1")
            val headers = mutableMapOf<String, String>()
            while (true) { val h = line(it, budget); if (h.isEmpty()) break; val parts = h.split(':', limit = 2); require(parts.size == 2); val name = parts[0].lowercase(); require(!headers.containsKey(name)); headers[name] = parts[1].trim() }
            require(headers["host"] == "127.0.0.1:${a.server.localPort}" && !headers.containsKey("origin"))
            val uri = java.net.URI(request[1])
            val q = OAuthCore.query(uri.rawQuery ?: "")
            if (uri.scheme == null && uri.rawAuthority == null && uri.rawPath == "/auth/callback" && q["state"] == a.state && q.containsKey("error")) {
              page(it, false)
              synchronized(lock) { if (attempt === a) { attempt = null; loginMessage = "OpenAI sign-in was declined. You can try again." } }
              a.server.close(); return
            }
            callback = OAuthCore.callback(request[1], a.server.localPort, a.state, a.previous)
            page(it, true)
          } catch (_: Exception) { try { page(it, false) } catch (_: Exception) {} }
        }
        val accepted = callback ?: continue
        a.server.close()
        // Retain an issued registration even if exchange fails (for example invalid_grant).
        // It has no tokens or trusted identity and does not replace an active account.
        val registrationId = synchronized(lock) {
          if (attempt !== a) return
          if (accountId != null) accountId else {
            val data = load(); val accounts = data.getJSONArray("accounts")
            val pending = JSONObject().put("id", UUID.randomUUID().toString()).put("client_id", accepted.second).put("subject", "")
            pending.put("label", "Unfinished sign-in · " + pending.getString("id").take(6)); accounts.put(pending); save(data)
            pending.getString("id")
          }
        }
        val response = json(OAuthCore.ISSUER + "/api/accounts/oauth/token", mapOf("grant_type" to "authorization_code", "client_id" to accepted.second, "code" to accepted.first,
          "code_verifier" to a.verifier, "redirect_uri" to "http://127.0.0.1:${a.server.localPort}/auth/callback", "resource" to OAuthCore.RESOURCE))
        val scopes = OAuthCore.scopes(response)
        val claims = OAuthCore.validateId(response.getString("id_token"), json(OAuthCore.ISSUER + "/.well-known/jwks.json"), accepted.second, a.nonce, a.subject)
        synchronized(lock) {
          if (attempt !== a) return
          val data = load()
          val target = account(data, registrationId)
          target.put("client_id", accepted.second).put("subject", claims.getString("sub"))
            .put("label", (claims.optString("email").ifBlank { "ChatGPT account" }).take(160) + " · " + target.getString("id").take(6))
          applyTokens(target, response, scopes)
          data.put("active", target.getString("id")); save(data)
          attempt = null; loginMessage = "ChatGPT account connected. Choose a model and send a message to verify access."
        }
        return
      }
      synchronized(lock) { if (attempt === a) { attempt = null; loginMessage = "Sign-in timed out. Tap Continue with ChatGPT to try again." } }
    } catch (_: Exception) {
      synchronized(lock) { if (attempt === a) { attempt = null; loginMessage = "Sign-in could not be completed. Select any unfinished registration below to retry. Your existing accounts were preserved." } }
    } finally { try { a.server.close() } catch (_: Exception) {} }
  }
  private fun applyTokens(target: JSONObject, response: JSONObject, scopes: List<String>) {
    target.put("access_token", response.getString("access_token")).put("refresh_token", response.getString("refresh_token"))
      .put("scopes", JSONArray(scopes)).put("expires_at", System.currentTimeMillis() + response.getLong("expires_in") * 1000)
    if (response.has("id_token")) target.put("id_token", response.getString("id_token"))
  }
  // The module serializes account, refresh and chat operations on one worker.
  private fun freshAccount(): JSONObject {
    val a = selected()
    check(a.has("access_token"))
    if (a.getLong("expires_at") > System.currentTimeMillis() + 90000) return a
    val response = json(OAuthCore.ISSUER + "/api/accounts/oauth/token", mapOf("grant_type" to "refresh_token", "client_id" to a.getString("client_id"), "refresh_token" to a.getString("refresh_token"), "resource" to OAuthCore.RESOURCE))
    val scopes = OAuthCore.scopes(response)
    if (response.has("id_token")) OAuthCore.validateId(response.getString("id_token"), json(OAuthCore.ISSUER + "/.well-known/jwks.json"), a.getString("client_id"), null, a.getString("subject"))
    synchronized(lock) {
      val data = load(); val target = account(data, a.getString("id")); applyTokens(target, response, scopes); save(data)
      return target
    }
  }
  fun signOut(id: String): String {
    check(activeChat.get() == null)
    val a = synchronized(lock) { check(attempt == null); account(load(), id) }
    var revoked = !a.has("refresh_token")
    if (!revoked) {
      try {
        val metadata = json(OAuthCore.ISSUER + "/.well-known/openid-configuration")
        val endpoint = metadata.getString("revocation_endpoint")
        require(endpoint == OAuthCore.ISSUER + "/api/accounts/oauth/revoke")
        revoked = request(endpoint, OAuthCore.form(mapOf("token" to a.getString("refresh_token"), "token_type_hint" to "refresh_token", "client_id" to a.getString("client_id"))), "application/x-www-form-urlencoded").status == 200
      } catch (_: Exception) {}
    }
    synchronized(lock) {
      val data = load(); val target = account(data, id)
      listOf("access_token", "refresh_token", "id_token", "expires_at", "scopes").forEach { target.remove(it) }; save(data)
    }
    return if (revoked) "Signed out. Saved chats stay on this device." else "Signed out on this device. Remote revocation was not confirmed; disconnect ACC in ChatGPT Settings."
  }
  fun models(): String {
    val a = freshAccount(); val data = json(OAuthCore.RESOURCE + "/models", token = a.getString("access_token"))
    val source = data.getJSONArray("models"); val models = JSONArray()
    for (i in 0 until source.length()) {
      val m = source.getJSONObject(i)
      if (m.optString("visibility") == "list") {
        val slug = m.getString("slug"); val name = m.getString("display_name")
        require(slug.length in 1..160 && name.length in 1..240)
        models.put(JSONObject().put("id", slug).put("name", name))
      }
    }
    check(models.length() in 1..200)
    return JSONObject().put("accountId", a.getString("id")).put("models", models).toString()
  }
  fun cancelChat(id: String) { activeChat.get()?.takeIf { it.id == id }?.let { it.cancelled = true; it.connection?.cancel() } }
  fun chat(id: String, accountId: String, model: String, rawMessages: String): String {
    require(id.matches(Regex("[A-Za-z0-9_-]{16,80}")) && model.length in 1..160)
    val messages = JSONArray(rawMessages); require(messages.length() in 1..39 && messages.length() % 2 == 1)
    var length = 0
    val input = JSONArray()
    for (i in 0 until messages.length()) {
      val m = messages.getJSONObject(i); val role = m.getString("role"); val text = m.getString("text")
      require(role == if (i % 2 == 0) "user" else "assistant"); require(text.isNotBlank() && text.length <= 100000)
      length += text.length; input.put(JSONObject().put("role", role).put("content", text))
    }
    require(length <= 50000)
    val call = ChatCall(id); check(activeChat.compareAndSet(null, call))
    try {
      val a = freshAccount(); require(a.getString("id") == accountId)
      val body = JSONObject().put("model", model).put("input", input).put("store", false).put("stream", true).toString()
      val conn = connection(OAuthCore.RESOURCE + "/responses", body, "application/json", a.getString("access_token"))
      call.connection = conn
      if (call.cancelled) error("Cancelled")
      val response = conn.execute()
      try {
      check(response.code == 200)
      require(response.header("Content-Type")?.startsWith("text/event-stream") == true)
      val reader = requireNotNull(response.body).charStream().buffered()
      val deadline = System.currentTimeMillis() + 180000
      val reply = reader.use { OAuthCore.readStream(it) { check(!call.cancelled && System.currentTimeMillis() < deadline) } }
      check(!call.cancelled)
      return reply
      } finally { response.close() }
    } finally { call.connection?.cancel(); activeChat.compareAndSet(call, null) }
  }
  private fun connection(url: String, body: String?, contentType: String, token: String?): Call {
    require(url.startsWith(OAuthCore.ISSUER + "/") || url.startsWith(OAuthCore.RESOURCE + "/"))
    val request = Request.Builder().url(url).header("Accept", "application/json, text/event-stream")
    if (token != null) request.header("Authorization", "Bearer $token")
    if (body != null) request.post(body.toRequestBody(contentType.toMediaType()))
    return client.newCall(request.build())
  }
  private fun request(url: String, body: String? = null, contentType: String = "application/json", token: String? = null): Reply {
    connection(url, body, contentType, token).execute().use { response ->
      if (response.code !in 200..299) return Reply(response.code, "")
      val source = requireNotNull(response.body).source()
      require(!source.request(1000001))
      return Reply(response.code, source.readUtf8())
    }
  }
  private fun json(url: String, values: Map<String, String>? = null, token: String? = null): JSONObject {
    val r = request(url, values?.let { OAuthCore.form(it) }, if (values != null) "application/x-www-form-urlencoded" else "application/json", token)
    check(r.status in 200..299)
    return JSONObject(r.text)
  }
  fun close() { cancelSignIn(); activeChat.get()?.let { cancelChat(it.id) }; listenerExecutor.shutdownNow() }
}
