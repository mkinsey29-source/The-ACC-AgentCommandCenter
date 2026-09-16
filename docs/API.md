# Local API and event contract — v0.1

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

The SSE stream is ordered and replayable within the local database. Local Git reconciliation checks every second; it is not yet an operating-system filesystem notification adapter. A Git change event does not claim an agent authored it. No remote PR/build polling is implemented in v0.1.

The MCP bridge translates tools into these local operations. It supports initialize, initialized notifications, ping, tools/list, and tools/call over newline-delimited JSON-RPC stdio. It negotiates the explicitly implemented protocol versions and exposes no extra resources/prompts. Host-specific installation and complete external SDK interoperability remain validation tasks.

## State and privacy

Runtime files: `acc.sqlite3`, `token`, lock files, and `runs/{run_id}/task.json`, `output.log`, `handoff.json`. Task and event changes commit together. Initial handoff packets contain current instructions, history, run/evidence metadata, and a local Git snapshot; **they are not full backups of edited files**. Existing files remain in place. Capture immutable patches/snapshots before enabling automatic takeover.

Output is streamed as reported chunks, not inferred model thinking. Avoid secrets in prompts/output. Full process output is stored locally. Log rotation, output quotas, richer structured model events, and automatic secret filtering are not yet implemented.
