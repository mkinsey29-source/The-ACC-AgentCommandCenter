package com.agentcommandcenter.chatgpt

import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.util.concurrent.Executors

class AccChatGPTModule : Module() {
  private val worker = Executors.newSingleThreadExecutor()
  private val engine by lazy { ChatGPTEngine(requireNotNull(appContext.reactContext).applicationContext) }
  private fun run(promise: Promise, message: String, block: () -> Any?) {
    worker.execute {
      try { promise.resolve(block()) }
      catch (error: Exception) { promise.reject("ACC_CHATGPT", (error as? ChatFailure)?.userMessage() ?: message, null) }
    }
  }
  override fun definition() = ModuleDefinition {
    Name("AccChatGPT")
    AsyncFunction("status") { promise: Promise ->
      try { promise.resolve(engine.status()) } catch (_: Exception) { promise.reject("ACC_STORAGE", "ChatGPT account storage could not be opened. Its saved data was preserved.", null) }
    }
    AsyncFunction("beginSignIn") { id: String?, promise: Promise -> run(promise, "Could not start sign-in. Finish or cancel the current request and retry.") { engine.begin(id) } }
    AsyncFunction("acknowledgePlanUse") { promise: Promise -> run(promise, "Could not save this preference.") { engine.acknowledgePlanUse(); engine.status() } }
    AsyncFunction("cancelSignIn") { engine.cancelSignIn() }
    AsyncFunction("selectAccount") { id: String, promise: Promise -> run(promise, "Could not switch accounts. Finish or cancel the current request first.") { engine.choose(id); engine.status() } }
    AsyncFunction("signOut") { id: String, promise: Promise -> run(promise, "Could not sign out. Finish or cancel the current request first.") { engine.signOut(id) } }
    AsyncFunction("models") { promise: Promise -> run(promise, "Could not load models for this account. Check your connection or sign in again. No API billing fallback was attempted.") { engine.models() } }
    AsyncFunction("chat") { id: String, accountId: String, model: String, messages: String, promise: Promise ->
      run(promise, "The response did not complete. It may have used some account allowance. Check your connection, sign-in and ChatGPT plan limits. ACC has not retried or switched billing.") { engine.chat(id, accountId, model, messages) }
    }
    AsyncFunction("cancelChat") { id: String -> engine.cancelChat(id) }
    OnDestroy { engine.close(); worker.shutdownNow() }
  }
}
