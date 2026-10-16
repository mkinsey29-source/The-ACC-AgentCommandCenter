# Local API and event contract — v0.6

All API calls require `Authorization: Bearer <token>`: the owner token (memory-only, new at every start, obtained once by exchanging a launch code, see "Owner launch" below) or the orchestrator's `agent-token`. Mutations require JSON. The server accepts only localhost/127.0.0.1 Host values for its bound port and rejects foreign browser origins. No cross-origin access is enabled. The token is a local control credential, not an agent-provider key.

| Method/path | Behavior |
| --- | --- |
| GET `/api/state` | Snapshot with tasks, agents, local Git, recent events, capability flags, and event cursor. |
| GET `/api/events?after=N` | SSE replay followed by live events. Each event has monotonically increasing `seq`, timestamp, kind, optional task ID, and data. Heartbeat comments are connection health only. |
| POST `/api/tasks` | Create provider-neutral work with `{title, instruction}` plus optional `agent`, `argv`, `timeout_seconds`, `task_area`, `workstream_id`, `required_capabilities`, `inputs`, `permissions`, `expected_artifacts`, `acceptance_requirements`, `resource_requirements`, `risk`, `priority`, `depends_on`, `data_classification`, `workspace_scope`, and `knowledge_scopes`. Existing callers may omit all new fields. Default agent: `local-command`; creation does not start work. |
| POST `/api/tasks/{id}/start` | Start the configured runner. Reject a second writer and unresolved recovery. |
| POST `/api/tasks/{id}/stop` | Stop the supervised process tree and retain files. It may still be stopping when the response arrives; use events for the terminal result. |
| POST `/api/tasks/{id}/assign` | `{agent}`; available idle-worker assignment only. |
| POST `/api/tasks/{id}/instructions` | `{instruction}`; preserve history, increment revision, clear review, and queue the idle task. |
| POST `/api/tasks/{id}/report` | `{message, reference?}`; attributed report, not independently verified evidence. |
| POST `/api/tasks/{id}/review` | `{message, reference, revision, run_id}`; only accept the current review-ready run. Reference identifies the externally reviewed snapshot. |
| POST `/api/tasks/{id}/recover` | `{process_tree_inspected:true}`; explicit operator acknowledgement after external inspection. Reject if the recorded parent PID still exists. |

Every user-visible task also has a permanent positive `task_number`. The human label is `Task N`; the UUID `id` remains the API key. Existing databases assign numbers once in original row order, internal conversation/transcription runs receive no number, and the next number is never reduced or reused.

Managed implementation, review, and coordination runs are stored as numbered `workflow_step` child records with `parent_task_id` and `parent_task_number`. They are history records controlled by the parent and cannot be started or scheduled directly. Recovery successors use `task_kind: recovery` and the same parent fields.

Additional v0.5 operations:

- `GET /api/archive` searches by `q`, `status`, `agent`, `number_from`, `number_to`, and `limit`.
- `POST /api/archive/export` writes a Markdown archive and consistent SQLite backup to an existing absolute local directory.
- `POST /api/project/mode` sets the authoritative project-wide `online` or `offline` assignment policy.
- `POST /api/tasks/{id}/recovery-handoff` creates a numbered successor after the prior writer has stopped.

Error responses contain `error`: 400 invalid request, 401/403 authentication/origin, 404 missing task/endpoint, 409 conflicting state. Integration job submission, memory proposal, conversation, assignment, and publication operations have scoped idempotency keys. Do not blindly replay other mutations after a network timeout; read state and reconcile first.

The SSE stream is ordered and replayable within the local database. Local Git reconciliation checks every second; it is not yet an operating-system filesystem notification adapter. A Git change event does not claim an agent authored it. GitHub refresh uses a configurable 15–300 second interval, default 30, with a manual refresh control and last-success timestamp.

