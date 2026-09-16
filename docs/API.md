# Local API and event contract — v0.4

All API calls require `Authorization: Bearer <local token>`. Mutations require JSON. The server accepts only localhost/127.0.0.1 Host values for its bound port and rejects foreign browser origins. No cross-origin access is enabled. The token is a local control credential, not an agent-provider key.

| Method/path | Behavior |
| --- | --- |
| GET `/api/state` | Snapshot with tasks, agents, local Git, recent events, capability flags, and event cursor. |
| GET `/api/events?after=N` | SSE replay followed by live events. Each event has monotonically increasing `seq`, timestamp, kind, optional task ID, and data. Heartbeat comments are connection health only. |
| POST `/api/tasks` | Create `{title, instruction, agent?, argv?, timeout_seconds?}`. Default agent: `local-command`; creation does not start work. |
| POST `/api/tasks/{id}/start` | Start the configured runner. Reject a second writer and unresolved recovery. |
| POST `/api/tasks/{id}/stop` | Stop the supervised process tree and retain files. It may still be stopping when the response arrives; use events for the terminal result. |
| POST `/api/tasks/{id}/assign` | `{agent}`; available idle-worker assignment only. |
| POST `/api/tasks/{id}/instructions` | `{instruction}`; preserve history, increment revision, clear review, and queue the idle task. |
| POST `/api/tasks/{id}/report` | `{message, reference?}`; attributed report, not independently verified evidence. |
| POST `/api/tasks/{id}/review` | `{message, reference, revision, run_id}`; only accept the current review-ready run. Reference identifies the externally reviewed snapshot. |
| POST `/api/tasks/{id}/recover` | `{process_tree_inspected:true}`; explicit operator acknowledgement after external inspection. Reject if the recorded parent PID still exists. |

Error responses contain `error`: 400 invalid request, 401/403 authentication/origin, 404 missing task/endpoint, 409 conflicting state. Mutation retries are not a general idempotency API yet: do not blindly replay creation, start, or report calls after a network timeout. Read state and reconcile first.

The SSE stream is ordered and replayable within the local database. Local Git reconciliation checks every second; it is not yet an operating-system filesystem notification adapter. A Git change event does not claim an agent authored it. GitHub refresh uses a configurable 15–300 second interval, default 30, with a manual refresh control and last-success timestamp.

The MCP bridge translates tools into these local operations. It supports initialize, initialized notifications, ping, tools/list, and tools/call over newline-delimited JSON-RPC stdio. It negotiates the explicitly implemented protocol versions and exposes no extra resources/prompts. Host-specific installation and complete external SDK interoperability remain validation tasks.

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

Runtime files: `acc.sqlite3`, `token`, lock files, and `runs/{run_id}/task.json`, `output.log`, `handoff.json`. Task and event changes commit together. Manual-task handoff packets contain current instructions, history, run/evidence metadata, and a local Git snapshot; **they are not full backups of edited files**. Existing files remain in place. Managed workflows additionally save a hash-verified review copy and manifest; scope and limits are in HERMES-CONNECTOR.md. Role changes can be requested for the end of the current step; automatic mid-step takeover remains disabled.

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
