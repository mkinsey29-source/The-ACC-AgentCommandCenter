# Implementation status — September 20, 2026

## Current product work: integration queue and shared memory (v0.6)

ACC now has a capability-routed provider catalog for DeepSeek Harness, Muse Spark 1.3 Contributor, Gemini/Nano Banana, Agent 3D Studio (img2threejs), Aura, TypeSafe Jev (Choice, Score, and Noul), RunPod, and the local Blender/Unity pipeline. Muse Contributor is policy-limited to public data and an isolated repository because its content may be used for provider training. Jobs persist in SQLite with priority, idempotent submission, cost budgets, offline blocking, explicit retry/cancel, expiring leases, and monotonic fencing. Workers report structured results, costs, and artifact references without placing provider credentials in ACC.

Shared project memory is versioned and review-gated. Agents can search active memory and propose new facts, decisions, conventions, and lessons; accepting a new version supersedes the previous active version. The dashboard, HTTP API, and MCP bridge expose both systems.

Agent 3D Studio has its first connector: `acc/agent3d.py` claims a queued job, asks a coding-agent CLI to build the asset with this project's own `img2threejs` skill chain, hashes the artifacts the agent declares, and reports success or failure back to the job queue. See [the connector guide](AGENT-3D-STUDIO.md) for the job contract and prerequisites.

A capability job can now also serve as a managed workflow's **implementer**: a new `kind: 'job'` agent type submits the job instead of spawning a subprocess, and the job's own finish report stands in for the result.json a spawned CLI would have written, so the existing independent-review, snapshot, and publish pipeline applies to job-produced artifacts (a 3D asset, for example) the same way it already does for code. Job-backed adapters are restricted to the implementer role; the reviewer and coordinator stay ordinary adapters. A coordinator restart marks an outstanding job-backed step interrupted via the same blanket recovery rule used for subprocess-backed steps.

Stopping a job-backed step now mirrors offline mode's own precedent instead of discarding work it cannot interrupt: `stop()` defaults to letting a claimed job run to completion (disabling further scheduling but still accepting a success it produces) and only escalates to an actual cancellation — via a new cooperative `cancel_requested` flag that `agent3d.py`'s lease-renewal loop notices and acts on by killing the local process it owns — when explicitly forced (`stop(force=True)`) or on timeout, which always forces.

