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

Bootstrap rules (enforced and tested in `tests/test_staging_e2e.py`):

- The secret is read **only** from the `X-ACC-Staging-Secret` header, never from the query string,
  body or `Authorization`. It is compared in constant time and never appears in responses, errors,
  `repr`, health output, audit or event data.
- A wrong, missing or repeated secret header returns the generic
  `401 authentication_required`. A wrong, missing or repeated `Origin` returns `403` with no CORS
  headers.
- `OPTIONS /staging/session` answers the browser's CORS preflight (the custom header forces one)
  for the configured Origin only, allowing `POST` and the `X-ACC-Staging-Secret`/`Content-Type`
  headers. No `Access-Control-Allow-Credentials` is ever sent.
- The bootstrap cannot choose an account, user or project: the staging verifier maps the secret to
  the single staging identity, and M08 `exchange_identity` issues an ordinary 15-minute session for
  `staging-account`. Every later request goes through normal M08/M09 authorization, so the bearer
  cannot reach another account or project.
- `StagingVerifier` is composed only by `StagingApplication`; no production composition registers
  it, and importing `acc.staging` has no side effects.

**The secret is a tester credential, not part of the published console.** Generate it with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`, store it only as a host secret, and
have the tester enter it into the running console (memory or `sessionStorage`). Never bake it into
the published console bundle, a URL or a repository.

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

`GET /health` (and `HEAD`) returns only `{"status", "service", "account_id", "project_id"}`.
It is **deliberately public**: hosting health checks send no `Origin`, and the fixed identifiers are
not secret. It never returns CORS headers, session data, repository contents or environment values.
Other methods return 404.

## Deployment requirements

- **ASGI entrypoint:** `staging_app:app`. Any ASGI server works; for example
  `pip install uvicorn` then
  `uvicorn staging_app:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers`.
  Uvicorn is a hosting dependency only, not an ACC dependency.
- **Environment:** `ACC_STAGING_BOOTSTRAP_SECRET` (required, 24+ characters; generate as above).
  `ACC_STAGING_ORIGIN` is optional and defaults to the console Origin below. Missing or malformed
  values stop the process at import, before it serves anything.
- **Single process:** run exactly one worker process. Sessions, WebSocket tickets, commands and
  events all live in that process's memory, so a second worker would reject the first worker's
  sessions and tickets. Restarting resets all staging state; that is intended.
- **Blocking calls:** M09 API calls are synchronous. For this small in-memory test that is
  acceptable; the production runtime must move them off the event loop.
- **TLS:** terminate HTTPS/WSS at the host. The console must use `https://` and `wss://`.
- **Published console Origin:** `https://acc-staging-console--memph1510.replit.app` (exact).
- **Fixture:** `staging-account` / `staging-project` / `staging-user`, `worker-1`, `tasks.active` 25.
- **Rate limits (host requirement):** limit `POST /staging/session` (for example 10/minute per
  client IP) and concurrent WebSocket connections at the host or proxy. With a 32-byte random secret
  brute force is not practical, so this is not a code blocker, but each successful bootstrap creates
  an in-memory session.
- **Lifetime:** take the service down after the browser test; it is not a long-running environment.

## Safety

This module must remain staging-only. It does not weaken M08 login or add a production bypass.
Production still needs durable repositories, real identity provisioning, billing-backed
entitlements, rate limits and deployment infrastructure.