The MCP bridge translates tools into these local operations. It supports initialize, initialized notifications, ping, tools/list, and tools/call over newline-delimited JSON-RPC stdio. It negotiates the explicitly implemented protocol versions and exposes no extra resources/prompts. Host-specific installation and complete external SDK interoperability remain validation tasks.

## Obsidian knowledge MCP

- `GET /api/knowledge`: report the configured project-vault and embedding state.
- `POST /api/knowledge/search`: search the project vault with `{query,limit?,include_inactive?,include_checkouts?}`.
- `POST /api/knowledge/checkout`: retrieve task context and create a pre-work synthesis note.
- `POST /api/knowledge/checkin`: create the linked completion or handoff note.
- `POST /api/knowledge/note`: create a typed knowledge note.
- `POST /api/knowledge/transition`: change note status while retaining its history.
- `POST /api/knowledge/rebuttal`: create a correction and mark/link the original note.

The corresponding MCP tools use the `acc_knowledge_*` prefix. See
[`OBSIDIAN-KNOWLEDGE-MCP.md`](OBSIDIAN-KNOWLEDGE-MCP.md) and
[`AGENT-KNOWLEDGE-PROTOCOL.md`](AGENT-KNOWLEDGE-PROTOCOL.md).

## Integration jobs and shared memory (v0.6)

- `GET /api/integrations`: provider catalog, durable jobs, and artifact records.
- `POST /api/integrations/jobs`: submit `{capability,input,provider?,task_id?,budget?,priority?,idempotency_key?}`. Automatic routing selects an enabled provider with that capability. Cloud-only work becomes `blocked_offline` while the project is offline.
- `POST /api/integrations/claim`: a worker claims one eligible job using `{owner,provider?,capabilities?,lease_seconds?}` and receives a secret lease token plus monotonic fence.
- `POST /api/integrations/jobs/{id}/renew`: renew with `{lease_token,fence,lease_seconds?}`.
- `POST /api/integrations/jobs/{id}/finish`: finish with `{lease_token,fence,status,result?,cost?,error?,artifacts?}`. Status is `succeeded` or `failed`; stale owners are rejected.
- `POST /api/integrations/jobs/{id}/cancel` and `/retry`: cancel waiting work or retry failed work under the current online/offline policy.
- `GET /api/memory`: search using `q`, `kind`, `status`, `task_id`, and `limit`. Default status is reviewed `active` memory.
- `POST /api/memory/propose`: propose a versioned `{title,body,kind?,key?,source?,task_id?,branch?,commit?,tags?,request_id?}` entry.
- `POST /api/memory/{id}/review`: activate or reject a proposal with `{status,reviewer?}`. Activating a version supersedes the prior active version with the same key.

Provider credentials are not accepted or stored by these endpoints. External workers keep credentials in their own environment, poll/claim jobs, perform the provider call, and report results and artifact references.

## Managed workflows

`POST /api/tasks/{id}/workflow` (MCP: `acc_configure_workflow`) accepts:

- `implementer`, `reviewer`, `coordinator`: configured adapter IDs; implementer and reviewer must differ.
- `enabled:false`: pause future launches, allowing a current runner to finish.
- `enabled:true` or omitted: configure/resume when idle. Enabling starts background work.
- `mode`: `online` (preferred adapters) or `offline` (declared local adapters / permitted fallbacks).
- `fallbacks`: optional object mapping each role to a configured adapter with `local:true`.
- `max_rounds`: 1–10, default 3. Counts implementation rounds, including the initial one.
- `restart:true`: explicitly restart implementation with a fresh snapshot/approval cycle. Required to rerun an accepted task.

Managed tasks persist `workflow.stage` (`implement`, `coordinate`, `review`), `phase` (`queued`, `running`, `held`, `complete`), configured roles, round, snapshot manifest, reports, and decision history. `active_agent` and each run record identify the actual adapter, including fallback. `launching` is saved before spawn; `processing_result` covers the recovery-visible interval before a handoff is committed. A crash in either state becomes `interrupted` and blocks new work until inspection.

