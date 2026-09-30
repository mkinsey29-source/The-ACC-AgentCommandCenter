# M09 — Hosted Platform API and Events

**Status:** Core v1 contracts in progress on `temporary/m09-platform-api-events-v1`.

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

Initial operations:

- list projects;
- project state (tasks/workers/attention);
- one task;
- events after a durable cursor.

## Security rules

Every account-scoped operation parses a Bearer session and calls M08 with the route's explicit
`account_id`. The first Web-facing slice requires both the exact read permission and the
`acc.web` entitlement.

M08 errors map deterministically:

- `AuthenticationError` -> 401 `authentication_required`;
- `AuthorizationError` -> 403 `access_denied`;
- caller input validation -> 400;
- repository/event-source contract violation -> 500 without row/event details.

Hosted storage adapters must apply `account_id` in the storage query itself. The API also checks
record account/project IDs as defense in depth.

## Event/reconnect contract

Transport is replaceable (polling, SSE, WebSocket), but replay semantics are stable:

- cursor is a nonnegative integer;
- reconnect requests events strictly after the last accepted cursor;
- events are strictly ordered by sequence;
- every event carries account/project ownership;
- cross-tenant/project events fail closed;
- an empty batch preserves the requested cursor.

This gives the later WebSocket adapter a durable replay contract instead of a separate live-only
event model.

## Deliberately not in this slice

- no public network server/framework;
- no CORS/origin policy yet;
- no WebSocket handshake/ticket implementation yet;
- no write/command endpoints;
- no durable hosted database adapter;
- no attempt to expose the local `Coordinator` directly;
- no changes to M08 or the frozen spine.

The next slice should add the hosted HTTP adapter and WebSocket/reconnect transport around these
contracts, then command/mutation endpoints with idempotent operation IDs and optimistic revisions.
