# ACC — Agent Command Center

**Continuing in a new chat:** start with [the detailed handoff](docs/HANDOFF-2026-09-16.md) for current implementation, evidence, laptop constraints and next steps.

A local project window for instructions, agent assignments, live worker output, local Git changes, and review records. Windows and Linux are the intended targets; this first implementation was exercised on Linux.

**v0.8 adds switchable orchestrator sessions.** The conversation header can select ChatGPT Remote, a coordinator-capable Claude/DeepSeek/other direct adapter, or Automatic. Switching fences an external owner immediately or waits for an active supervised decision boundary, then transfers durable history and task state. Automatic selection combines TypeSafe Jev Choice probabilities with measured reliability, cost, quality, and continuity; deterministic routing remains available without Jev.

The durable provider queue and reviewed shared project memory remain available. DeepSeek Harness, Muse Spark Contributor, Gemini/Nano Banana, Agent 3D Studio (img2threejs), Aura, TypeSafe Jev, RunPod, Blender, and Unity are represented by capability-based provider profiles. Muse Contributor jobs are limited to public data in an isolated repository. Credentials and provider-specific executors remain external to ACC.

For a saved launch from Linux, run `./start-acc.sh init --project /path/to/project`, then `./start-acc.sh`. On Windows use `start-acc.ps1` from PowerShell. `doctor` reports missing connections. Setup opens the dashboard and generates an absolute-path MCP configuration fragment.

## Run locally

Requires Python 3.10+ and Git. There are no third-party runtime dependencies or frontend build steps.

From this repository on Linux:

```sh
python3 -m acc --project /absolute/path/to/your/project
```

On Windows PowerShell:

```powershell
py -3 -m acc --project "C:\Projects\HearthandHavoc"
```

Open the local URL printed in the terminal. Its fragment contains the session token; keep that link private. The app stores the token only in the browser tab's session storage and removes it from the address bar. Runtime state is stored under your home directory's `.acc` folder, separately from the managed project.

Use `--port 8766`, `--state-dir /private/state/path`, or `--agents /private/agents.json` as needed. The service binds to `127.0.0.1`; no tunnel is needed for local use. Stop with Ctrl+C so supervised work can be stopped and recorded.

## Conversation first

Type naturally in ACC or use the desktop orchestrator through MCP. Configure agent roles once; requested work flows into implementation and independent review. Ideas stay in the conversation. Optional microphone recording uses a configured local transcriber. Messages, offline results, and handoffs are persisted so a returning orchestrator can continue without repeating assignments. See [conversation setup and behavior](docs/CONVERSATION-WORKFLOW.md).

## What works

- Create tasks with original instructions and retained requirement revisions.
- Select tasks and see assigned versus active workers, latest activity, and next step.
- Launch an explicit local command or a configured command-based worker.
- Stream actual output before the command finishes; reconnect using persisted event cursors.
- Inspect local changed files, current branch, and recent commits. Git state is reconciled once a second; runner output is streamed directly.
- Stop supervised work, preserve its files, and record a handoff packet.
- Switch managed workflow roles after the current step finishes; retain work and invalidate a replaced reviewer’s verdict. Stop-and-inspect remains available for urgent interruptions.
- Set task priorities and prerequisites; cyclic dependencies are rejected.
- View timestamped GitHub PRs and remote commits, with stale data clearly marked when offline.
- Preview accepted work, commit reviewed changes, push the configured source branch, and create or reuse its PR. Publication never merges.
- Persist tasks, runs, activity, reports, and review acceptance in SQLite.
- Block another ACC writer in the same workspace, including a second state directory under the same OS user.
- Expose task controls through a local stdio MCP bridge for a connected orchestrator.
- Route AI, asset, and rendering work through durable integration jobs with idempotency keys, budgets, offline blocking, and fenced worker leases.
- Keep versioned shared project memory; agents propose entries and a reviewer activates or rejects them.
- Label unavailable providers, failed runs, interrupted processes, and pending review honestly.

For manual tasks, an exit code of zero means **Needs review**, not approved. Managed workflows advance only with a valid structured result and use the stronger snapshot-bound acceptance checks described below. Reported acceptance records its run ID, requirement revision, and a supplied code snapshot reference. ACC does not independently prove that an external reviewer inspected that reference.

## Hermes background coordination

Use the included Hermes connector to run implementation → coordinator → independent review → coordinator, with bounded correction rounds. The dashboard has workflow assignments, pause/resume, online/offline mode, and permitted local fallbacks. ACC preserves a code snapshot and rejects stale results, modified snapshots, and acceptance without an approving review. A local coordinator can supervise cloud workers; fully offline work requires configured local workers too.

See the [complete Hermes setup and behavior guide](docs/HERMES-CONNECTOR.md), [example agent profiles](examples/hermes-agents.json), and [Linux background service template](examples/acc.service). The guide includes Windows startup, MCP setup, the actual verification performed, and remaining host checks.

For Google Antigravity, use the [MCP-first interactive integration contract](docs/ANTIGRAVITY-INTEGRATION.md). ACC does not claim background Antigravity control until the installed host exposes and passes a supported noninteractive lifecycle.

