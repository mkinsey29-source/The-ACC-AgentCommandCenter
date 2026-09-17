# Conversation-first ACC

ACC 0.3 makes natural language the primary input. Technical task creation remains available under **Advanced task controls**. The same saved conversation can be handled by a desktop orchestrator through MCP, a configured online command adapter, or a local adapter.

## Everyday operation

1. Speak through ChatGPT Remote as usual. Its connected desktop session uses the local ACC MCP bridge. ACC does not add another phone connection.
2. At the computer, type in ACC or press **Record voice**. Text is saved in a browser outbox before sending and then stored in ACC's SQLite database. Recordings use the same outbox and are saved under the host state directory before transcription.
3. The orchestrator replies in plain language. Brainstorming stays discussion; requested work produces linked tasks using the configured implementation, review, and coordination roles.
4. The work plan shows workers, progress, output, review, and next steps. Each generated task links back to the original messages. Revising work preserves the prior requirement revision.
5. Offline work remains in the same database. A returning desktop orchestrator reads messages and current task results before making another decision.

Intent classification is performed by the selected model. The engine enforces the declared intent and references, but cannot prove a model interpreted ambiguous language correctly. An uncertain model should ask a question and return no actions. There is no keyword rule that silently treats every idea as permission to execute.

## One-time routing

Copy `examples/hermes-agents.json` to your host configuration, configure its Hermes profiles, and start ACC with `--agents` pointing to it. The example includes an online orchestrator, a local coordinator, online implementation/review, and separate local implementation/review adapters. Profiles must actually point at the intended online providers or local model server; `local: true` is an operator declaration, not automatic detection.

The **Orchestrator settings** panel changes routing without editing a task form. Settings are persisted per ACC state directory. File configuration supplies initial defaults; thereafter the saved UI settings win. Pick default roles once. The ordinary message composer has no agent, task type, or command fields.

- An active external MCP lease has priority over automatic conversation handling.
- Without an external owner, the preferred adapter handles pending messages.
- A failed or timed-out planner can fall back to the configured local planner after its supervised process has ended and Git-visible project files are verified unchanged. Local fallback marks the newly requested workflow as offline, selecting permitted local workers.
- The next new conversation turn tries the preferred online adapter again. Existing work is not duplicated, and already running workers retain ownership until they finish or are stopped.
- **Local only** forces local planning and local workflow roles. **Handle saved messages automatically** can be disabled to preserve messages for a later external session.
- If no configured adapter is available, the messages remain saved. If a result is malformed, project files changed, or execution failed after possible edits, ACC holds the affected work for inspection.

This detects actual adapter failures; it does not infer cloud-provider availability from Wi-Fi status. A hanging planner has a 180-second deadline before a permitted local fallback. Both local workers and the local model runtime must be installed/running for fully offline execution. The model process is invoked per turn; ACC does not install or start a model server.

## Desktop MCP handoff

Use the stdio bridge already described in `HERMES-CONNECTOR.md`. The desktop host invokes these tools:

| Step | Tool | Result |
| --- | --- | --- |
| Read prior state | `acc_state`, `acc_conversation_read` | Current work, outcomes, original messages; paginate messages with their `seq` cursor |
| Claim decision ownership | `acc_conversation_claim` | A 120-second lease and shared context |
| Capture a remote instruction | `acc_conversation_send` | Exact user wording, source, stable message ID |
| Refresh/keep ownership | `acc_conversation_renew` | Captures new pending messages and extends the lease by 120 seconds |
| Commit the decision | `acc_conversation_complete` | Reply, requested tasks, consumed messages, and receipt saved atomically |
| Yield without a decision | `acc_conversation_release` | Messages remain pending for the next owner |

Claim before saving a new remote instruction, then renew to include it in the captured batch. Renew while reasoning. The result must follow the contract returned in context; create/revise actions cite the pending user message IDs. Task revision actions also include the current revision. A completed result can be retried unchanged using the same token; ACC returns its receipt without creating tasks again. A different result for that token, an expired token, or an outdated task revision is rejected.