Managed `start`, direct assignment, and manual acceptance are blocked; use workflow controls. Requirements revision disables the old workflow. Changing the reviewer invalidates its previous verdict. Results carry exact `task_id`, `run_id`, integer `revision`, and `snapshot_id`, plus `summary`; implementers list `checks`, reviewers add `verdict`, `findings`, and `checks`, coordinators return one permitted `action`. Inspect the `workflow.result_contract` in the packet. Managed adapters must atomically write the specified `result_file`; the Hermes driver does this from its terminal response.

A coordinator may request review, request changes following review, accept an approving review, or hold. Invalid transitions hold instead of launching anything. Acceptance references the implementation run and review run separately. No model-provided command string is executed as a coordination action. Normal worker commands remain configured on the host.

## State and privacy

Runtime files: `acc.sqlite3`, `agent-token` (the orchestrator credential, unless `--agent-token-file` places it elsewhere), `pairing.json` and `reach.json` when remote access is on, `endpoint.json` (the loopback address and process id for the orchestrator bridge, no credential; written at every start, removed at shutdown, or placed elsewhere with `--endpoint-file`), lock files, and `runs/{run_id}/task.json`, `output.log`, `handoff.json`. Task and event changes commit together. Manual-task handoff packets contain current instructions, history, run/evidence metadata, and a local Git snapshot; **they are not full backups of edited files**. Existing files remain in place. Managed workflows additionally save a hash-verified review copy and manifest; scope and limits are in HERMES-CONNECTOR.md. Role changes can be requested for the end of the current step; automatic mid-step takeover remains disabled.

Output is streamed as reported chunks, not inferred model thinking. Avoid secrets in prompts/output. Full process output is stored locally. Log rotation, output quotas, richer structured model events, and automatic secret filtering are not yet implemented.


## Conversation and recording endpoints (v0.3)

All routes use the existing loopback bearer-token/origin checks.

- `GET /api/conversation?after=0`: up to 100 original messages, ordered by persistent sequence. Continue with the last `seq`.
- `POST /api/conversation/send`: `{id, text, source?}`; stable ID makes identical retries idempotent. Original text is preserved.
- `POST /api/conversation/configure`: persistent `{enabled, preferred_agent, local_agent, mode, workflow}` defaults. `workflow` uses the managed workflow role/fallback schema.
- `POST /api/conversation/claim`: `{owner}`; returns token, expiry, and context.
- `POST /api/conversation/renew`: `{token}`; refreshes pending batch and lease.
- `POST /api/conversation/complete`: `{token, reply, intent, actions}`. Intent is `discussion`, `clarification`, or `request`; only requests can contain actions. Action type `create` requires title/instruction/source_ids; `revise` requires task_id/current revision/instruction/source_ids. IDs must refer to the captured pending messages. All changes and reply are committed together. Identical retries return the saved receipt.
- `POST /api/conversation/release`: `{token}`; external session yields without consuming messages.
- `POST /api/conversation/retry`: `{}`; resumes a held inbox after inspection, without bypassing runner recovery.
- `POST /api/voice/save`: `{id, mime, audio}` with base64-encoded audio. Maximum 15 MiB request / 10 MiB decoded audio. Requires host transcription configuration. Saves before scheduling; same ID/content is idempotent.
- `POST /api/voice/retry`: `{task_id}`; retries a paused transcription.

`GET /api/state` adds `conversation`: recent messages, pending count, current owner (without lease token), routing settings, held reason, internal active/interrupted runs, and recording status. Internal planner/transcription tasks are omitted from the ordinary work plan.

Conversation leases last 120 seconds for external sessions. The supervised local turn remains owned until its runner ends or is recovered. Stale ownership and revision conflicts return HTTP 409. The complete flow, model contracts, and offline behavior are documented in `CONVERSATION-WORKFLOW.md`.


## Laptop readiness controls (v0.4)

