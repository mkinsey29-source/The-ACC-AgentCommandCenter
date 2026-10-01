# ACC Web on ChatGPT Sites — feasibility experiment

This is an isolated deployment experiment, not the production ACC Web application.

## Question being tested

Can ChatGPT Sites host the **full ACC Web client shell** while the heavy ACC control plane remains on an external backend?

The experiment intentionally exercises more than a landing page:

- persistent ACC command-center shell;
- Master Control;
- 9 workspace modes;
- 96 task records;
- agent/status panels;
- Attention rail;
- Orchestrator Chat;
- Play-by-Play;
- responsive desktop/tablet/mobile layout;
- external HTTPS state loading;
- external WebSocket event stream seam;
- 2,000-event browser stress test.

The experiment does **not** pretend M08/M09 are finished. Live mode is a transport seam waiting for a real hosted ACC staging backend.

## Local checks

From this directory:

    npm run check
    npm run build

There are no npm dependencies. `build` copies the static client into `dist/`.

Open `index.html` through any static server. Demo mode works with no backend.

## Actual Sites runtime test

Sites provisioning/deployment cannot be done through a standalone CLI/API from this chat. Open this branch/project in ChatGPT Work or Codex on desktop and use the prompt in `SITES-DEPLOY-PROMPT.md`.

Keep the first deployment owner-only/private.

### Runtime acceptance matrix

1. Sites recognizes the existing project and can save a version.
2. The saved version builds without rewriting the architecture.
3. Owner-only deployment succeeds and returns a production URL.
4. Desktop layout preserves all three columns plus the bottom console.
5. Tablet/mobile layouts remain usable.
6. All 9 workspace tabs switch without navigation errors.
7. Demo event stream remains responsive for five minutes.
8. `Stress test` completes 2,000 event insertions without the UI becoming unusable.
9. Browser storage retains only endpoint configuration; the test bearer token is session-only.
10. External HTTPS can reach a public staging endpoint when M09/staging exists.
11. External WSS can receive live events when M09/staging exists.
12. A saved version can be redeployed without changing the Site URL.
13. Site analytics become visible after test visits where supported.
14. Sign in / production authorization is not considered passed until M08 provides the ACC auth contract.

## Interpretation

- **PASS for frontend hosting:** items 1–8 succeed. Sites is suitable as an ACC Web hosting candidate.
- **PASS for live ACC:** items 9–11 plus the future M08/M09 authentication tests succeed.

## Live-connectivity staging backend

`staging/` now contains an isolated public-backend fixture for the next test. It uses synthetic ACC
state only, bearer auth for HTTPS, a one-time short-lived WebSocket ticket, an explicit Sites Origin
allowlist, and no local ACC token.

Local staging verification: **6/6 tests pass** plus Python compilation.

The deployed Site client now obtains a ticket from `POST /api/ws-ticket` before opening the
WebSocket. This avoids placing the longer-lived staging bearer token in the WebSocket URL.

A public host is still required to turn the local staging fixture into HTTPS/WSS endpoints.
- **PARTIAL:** Site hosting works but live backend/auth is blocked by unfinished ACC modules.
- **FAIL:** Sites cannot deploy the compatible project, cannot sustain the shell interaction load, or blocks the required HTTPS/WebSocket transport.

Do not choose Sites as the only ACC Web host from this experiment alone. Preserve a conventional-host fallback until live authentication, reconnect behavior, beta usage limits, and production operations are proven.
