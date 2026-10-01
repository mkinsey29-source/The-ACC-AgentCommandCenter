# M09 staging runtime

This is a disposable **staging-only** composition for end-to-end browser testing of the accepted
M08/M09 contracts. It is not the production hosted runtime and does not contain production data.

## Entrypoint

ASGI application: `staging_app:app`.

The host supplies:

- `ACC_STAGING_BOOTSTRAP_SECRET`: required random secret, at least 24 characters;
- `ACC_STAGING_ORIGIN`: optional exact HTTPS browser Origin. Default:
  `https://acc-staging-console--memph1510.replit.app`.

A generic ASGI server can run the entrypoint. For example, a host may install Uvicorn and point it
at `staging_app:app`; Uvicorn is intentionally not made a core ACC dependency.

## Isolated fixtures

- account: `staging-account`
- project: `staging-project`
- user: `staging-user`
- worker: `worker-1`
- `tasks.active` quota: 25
- session lifetime: 15 minutes

Everything is process-memory only and disappears when the staging service restarts.

## Staging session bootstrap

The console may request a temporary ACC session:

`POST /staging/session`

Headers:

- `Origin`: must exactly match the configured staging Origin;
- `X-ACC-Staging-Secret`: must equal the host-only staging bootstrap secret.

The response returns a normal opaque M08 ACC bearer session plus the staging account/project IDs.
The secret is not an ACC session and is never stored in M08 state. Do not reuse a production
credential as the bootstrap secret.

## End-to-end path

After bootstrap, the browser uses the ordinary accepted M09 contract:

1. GET project state.
2. POST event-ticket.
3. connect WSS with the one-time ticket.
4. POST `task.create` command.
5. receive `command.applied` over the same event source.
6. disconnect/reconnect using the durable cursor and verify replay semantics.

The command repository is also the event source and the read projection reads from that same
in-memory state, preventing the staging composition from accidentally testing three unrelated
mocks.

## Health

`GET /health` returns only non-secret staging identifiers/status. It is intended for hosting
health checks.

## Safety

This module must remain staging-only. It does not weaken M08 login or add a production bypass.
Production still needs durable repositories, real identity provisioning, billing-backed
entitlements, rate limits and deployment infrastructure.