| POST path | Payload and behavior | MCP tool |
| --- | --- | --- |
| `/api/tasks/{id}/switch` | `{role,agent,request_id}`. Persist an assignment request; apply after current step or immediately when idle. A replacement reviewer must review the snapshot again. | `acc_switch_agent` |
| `/api/tasks/{id}/schedule` | `{priority?,depends_on?}`. Higher priority runs first; prerequisites must be accepted. Cycles and active-task rescheduling are rejected. | `acc_schedule_task` |
| `/api/github/configure` | `{enabled?,remote?,source?,base?,interval?}`. Defaults origin, temporary, main, 30 seconds. | `acc_github_configure` |
| `/api/github/refresh` | `{}`. Request asynchronous refresh; state retains dated cached results on failure. | `acc_github_refresh` |
| `/api/tasks/{id}/publish-preview` | `{}`. Requires accepted managed work, matching snapshot and baseline. Returns exact repository, source/base, changed paths and outgoing commit history. | `acc_publish_preview` |
| `/api/tasks/{id}/publish` | `{preview_id,request_id}`. Starts asynchronous commit/push/PR operation under existing user authorization. Inspect publication events/status; do not confuse HTTP acceptance with a confirmed PR. | `acc_publish_task` |

Publication reserves the workspace against workers and external conversation claims. It rejects unrelated dirty files, existing staged work, overlapping pre-task edits, review drift, changed refs, and pushes to the protected base. A GitHub CLI login and Git push authentication are required on the host. Existing accepted tasks from before baseline tracking must run a fresh cycle. Failed or uncertain operations retain local artifacts for inspection; a preview reconciles the state before retrying. There is no merge or force-push action.

Assignment request IDs and publication request IDs support identical retry reconciliation. Other legacy task actions still require the caller to reconcile uncertain responses. Restart recovery cancels pending role changes; inspect the old process tree and explicitly choose the next assignment.

Git built-in text normalization and `core.filemode=false` are handled against the preview-bound expected tree. Assigned clean filters, including Git LFS, require manual publication; ACC rejects them before hashing/staging. The current review snapshot is code-oriented (10,000 files / 100 MiB), not a full asset repository snapshot.

## Remote access (SC-7, SC-8)

See `docs/REMOTE-ACCESS.md`. In `local` mode (the default) none of this is reachable and the rules above are unchanged.

- `GET /api/remote` (owner only): `{mode, enabled, origin, hosts, devices, pending}`.
- `POST /api/pairing/begin` (owner only): `{token, code, expires, origin, url}`; a one-use link and eight-digit code valid for 5 minutes.
- `POST /api/pairing/claim` (remote host, no login): `{secret, name}` where `secret` is the link token or the code; returns `{request_id, claim, code}` (the six-digit match code).
- `POST /api/pairing/poll` (remote host, no login): `{request_id, claim}`; returns `{status}` of `waiting`, `approved`, `denied` or `expired`. On `approved` the response sets the `acc_device` cookie (HttpOnly, SameSite=Strict, Secure when served over HTTPS) once; the credential is never in the body.
- `POST /api/pairing/approve` and `/deny` (owner only): `{request_id}`.
- `POST /api/pairing/revoke` (owner only): `{device_id}`; also ends that device's event stream.

"Owner" means a request with a local Host and the local control token. Over a remote host only a paired device's cookie authenticates; the control token is refused. Pairing errors: 403 bad or used link, 404 unknown request, 409 expired or device cap reached, 429 too many attempts.

### Permission gates (SC-9)

Callers using the orchestrator credential (`agent-token`) or a paired device are gated; the owner is not. See `docs/REMOTE-ACCESS.md`.

