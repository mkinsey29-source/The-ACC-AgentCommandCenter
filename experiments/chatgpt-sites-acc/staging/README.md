# ACC Sites live-connectivity staging backend

This server exists only to prove that the deployed ChatGPT Site can communicate with a public ACC-shaped backend over HTTPS and WebSockets.

It is not M08/M09 production infrastructure and must never receive the local ACC control token.

## Endpoints

- `GET /healthz` — public health probe.
- `GET /api/state` — bearer-authenticated synthetic ACC state.
- `POST /api/ws-ticket` — bearer-authenticated, short-lived one-time WebSocket ticket.
- `GET /events?ticket=...` — WebSocket stream. The ticket is consumed on first use.

The browser cannot set an Authorization header during the WebSocket handshake, so the HTTPS-authenticated ticket step avoids putting the long-lived staging bearer token in the WebSocket URL.

## Security constraints

- Explicit browser Origin allowlist.
- Synthetic test data only.
- Separate staging bearer token.
- One-time, expiring WebSocket tickets.
- No credential replication or local ACC token.
- `Cache-Control: no-store`.

## Local verification

```sh
python -m unittest -v test_staging_server.py
python -m py_compile staging_server.py
```

Current local result: 6/6 tests pass.

## Deployment environment

Set:

- `ACC_STAGING_TOKEN` — a new random test-only token.
- `ACC_ALLOWED_ORIGINS=https://acc-web-feasibility.marvinkinsey.chatgpt.site`
- `PORT` — supplied by the host.

The hosting platform must terminate TLS so the public endpoints are HTTPS/WSS.