The connected orchestrator must make these calls. ACC cannot automatically copy unrelated ChatGPT conversations, wake a dormant ChatGPT session, or install an MCP server into the desktop app. The remote app continues using its own built-in desktop connection. A local agent can handle ACC input while no external session holds the conversation lease.

Leases cover conversation decisions, not arbitrary direct filesystem access by other applications. The supervised runner and workspace lock serialize ACC's own workers. While an external lease is active, ACC waits before launching another worker. If a worker already owns the project, claim waits until it completes; messages can still be saved immediately.

## Local voice

The browser records audio with `MediaRecorder`; it does not use a browser cloud speech-recognition service. Recordings are limited to two minutes in the UI and 10 MiB at the server. Microphone permission and a supported local browser are required. Pending recordings survive browser reloads through IndexedDB. Keep the browser's site storage until pending messages/recordings reach the host.

Configure a local transcription command in the same agents JSON:

```json
{
  "transcription": {
    "argv": [
      "/absolute/path/to/python",
      "/absolute/path/to/The-ACC-AgentCommandCenter/acc/transcribe.py",
      "--model-dir", "/absolute/path/to/downloaded-whisper-model",
      "--audio", "{audio_file}",
      "--output", "{text_file}"
    ]
  }
}
```

This is an additional top-level entry beside `agents` and `conversation`. On Windows, use your Python executable and Windows paths; escape backslashes in JSON or use forward slashes.

The included `acc/transcribe.py` adapter uses the optional **faster-whisper** package. Install it into the Python environment named above while online (`python -m pip install faster-whisper`) and download a compatible converted model in advance. The model directory must contain `model.bin`, `config.json`, and `tokenizer.json`. The adapter requires a local directory, disables Hugging Face online access, defaults to CPU/int8, and writes UTF-8 text atomically. Its package API follows the [official faster-whisper usage documentation](https://github.com/SYSTRAN/faster-whisper#usage). ACC itself still has no required third-party Python package.

An alternative local transcriber can use the same argv placeholders. It must stay attached, exit nonzero on failure, and write the completed transcript to `{text_file}`. Do not point this local-only configuration at a cloud transcription command.

Transcription uses the supervised runner and waits for current work. A failed recording remains available under the state directory and can be retried from the conversation panel. Recognized speech becomes a user message with source `local voice`; the audio remains available for comparison. Transcription correctness depends on the installed speech model.

## Persistence and recovery

Original user text is stored verbatim. Task creation/revision, reply, message acknowledgement, and the turn receipt share a database transaction. Browser retries reuse a stable message or recording ID. New messages arriving during a turn remain pending for the next turn.

A crash during a local runner is surfaced as interrupted work. The conversation panel exposes its process ID and recovery controls. Inspect the previous process and descendants, acknowledge recovery, then retry saved messages. ACC does not assume an unobserved child has stopped. This is the same recovery rule used for code workers.

The database and browser outbox are local storage, not an encrypted backup service. Browser outboxes are separated by project path on the current local origin; use a stable port and keep the same project path when recovering queued input.

## What was exercised

`tests/test_conversation.py` uses scripted planner/worker responses with real SQLite transactions, subprocesses, authenticated HTTP, and MCP dispatch. It exercises an online failure → local plan → local implementation/review → desktop context handoff → next online turn, plus duplicate delivery, rollback, ideas with no execution, lease expiry, old revisions, concurrent message arrival, restart recovery, planner edits, and recording/transcript persistence.

The optional speech adapter's API contract is tested with a stub module, not recorded human speech. Browser microphone capture, layout/click behavior, real Hermes/provider/model quality, native Windows process handling, and the user's desktop MCP installation remain host checks. No paid inference was used in this verification.