- A gated operation returns `202 {status:"approval_required", approval_id, approval, message}` and does not run. The gated operations are: creating a task with `argv` (`POST /api/tasks`); **starting** a stored task whose worker is the local command (`POST /api/tasks/{id}/start`); `archive/export`; `tasks/{id}/publish`; `github/configure`; `tasks/{id}/recover`; `memory/{id}/review` with `active`; `knowledge/transition`; `knowledge/rebuttal`; `knowledge/note` with status `verified` or `active`; and **a promoting check-in** (`knowledge/checkin`) whose own status is `verified` or `active` (**an omitted status counts as `active`**, the default) or that carries a `completed_knowledge` item marked `verified`. A routine check-in states `"status": "supported"`. The approval `kind` is `command.create`, `command.start`, `files.export`, `github.publish`, `github.configure`, `task.recover`, `memory.accept`, `knowledge.transition`, `knowledge.rebuttal`, `knowledge.note` or `knowledge.checkin`.
- The writers re-check authority themselves, for the writes the tests cover. Check-in, note, transition and rebuttal refuse a gated caller who would establish `verified` or `active` knowledge, before writing anything; memory activation is refused the same way. A gated caller's **checkout** (`knowledge/checkout`) is not refused but is saved as `supported`, because it is an operational record whose text the caller supplies; the owner's and the product's own checkouts stay `active`. A configured worker's own result (task, workflow or job-backed) never establishes knowledge: items it marks `verified` are kept as `supported`, with the claim recorded in the note and in the Activity feed (a best effort record: an event that cannot be written does not stop the save), until the owner promotes them with `knowledge/transition`. Other routes are not claimed to be covered unless a test says so.
- Approvals are bound to what was shown. For a start, ACC takes **one snapshot** of the task and its worker's configuration under the coordinator lock, and the displayed command, the recorded binding (task revision, worker, exact `argv`, timeout, configuration) and the later comparison all come from that snapshot. A publish approval records the saved preview (id, repository, task branch, base, push destination, head). Approving first re-reads the live state; if anything differs, nothing runs and the request becomes `stale` (409). Asking again after a change creates a new request and marks the earlier one `superseded`. A start approval re-checks and runs under one hold of the coordinator lock; a publish approval re-checks and then relies on the publisher's own target-change rejection.
- Exact values: `argv` (the raw list) and `argv_json`, the same list as ASCII JSON in which every argument boundary is quoted and every non-ASCII character, including invisible, control, bidi and variation characters, is a `\uXXXX` escape (valid JSON that round-trips). The export folder is shown the same way (`directory_json`). Publish details give `repository`, `branch` (the task branch actually pushed), `base`, `remote` and `push_destination` (all credentials removed) as JSON strings, plus `preview_id`, `head`, `outgoing_commits` and at most 200 `changed_paths`; when there are more, `changed_paths_truncated` says how many are not shown. Summaries are shortened for scanning and have invisible characters written out; the exact block is what to read before approving.
- `GET /api/state` includes `attention`: the pending approval requests (`id, kind, summary, details, requested_by, status, created, expires`).
- `GET /api/approvals/{id}` (any authenticated caller): the request's `status` (`pending`, `approved`, `denied`, `expired`, `executed`, `failed`, `stale`, `superseded`) and, once run, `result: {status, body}`. MCP: `acc_approval_status`.
- `POST /api/approvals/{id}/approve` and `/deny` (owner only): approving runs the stored request once and returns the resolved request. 403 for any other caller, 404 unknown, 409 already answered, expired, or stale (the target changed after the request), 429 too many waiting.

### Owner launch

The owner token is never written to disk or placed in a URL or process argument. ACC prints (and opens) `http://127.0.0.1:<port>/#launch=<code>`; the page exchanges the one-use code for the token.

- `POST /api/launch` (local host only, no login): `{code}` returns `{token}` once. The code expires after 10 minutes and works one time; a wrong or reused code returns 403, and 30 attempts a minute are allowed (429 beyond that). It is refused on any remote host, and a refusal does not use up the code.
- `POST /api/launch/new` (owner only): returns `{url, expires}` with a fresh one-use link, for another window.