For low-cost game-logic work, see the [Muse Spark isolated-repository workflow](docs/MUSE-SPARK-WORKFLOW.md). The provider remains unavailable until a local credential and worker are configured.

For agent-built 3D assets instead of a remote mesh-generation API, see the [Agent 3D Studio connector](docs/AGENT-3D-STUDIO.md). Its claim/run/finish loop is tested against a scripted stand-in; no live coding-agent CLI run has been exercised yet. Configuring it as a job-backed implementer on a managed workflow (instead of submitting a bare job) routes its output through the same independent-review and git-delivery pipeline code tasks already use.

## First concrete task

For a model-free verification, open **Advanced task controls** and create a task assigned to **Local command**:

- Title: `Verify ACC's coordinator`
- Instruction: `Run the automated coordinator tests and report the actual result.`
- Command arguments on Linux: `["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]`
- Command arguments on Windows: `["py", "-3", "-m", "unittest", "discover", "-s", "tests", "-v"]`

Click **Start task**. Output should appear during execution. This is real tool execution, not model inference. Commands are argument arrays and run with `shell=False` in the selected project. They have the permissions of the local user; this application is not an execution sandbox.

## Connect an orchestrator

Run the bridge as an MCP stdio server in your host's configuration:

```json
{
  "command": "python3",
  "args": ["-m", "acc.bridge", "--url", "http://127.0.0.1:8765", "--token-file", "/absolute/path/to/state/token"],
  "cwd": "/absolute/path/to/The-ACC-AgentCommandCenter"
}
```

This is the server command description; the outer configuration shape is host-specific. For Windows, use `py` with `-3` before `-m`. The actual state/token path is printed by the coordinator. Start the coordinator before the bridge. Do not commit the token.

Conversation tools include `acc_conversation_read`, `acc_conversation_send`, `acc_conversation_claim`, `acc_conversation_renew`, `acc_conversation_complete`, `acc_conversation_release`, `acc_conversation_retry`, and `acc_orchestrator_select`.

Additional tools: `acc_switch_agent`, `acc_schedule_task`, `acc_github_refresh`, `acc_github_configure`, `acc_publish_preview`, and `acc_publish_task`.

Integration tools: `acc_submit_integration_job`, `acc_claim_integration_job`, `acc_renew_integration_job`, `acc_finish_integration_job`, `acc_cancel_integration_job`, `acc_retry_integration_job`, `acc_memory_search`, `acc_memory_propose`, and `acc_memory_review`.

Task tools: `acc_configure_workflow`, `acc_state`, `acc_create_task`, `acc_start_task`, `acc_stop_task`, `acc_assign_task`, `acc_update_instructions`, `acc_report`, and `acc_record_review`.

Your connected orchestrator explicitly records instructions and actions through these tools. ACC does not read unrelated chats or automatically connect this repository to ChatGPT Remote. The bridge's HTTP operations are tested; native host setup still needs verification on your computer.

## Configure a coding worker

Copy `examples/agents.json` outside the repository and replace the placeholder command with the actual installed worker host's noninteractive invocation. Then launch ACC with `--agents` pointing to that file.

For a named provider (DeepSeek, Gemini, Claude, Grok, a local Ollama/LM Studio server) instead of a custom `argv` adapter, use one of the built-in `driver` values documented in [DRIVERS.md](docs/DRIVERS.md), with a worked example of each in [`examples/all-drivers-agents.json`](examples/all-drivers-agents.json).

`{prompt_file}` is replaced with a JSON task packet path. `{project}` is replaced with the project path. The adapter must read the packet, follow its requirements, emit progress on stdout/stderr, and remain attached until its work finishes. ACC does not force a task-file flag on hosts that do not support it; a small wrapper may be needed.

A detected executable means **configured**, not a verified provider login or live model connection. API credentials stay in the host's own configuration or environment. No paid model calls are made by the default setup. Output and prompts are private local data; avoid putting credentials in worker output or task text.

Adapters must not daemonize, escape their process group, or leave detached work behind. POSIX stop/timeout tests include a child that ignores SIGTERM. Windows currently uses `taskkill /T /F`; detached-child/job-object containment and native Windows validation remain outstanding. Switching waits for the current step; forceful automatic mid-step transfer remains disabled; preferred conversation adapters are tried per turn, with permitted local fallback on planner failure. Managed workflows support an explicit offline mode and a permitted local coordinator fallback after a failed coordinator run.

Default run deadline: 900 seconds. The API accepts `timeout_seconds` from 1 to 86,400. Timeouts retain files and mark the run failed. Recovery after a coordinator crash requires inspecting the old PID and descendants before acknowledging release; ACC does not blindly restart interrupted workers.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check acc/web/app.js
```

See [implementation status](docs/IMPLEMENTATION-STATUS.md) for evidence and remaining work, [the full plan](docs/PROJECT-PLAN.md) for the agreed direction, and [the API](docs/API.md) for the bridge/HTTP contract.

Work is tracked through short-lived `temporary` → `main` PRs. The repository deletes merged branches automatically.