The Hermes CLI adapter (`acc/hermes.py`) has been audited against the DeepSeek-adapter concerns noted in `PROJECT-PLAN.md`: process cleanup, prompt-delivery timeout, failure exit code, and instruction loading were already solid; a real narrower gap (a mid-stream stdout failure leaving Hermes running unsupervised until ACC's own outer timeout eventually reached it) was found and fixed. Windows process-tree termination remains unverified for lack of a Windows host, unchanged from before.

DeepSeek (or any model Ollama serves locally) can now run as a workflow adapter without Hermes: a new `driver: 'ollama'` agent type in `agents.json` (`{"id": "deepseek-local", "driver": "ollama", "model": "deepseek-r1", "host": "http://127.0.0.1:11434"}`) talks straight to a local Ollama server's HTTP API. No subprocess of its own to supervise past ACC's own spawned adapter process; availability is a live reachability probe of the configured host, not a CLI-presence check, since this driver has no process of its own on the host to look for. It strips a leading `<think>...</think>` reasoning trace before parsing the required JSON result, since DeepSeek-R1 emits one by default even when told not to. The prompt-building and result-parsing logic is now shared with the Hermes adapter (`acc/worker_prompt.py`) rather than duplicated, closing off the kind of contract-drift bug that already happened once between the subprocess and job-backed paths.

A `driver: 'dsh'` agent type runs DeepSeek Harness (`@deepseek-ai/dsh`) as a workflow adapter, spawned and supervised the same way the Hermes adapter is (`acc/deepseek_harness.py`, sharing `worker_prompt.py`'s prompt-building and `<think>`-stripping result extraction with the other two drivers). Its contract was verified by downloading and statically reading the actual shipped `@deepseek-ai/dsh` and `@deepseek-ai/dsh-headless` packages' own published TypeScript type declarations rather than trusting third-party tutorials found first: those describe a `--json` NDJSON event stream that does not exist anywhere in the real shipped CLI parser. The real, confirmed contract is `dsh --profile headless "<task>"` printing the final assistant text to stdout and exiting 0 for a completed turn, 1 for anything else.

A `driver: 'deepastra'` agent type runs DeepAstra (`github.com/ItsssssJack/DeepAstra`, an unofficial single-maintainer wrapper that reconfigures OpenAI's Codex CLI to call DeepSeek's or OpenRouter's API) as a workflow adapter (`acc/deepastra.py`). Reading its `launch.py` source directly, rather than trusting summaries, surfaced two real gaps neither of the other two drivers has: `launch.py` spawns its `codex exec` child with `start_new_session=True` and no signal handler, so an external SIGTERM (what ACC's `stop_tree()` sends on stop/cancel/timeout) kills `launch.py` before its own cleanup runs, orphaning a real, billed API call; and its `run-status.json` never carries the model's final answer text, only usage/tool-event counts. Both are fixed on ACC's side without patching the upstream script: a SIGTERM handler in `acc/deepastra.py` walks `/proc/<launch.py-pid>/task/<pid>/children` to find and `killpg` the detached `codex` process directly, and the result is recovered from the last `item.completed`/`agent_message` event in the raw Codex JSONL log `launch.py` already writes under `--log-dir` — a schema confirmed against Codex's own `codex-rs/exec/src/exec_events.rs` source, not a blog post. `tests/test_deepastra_adapter.py` proves the orphan-cleanup fix against a real detached OS process (a `start_new_session=True` child writing a heartbeat file that a `_kill_orphans` call is shown to actually stop), not a mock.

All three subprocess-spawning drivers (`hermes.py`, `deepseek_harness.py`, `deepastra.py`) now launch their worker CLI with a credential-filtered environment (`worker_prompt.subprocess_env()`, shared like the prompt/result contract): anything named like `*KEY*`/`*TOKEN*`/`*SECRET*` is stripped from what the subprocess inherits, so a model with shell/tool access can't read and exfiltrate ACC's own unrelated secrets. A real DeepAstra provider credential still reaches `launch.py` explicitly via `--key-file` rather than an ambient `*_API_KEY` variable. Considered and deferred for now: an OS-level workspace-write sandbox (mirroring Codex CLI's `-s workspace-write`) around the `dsh` subprocess via `bwrap` — real and buildable, but a bigger, Linux-only addition needing its own dependency-availability handling and process-supervision verification (the sandboxed child's pid isn't the pid ACC sees), not bundled into this pass.

A `driver: 'gemini'` agent type (`acc/gemini.py`, mirroring `ollama.py`) calls the plain developer-facing Gemini Interactions API directly over HTTP (`POST generativelanguage.googleapis.com/v1beta/interactions`, `x-goog-api-key` auth, `{"model", "input"}` body, synchronous `output_text` response) — verified against Google's own current 2026 sources rather than assumed continuity with older Gemini API knowledge, and deliberately not the two other, materially different 2026 "Gemini" surfaces also found during research: the GCP Enterprise/Vertex variant (project id + OAuth bearer tokens) and the separate, fully Google-hosted "Managed Agents" remote-sandbox product, neither of which fits ACC's local-subprocess/HTTP driver model.

A `driver: 'gemini-cli'` agent type wrapping the plain `gemini` binary was built, then removed: Google is retiring that CLI for individual/free-tier accounts (2026-06-18, already past), returning HTTP 410 rather than working; only enterprise/paid Gemini access keeps it functional, so shipping it as a first-class driver would mislead most operators. `driver: 'antigravity'` (`acc/antigravity.py`) replaces it, wrapping Google's real, confirmed successor tool, the Antigravity CLI (`agy`, `google-antigravity/antigravity-cli`): `agy -p "<task>" --output-format json --dangerously-skip-permissions` prints one JSON object (`status`, `response`, `usage`, optional `structured_output`) and exits nonzero on failure, alongside a structured `AGY_ERROR: {...}` line on stderr (captured separately from stdout, not merged, so it can't corrupt the JSON parse). Checking agy's own currently-open issue tracker (not tutorials) surfaced real reliability bugs matching ACC's own usage shape — a large, JSON-heavy prompt can make headless mode report `status: SUCCESS` with an empty `response` and exit 0 (issues #840, #794), and a launched background command can still be running after the turn reports itself complete (#1044, an internal agy bug with no externally reachable process to clean up the way DeepAstra's detached `codex` child could be). `acc/antigravity.py` treats exit 0 and `status: SUCCESS` as necessary but not sufficient, and raises a distinct error naming those upstream issues when the response is blank rather than treating it as a silent no-op. Authentication for headless use wasn't pinned to one certain mechanism (conflicting `ANTIGRAVITY_API_KEY` vs. `GEMINI_API_KEY`-plus-config-file claims across sources), so the driver reads a local credential-reference file and exports it under an operator-chosen env var name (`--api-key-env`) rather than assuming one fixed path.

All three of `hermes.py`, `deepseek_harness.py`, `deepastra.py`, and now `antigravity.py` share the credential-filtered subprocess environment (`worker_prompt.subprocess_env()`).

The existing "Gemini/Nano Banana" entry in the capability-job provider catalog (see the top of this document) is a separate, still-unimplemented placeholder for a future image-generation feature — unrelated to these `kind: 'model'` workflow drivers.

Automated verification passes 156 tests, with one optional MCP SDK interoperability test skipped when its dependency is absent. Python compilation, JavaScript syntax, and shell syntax checks pass. These tests use local scripted clients, including a scripted stand-in for the Agent 3D Studio coding-agent CLI and a simulated worker claiming/finishing jobs directly against the integration queue for the job-backed-implementer path; one test (`test_agent3d.py`) drives the cooperative-cancellation mechanism end to end across real process boundaries (a real worker process, a real killed subprocess) rather than a scripted stand-in. Live provider calls, a live coding-agent run against a real reference image, RunPod GPUs, Blender, Unity, and native browser interaction remain unverified. The intermittent failure previously seen in `test_changing_reviewer_invalidates_previous_review` is root-caused and fixed: it was a test-harness race (a raw, lock-bypassing fixture write racing against `_collect()`'s still-in-flight trailing save), not a product bug — any real caller goes through a Coordinator method and already blocks on the same lock. 0/50 isolated runs and 0/5 full-suite runs failed after the fix.

## Current product work: archive, global mode, and recovery

ACC now provides task-number/text/status/agent archive search, explicit Markdown plus SQLite backup export, and one authoritative project-wide Online/Offline setting. Offline blocks new cloud-model starts across direct tasks, managed workflows, and local conversation routing; a supervised cloud step already running is allowed to reach a safe boundary without introducing another writer.

Unavailable workers can be replaced only after the active writer has stopped. ACC creates a new numbered recovery task containing the original requirement, current revision/status/phase, baseline and current Git facts, changed files, recent runs, last report, failure reason, and an inspection warning. The UI keeps these operations under contingency controls.

Each managed implementation, independent-review, and coordination run also creates its own numbered workflow-step record linked to the parent task. These records are visible and searchable but cannot be launched or scheduled independently.

The Antigravity integration is documented as an MCP interactive-client contract. Live host discovery and any future background adapter remain laptop checks because no supported installed noninteractive Antigravity CLI/API has been demonstrated.

## Current product work: task identity and command-center views

User-visible tasks now receive permanent ACC-wide sequential numbers while retaining UUID API keys. Existing state databases migrate in creation order, and internal planner/transcription runs do not consume numbers. The dashboard has linked Tasks and Agents views; selecting a task from either opens the same task detail and history. Task numbers also appear in conversation links, dependency choices, events, publication titles, PR descriptions, and commit trailers.

Searchable archive/export, globally numbered child/recovery tasks, the project-level Online/Offline switch, and the full replacement dialog remain follow-up work.

## Current follow-up: laptop readiness (v0.4)

Saved Linux/Windows launchers, setup diagnostics and MCP configuration generation are implemented. The dashboard now shows GitHub PR/commit activity, stale connection state, and a publication preview. Reviewed managed tasks can be committed and pushed to the configured source branch, with a PR created or reused. No automatic merge is provided. Local Git remains observed every second; GitHub refresh defaults to every 30 seconds and supports manual refresh.

Managed role changes are persisted and applied after the current step. The replacement gets retained files/history; replacing a reviewer invalidates the previous verdict. Priority and dependency scheduling are implemented. Interrupted process recovery requires inspection and cancels pending switches. These controls are available through both the UI and MCP.

See [laptop setup](LAPTOP-SETUP.md), [API controls](API.md), and [readiness evidence](evidence/laptop-readiness.json). Host authentication, real model runtimes, voice hardware, native Windows execution, and browser interaction are not established by Python tests.

## Previous follow-up: conversation and offline handoff

ACC 0.3 adds a conversation-first UI, persistent original messages, an IndexedDB browser outbox, external MCP claim/renew/complete/release tools, fenced turn ownership, and transactional reply/task/message acknowledgement. Configured online planners can fall back to local planners; new turns return to the preferred online adapter. Generated work uses the existing implementation/independent-review pipeline and retains source-message links. Technical task controls remain available as an advanced option.

Local audio capture saves recordings before a supervised transcription command. The optional faster-whisper adapter uses predownloaded model files. Interrupted internal runs are visible and recoverable. See [conversation workflow](CONVERSATION-WORKFLOW.md) for actual behavior and setup.

Verification is recorded in `docs/evidence/conversation-tests.json`. Scripted adapters exercise real process, persistence, MCP/HTTP, review, and reconnection paths. This does not claim live speech/model quality or browser/desktop host verification.

## Follow-up: live model responses through ACC

Three live session subagents supplied planning, implementation, and independent review, with the planner returning for coordination decisions. Their five actual responses passed through a transport-only relay into ACC's normal conversation/workflow engine. The task was accepted after independent review, and reconnect/replay checks preserved one completed task. See [live-agent evidence](LIVE-AGENT-HANDOFF.md). This tests live reasoning and handoffs, while provider-specific and disconnected inference remain unverified.

## Previous follow-up: Hermes coordination

Implemented `acc/hermes.py` with Hermes profile support, file-based one-shot prompts, streaming CLI output, terminal result validation, and MCP config generation. Added persistent managed workflow stages, independent review snapshots, bound results, corrections with limits, pause/resume, visible role changes, explicit offline mode, and configured local coordinator fallback. See [Hermes setup](HERMES-CONNECTOR.md).

Launch intent and result-processing state are now recovery-visible. Failed stops can be retried; shutdown retains locks if termination is unconfirmed. The dashboard retains received stream events across snapshot refreshes instead of dropping bursts outside the last 100 events.

Test evidence is recorded in `docs/evidence/hermes-connector-tests.json`. Actual subprocess/HTTP/stdio execution is tested with scripted model responses. Hermes installation, provider inference, model quality, browser interaction, and native Windows operation are not claimed verified.

## Historical: delivered in the initial PR

The local coordinator, persistent task/event database, browser dashboard, command runner, local Git observer, and stdio orchestrator bridge are implemented. The application has no third-party runtime dependency and no fake preloaded activity.

The dashboard starts with an empty task list. It can create real tasks and run real local commands, show output as it arrives, inspect local Git, stop supervised work, reassign idle tasks, and record instructions/evidence/review acceptance. Configured external worker commands receive JSON task packets.

## Evidence

- 15 automated tests passed on the hosted Linux environment before publication.
- Tests execute real subprocesses and cover output arriving before completion, failing commands, run deadlines, parent/child termination, exclusive workspace ownership, persistence/replay, interrupted-run recovery, requirement revisions, review/run mismatch, real Git edits/commits, unavailable providers, configured adapter packets, HTTP authentication/origin rejection, MCP-to-HTTP task creation, and SSE cursor replay.
- Python compilation and frontend JavaScript syntax checks passed.
- Browser click/layout verification was attempted, but Chromium is not installed and its download timed out. Do not claim browser interaction or visual acceptance from syntax checks.
- No paid provider calls were made. No live Claude/DeepSeek/Grok/local-model inference or user's native Remote MCP host was validated.
- Windows paths and termination code are authored, but native Windows behavior is unverified. POSIX process-group evidence does not establish Windows job containment.

## Deliberate first-build simplifications

The planned TypeScript interface is initially dependency-free JavaScript to make the first local build runnable without a bundler. The transport and task model remain separable for later TypeScript adoption. Local file/Git observations use one-second reconciliation rather than an OS watcher. Runner output uses direct events, independent of those observations.

Stage A is partially demonstrated with actual local command workers and a tested bridge contract. It is not fully accepted until the user's orchestrator host and one real coding-model adapter are connected and exercised.

## Remaining work

1. Configure Hermes profiles on the user's machine and execute a small live-model handoff. Validate provider login, tool permissions, model context, output contract, and cancellation.
2. Verify dashboard controls and responsive layout in a browser; validate native Windows process containment and scheduled startup.
3. Validate the new conversation fallback/return with live providers. Safe active worker reassignment and automatic recovery from partially edited failed worker runs remain separate work.
4. Connect provider-specific workers and validate live Gemini/Nano Banana, Agent 3D Studio (img2threejs), Aura, TypeSafe Jev, DeepSeek Harness, RunPod, Blender, and Unity jobs. Add native asset-aware review evidence.
5. Extend idempotency beyond the conversation/recording endpoints, add retention quotas and stronger execution isolation as needed.

Live mid-run requirement injection, automatic recovery from partial failed edits, and direct Unity/Blender control are not implemented by ACC itself. Continue using the existing game pipeline under its coordinator.

## Repository workflow

Changes use a short-lived `temporary` branch and merge to `main` through a PR. The repository deletes merged branches automatically. The full agreed direction is in `PROJECT-PLAN.md`.

## Follow-up: real sub-agent handoff exercise

A separate implementation agent and reviewer have now exercised a manually coordinated handoff, with actual ACC stdio/HTTP validation runs. The implementation's 16 tests and the reviewer's 10 methods (428 calls) passed. A deliberately broken variant failed; stale review approvals were rejected. See [the executed simulation](REVIEW-HANDOFF-SIMULATION.md) and its replay artifacts. Model-session delegation and snapshot integrity were handled externally by the orchestrator; automatic routing was pending at that point and is implemented in the Hermes follow-up above.

## Laptop readiness validation

87 tests passed with the optional MCP SDK installed. A live independent session reviewer approved the frozen changes for Linux laptop setup and host validation, including separate CRLF reproduction and GitHub regression checks. Saved setup launched the actual authenticated server. See [the evidence record](evidence/laptop-readiness.json) for the reviewed file hashes, test boundaries, and outstanding host checks. This paragraph and evidence record were added after the frozen implementation review.
