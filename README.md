# ACC — Agent Command Center

A local project window for instructions, agent assignments, live worker output, local Git changes, and review records. Windows and Linux are the intended targets; this first implementation was exercised on Linux.

**v0.1 is a working local foundation, not the complete orchestration system.** No simulated tasks are loaded. Cloud models are unavailable until you configure their host commands. Automatic agent takeover, offline fallback, and GitHub publishing remain planned.

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

## What works

- Create tasks with original instructions and retained requirement revisions.
- Select tasks and see assigned versus active workers, latest activity, and next step.
- Launch an explicit local command or a configured command-based worker.
- Stream actual output before the command finishes; reconnect using persisted event cursors.
- Inspect local changed files, current branch, and recent commits. Git state is reconciled once a second; runner output is streamed directly.
- Stop supervised work, preserve its files, and record a handoff packet.
- Reassign idle tasks to available workers. Active takeover is explicitly blocked.
- Persist tasks, runs, activity, reports, and review acceptance in SQLite.
- Block another ACC writer in the same workspace, including a second state directory under the same OS user.
- Expose task controls through a local stdio MCP bridge for a connected orchestrator.
- Label unavailable providers, failed runs, interrupted processes, and pending review honestly.

An exit code of zero means **Needs review**, not approved. Reported acceptance records its run ID, requirement revision, and a supplied code snapshot reference. ACC does not independently prove that an external reviewer inspected that reference.

## First concrete task

With ACC managing this repository, create a task assigned to **Local command**:

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

Available tools: `acc_state`, `acc_create_task`, `acc_start_task`, `acc_stop_task`, `acc_assign_task`, `acc_update_instructions`, `acc_report`, and `acc_record_review`.

Your connected orchestrator explicitly records instructions and actions through these tools. ACC does not read unrelated chats or automatically connect this repository to ChatGPT Remote. The bridge's HTTP operations are tested; native host setup still needs verification on your computer.

## Configure a coding worker

Copy `examples/agents.json` outside the repository and replace the placeholder command with the actual installed worker host's noninteractive invocation. Then launch ACC with `--agents` pointing to that file.

`{prompt_file}` is replaced with a JSON task packet path. `{project}` is replaced with the project path. The adapter must read the packet, follow its requirements, emit progress on stdout/stderr, and remain attached until its work finishes. ACC does not force a task-file flag on hosts that do not support it; a small wrapper may be needed.

A detected executable means **configured**, not a verified provider login or live model connection. API credentials stay in the host's own configuration or environment. No paid model calls are made by the default setup. Output and prompts are private local data; avoid putting credentials in worker output or task text.

Adapters must not daemonize, escape their process group, or leave detached work behind. POSIX stop/timeout tests include a child that ignores SIGTERM. Windows currently uses `taskkill /T /F`; detached-child/job-object containment and native Windows validation remain outstanding. Active takeover and automatic offline transitions are disabled on all platforms in this version.

Default run deadline: 900 seconds. The API accepts `timeout_seconds` from 1 to 86,400. Timeouts retain files and mark the run failed. Recovery after a coordinator crash requires inspecting the old PID and descendants before acknowledging release; ACC does not blindly restart interrupted workers.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check acc/web/app.js
```

See [implementation status](docs/IMPLEMENTATION-STATUS.md) for evidence and remaining work, [the full plan](docs/PROJECT-PLAN.md) for the agreed direction, and [the API](docs/API.md) for the bridge/HTTP contract.

Work is tracked through `temporary` → `main` PRs for this initial delivery. Do not merge or delete the source branch without the user's instruction.
