# M09 — Hosted Platform API and Events

**Status:** PARTIAL — read/event contracts, hosted HTTP/WSS transport, and permissioned commands are integrated (PR #33 merged as `c3d3794`). `SQLiteCommandRepository` now provides an initial durable atomic command/event store; production composition, durable read projections, M08 production integration, event/session retention policy, off-loop execution and deployment controls remain.

## Purpose

M09 is the hosted boundary between authoritative ACC Platform state and remote surfaces: ACC Web,
the ACC Plugin, and future cloud-connected Desktop. It consumes M08 and does not turn the existing
loopback `acc/server.py` into a public server.

## First slice

The first slice separates the application contract from the eventual hosting framework.

`acc/platform/` defines:

- `PlatformReadRepository`: account-scoped hosted read model;
- `AccountProjectView`: project summary;
- `PlatformEventSource`: durable account/project-scoped event replay source;
- `PlatformEvent` and `EventBatch`: cursor/reconnect contract;
- `PlatformApi`: framework-neutral authenticated read/event application boundary;
- `ApiResponse` and `ApiError`: deterministic status/error envelope.

Initial operations and required grants (all also require the `acc.web` entitlement):

| Operation | Permissions |
|---|---|
| list projects | `project.read` |
| project state (project + tasks + workers + attention) | `project.read` **and** `task.read` (it returns full task records) |
| one task | `task.read` |
| events after a durable cursor | `event.read` |

Workers and attention items have no separate permission in v1; they are covered by `project.read`.
If they later gain sensitive fields, give them their own permission before exposing those fields.

`PlatformReadRepository` must return **public projections**. M09 checks ownership
(`account_id`/`project_id`, plus task `id`) but otherwise passes records through unchanged. It does
not strip fields, so internal fields such as argv, prompts, credential references or local paths
must never be in the read model.

## Security rules

Every account-scoped operation parses a Bearer session and calls M08 with the route's explicit
`account_id`. The first Web-facing slice requires both the exact read permission and the
`acc.web` entitlement.

M08 errors map deterministically:

- `AuthenticationError` -> 401 `authentication_required`;
- `AuthorizationError` -> 403 `access_denied`;
- caller input validation (malformed bearer header, account/project/task ID, cursor or limit) -> 400.
  Route IDs are validated before any repository call;
- repository/event-source contract violation -> 500 without row/event details;
- any other unexpected exception -> opaque 500 `internal_error` via `PlatformApi.handle`. The HTTP
  adapter must call every operation through `handle` (or reproduce it) and log the chained
  exception server-side only.

Tokens are never echoed. A token for account A requesting account B always returns the same 403,
whether or not B or its projects exist, so it can't be used to enumerate accounts. Within one's own
account, an unknown project or task is 404.

Hosted storage adapters must apply `account_id` in the storage query itself. The API also checks
record account/project IDs as defense in depth.

## Event/reconnect contract

Transport is replaceable (polling, SSE, WebSocket), but replay semantics are stable:

- cursor is a nonnegative integer;
- reconnect requests events strictly after the last accepted cursor;
- events are strictly ordered by sequence;
- every event carries account/project ownership;
- cross-tenant/project events fail closed;
- an empty batch preserves the requested cursor and cannot claim `has_more`;
- a batch may not contain more events than the requested `limit` (1–500);
- event timestamps are finite and nonnegative;
- a missing cursor (`None`) means "from the beginning" (0); any other non-integer, boolean or
  negative cursor is a 400.

`seq` is monotonic **within one (account, project) stream**. It need not be contiguous: a source may
use an account-wide or global sequence, so gaps in a project stream are normal and are not a
missed-event signal.

### Reset/retention signal

The hosted-transport slice adds the required additive reset signal before retention/pruning:
`EventBatch.reset_required` and `oldest_available`.

A reset batch contains no events, cannot claim `has_more`, identifies the oldest retained sequence,
and supplies the cursor to resume from **after the client refetches project state**. This covers both
pruned-history gaps and a cursor that is no longer valid after restore/recovery. The HTTP ticket
endpoint returns `409 event_reset_required` rather than opening a stream from a stale cursor; an
already-open WebSocket can also emit `reset_required` and close with code 4009.

An event source must set the reset cursor to a real stream position no later than the stream head
at the time it reports the reset. The client procedure is fixed:

1. Record the reset `cursor` **before** doing anything else.
2. Refetch project state (`GET .../projects/{project_id}`) and replace local state with it.
3. Request a new ticket with `after=<recorded cursor>` and reconnect.

Because the cursor was captured before the refetch, every event after it is replayed, so nothing
is lost between the refetch and the resume. Events that the refetched state already reflects can
be replayed as well, so clients must apply events idempotently (for example by comparing entity
revisions). If the recorded cursor has itself been pruned by the time the client reconnects, the
ticket request returns 409 again and the client repeats the procedure.

Other reconnect notes:
- repeated reconnects with the same cursor are idempotent;
- duplicates and replays (`seq <= cursor`) fail closed as 500;
- `events_after` for a project ID that doesn't exist in the account returns an empty batch, not a
  404. The adapter may add a `project` existence check if subscribing to phantom projects becomes a
  concern.

This gives the later WebSocket adapter a durable replay contract instead of a separate live-only
event model.

## Hosted transport slice

`acc/platform/transport.py` is a dependency-free ASGI application boundary. Any production ASGI
server can host it without coupling the M09 application contract to that server.

It provides:

- explicit HTTPS Origin allowlisting; no wildcard origins;
- CORS responses scoped to the accepted origin;
- HTTP routes under `/v1/accounts/{account_id}/...`;
- short-lived, random, one-time WebSocket tickets issued only after `event.read` authorization;
- bearer session tokens never placed in the WebSocket URL;
- reauthorization on every WebSocket poll so revocation or permission loss closes the stream;
- replay from the ticket's durable cursor;
- reset/retention handling;
- read-only WebSocket input (only `ping` is accepted).

The one-time ticket store retains the already-presented ACC session Authorization value only in
process memory for the short-lived stream. It is never serialized, logged (it is excluded from the
ticket record's `repr`), returned to the client, or placed in the WebSocket URL. The store is
process-local by design: it must not be persisted or shared between processes. A multi-process
deployment must keep the ticket request and the WebSocket on the same process (or replace the store
with one keyed by a server-side session reference once M08 provides one). Retaining the value
gives no more than the session itself does: the stream re-runs full M08 authorization on every
poll, so revocation, expiry, account suspension, membership removal, loss of `event.read` or loss
of `acc.web` closes it.

Transport rules:

- `Origin` is required on every HTTP request and WebSocket handshake and must equal one allowed
  origin exactly (`https://host[:port]`, lowercase, no path, query, fragment, userinfo or
  wildcard). A repeated `Origin` or `Authorization` header is treated as absent, so the request
  fails closed. Preflight and error responses never carry CORS headers for a disallowed origin,
  and `Access-Control-Allow-Credentials` is never sent (the API uses bearer headers, not cookies).
- Query integers must be ASCII digits and appear at most once; `after=` (blank), repeats,
  non-ASCII query strings and out-of-range values return 400.
- The WebSocket handler receives `websocket.connect` before validating the Origin, path or ticket.
  A rejected Origin or path closes the handshake with 4403 before the ticket is looked at, so it
  does not burn a ticket. A missing, malformed, repeated, expired or reused ticket closes with 4401.
- Close codes: 4401 session ended or ticket invalid; 4403 origin denied or access lost; 4009 reset
  required; 4400 client sent anything other than the text `ping`; 1011 invalid event source or
  non-JSON event data (HTTP returns an opaque 500 for the same case).
- One `receive()` is kept outstanding between polls and is never cancelled by a poll timeout;
  disconnects and cancellation cancel it. A `has_more` backlog is drained without waiting for the
  poll interval, reauthorizing before every batch.
- The ASGI `lifespan` scope is supported.

Deployment requirements not handled inside this module:

- `PlatformApi` calls are synchronous. With a blocking durable repository or event source the
  adapter must run them off the event loop (for example with `asyncio.to_thread`) or use an async
  repository; the in-memory test doubles do not block.
- The ticket store has no size cap beyond the 10-300 second TTL. Rate-limit ticket issuance and
  concurrent WebSocket connections per session/account at the hosting layer.
- Terminate TLS in front of the ASGI server; this module does not listen on sockets.

## Still deliberately deferred

- no write/command endpoints;
- no durable hosted database adapter;
- no attempt to expose the local `Coordinator` directly;
- no changes to M08 or the frozen spine.

After independent review and executable verification of this transport slice, the next M09 work is
command/mutation endpoints with idempotent operation IDs and optimistic revisions, plus the durable
hosted repository/runtime needed to deploy the real Platform service.


## Remote command/mutation slice

**Status:** implemented on `temporary/m09-command-mutations-v1` (PR #33). The independent review
corrected the quota model and tightened the request schemas; those corrections are a public-contract
change awaiting independent confirmation.

### Route and request

`POST /v1/accounts/{account_id}/projects/{project_id}/commands` with a JSON object body of exactly:

```json
{"operation_id": "op-...", "kind": "task.create", "expected_revision": 3,
 "payload": {"task_id": "task-9"}}
```

- The body must be one JSON object with exactly these four fields; unknown fields (including the
  former `quotas`) are rejected with 400. At most 100,000 bytes, UTF-8, no repeated object keys, no
  `NaN`/`Infinity`. Only this route reads a request body.
- `operation_id`: 1-200 characters, client-generated, unique per account for the lifetime of the
  stored result.
- `expected_revision`: required non-negative JSON integer (booleans, strings and floats are 400).
- `payload`: exact per-kind schema; every field required, no other field accepted:

| Kind | Permission | Payload | Valid when | Quota (server-derived) |
|---|---|---|---|---|
| `task.create` | `task.write` | `{"task_id"}` | task ID unused in the project | reserves `tasks.active` 1 |
| `task.cancel` | `task.cancel` | `{"task_id"}` | task exists and is not cancelled | releases what the task reserved |
| `project.mode` | `project.write` | `{"mode": "online"\|"offline"}` | mode differs from current | none |
| `worker.pause` | `worker.control` | `{"worker_id"}` | worker exists and is active | none |
| `worker.resume` | `worker.control` | `{"worker_id"}` | worker exists and is paused | none |

The command kind alone selects both the required permission and the mutation, so a weaker kind
cannot carry a payload that performs a stronger mutation.

### Authorization and quota ownership

Every request re-runs M08 `authorize` for the route's account with the kind's exact permission and
the `acc.web` entitlement, so revocation, expiry, account suspension, membership removal and loss
of the permission or entitlement stop the next mutation.

Clients never choose quotas. `COMMAND_QUOTAS` maps each kind to its reservations, and the ceiling
for each is the account's **live M08 entitlement limit**, passed into the repository transaction.
A missing limit fails closed (409 `quota_exceeded`). The repository compares recorded usage plus
the reservation with that ceiling inside the same transaction as the mutation, so concurrent
requests cannot overshoot.

### Responses

| Status | Code | Meaning | Effects |
|---|---|---|---|
| 201 | — | applied; body has `operation_id`, `status`, `revision`, `result`, `replayed:false` | exactly one mutation, revision +1, quota change, audit record, event, stored result |
| 200 | — | replay of the same operation; original body with `replayed:true` | none |
| 400 | `invalid_request` | malformed body, IDs, kind or payload | none |
| 401 / 403 | `authentication_required` / `access_denied` | M08 check failed | none |
| 404 | `not_found` | project, task or worker not in this account/project | none |
| 409 | `idempotency_conflict` | operation ID already applied with a different fingerprint | none |
| 409 | `revision_conflict` | stale or future `expected_revision`; body includes `current_revision` | none |
| 409 | `quota_exceeded` | reservation would exceed the M08 ceiling, or none is configured | none |
| 409 | `state_conflict` | invalid transition (duplicate task, already cancelled/paused/active, same mode) | none |
| 500 | `internal_error` | repository fault; opaque | none |

A rejected command (any 4xx) does **not** claim the operation ID, so the client may correct and
retry with the same ID.

### Idempotency semantics

- Operation IDs are scoped to the account: the key is `(account_id, operation_id)`.
- The fingerprint is SHA-256 over account, project, kind, expected revision, normalized payload and
  the **authenticated actor**. A retry by the same user with the same input replays; any other
  input, a different project, or **another user in the same account** reusing the ID gets 409
  `idempotency_conflict` and never sees the original result. Clients must therefore generate
  operation IDs per user (for example random UUIDs), not per shared workflow.
- A replay still requires the caller's current permission; it does not re-check revision or quota.

### Audit and events

Each applied command writes one audit record (`operation_id`, `account_id`, `project_id`,
`actor_user_id`, `kind`, resulting `revision`, `at`) and one `command.applied` Platform event whose
data carries `operation_id`, `kind`, `actor_user_id`, `revision` and the command `result`, with the
same timestamp. No bearer token or session value is recorded. The event is served by the existing
`events_after`/WebSocket path: the command store must be the `PlatformEventSource` (or share its
sequence), not a second event model.

### Durable repository requirements

`InMemoryCommandRepository` is reference semantics. A SQL implementation of
`PlatformCommandRepository.execute` must run steps 1-6 of its docstring in one serializable (or
equivalently locked) transaction and needs at least:

- **Command results:** unique key `(account_id, operation_id)`, storing fingerprint, result and
  revision. Insert it in the same transaction as the mutation; a unique-violation on commit means a
  concurrent winner, so re-read and replay or raise `IdempotencyConflict`. Retain results at least
  as long as clients may retry (define a retention window before pruning).
- **Project revision:** compare-and-swap, e.g. `UPDATE projects SET revision = revision + 1 WHERE
  account_id = ? AND project_id = ? AND revision = ?`, requiring exactly one updated row, or
  `SELECT ... FOR UPDATE` before validating the transition.
- **Quota usage:** one row per `(account_id, quota_name)`, updated with a conditional increment
  (`SET used = used + ? WHERE used + ? <= ?`, the ceiling from M08) or under a row lock; releases
  never go below zero. Each task records what it reserved so a release is exact.
- **Audit:** append-only insert in the same transaction.
- **Events:** a sequence allocated inside the transaction and strictly increasing per
  `(account_id, project_id)` stream (a global sequence is fine; gaps are allowed). Sequences must
  become visible in order: if concurrent transactions can commit out of sequence order, readers
  must not advance past an uncommitted gap (e.g. allocate under the project row lock).
- **Tasks/workers:** keyed by `(account_id, project_id, id)` so IDs never cross projects.

The production runtime must also run these synchronous calls off the ASGI event loop and add rate
limits (see the hosted transport deployment requirements).


## Initial SQLite command/event adapter

`acc.platform.SQLiteCommandRepository` implements the M09 command repository and event source using a durable SQLite file. Each command runs under `BEGIN IMMEDIATE`, serializing revision checks, quota usage, mutation, idempotency result, audit row and per-project event sequence in one transaction. A result keyed by `(account_id, operation_id)` is retained indefinitely; pruning is disabled until a client retry-retention policy is specified. Event sequence allocation is stored on the project row and event replay uses the same database.

This is a storage adapter, not yet the production hosted runtime. The hosted read projection adapter and composition/configuration are still needed. SQLite calls are synchronous, so the ASGI transport must move them off the event loop. Production also needs a shared durable disk/database deployment, rate limits, and an M08 server-side session-reference design for WebSocket authorization. The in-memory staging service remains separate and was not changed by this adapter.
