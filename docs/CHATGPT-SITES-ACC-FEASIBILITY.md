# ChatGPT Sites as an ACC Web hosting candidate

**Date checked:** 2026-09-29 America/Chicago  
**Status:** Source/runtime experiment prepared; actual Sites deployment still required.

## Architecture interpretation

ACC does not need Sites to run the ACC backend. Sites only has to host the browser client.

```text
ChatGPT / Codex
      |
   ACC Plugin
      |
   ACC Platform  <---->  ACC Desktop/local nodes
      |
  HTTPS / WSS
      |
ChatGPT Sites
  ACC Web UI
```

Keep canonical tasks, workers, leases, knowledge authority, provider credentials, billing,
background processing, and local resource truth outside the Site.

## Current Sites capabilities relevant to this test

The current OpenAI Sites developer/help documentation describes Sites as capable of hosting
compatible existing web projects and documents HTTP/HTTPS/WebSocket use, saved versions and
production deployments, hosted environment values/secrets, D1/R2 storage, identity options,
analytics, optional custom domains, and Site-hosted MCP/plugin workflows. Sites remains a beta
surface with plan/workspace limits and unsupported hosting patterns.

This experiment deliberately tests only the browser/runtime-hosting portion first. D1/R2 are not
used in the first pass because they are not required for ACC's canonical state.

## Experiment

Branch: `temporary/chatgpt-sites-acc-feasibility`

Source: `experiments/chatgpt-sites-acc/`

The experiment uses no third-party runtime dependencies and deliberately includes the stable ACC
shell concepts plus enough task/event volume to catch obvious browser/runtime problems:

- Master Control;
- 9 workspaces;
- Attention and worker rails;
- Orchestrator Chat and Play-by-Play;
- 96 task records;
- continuous demo events;
- a 2,000-event browser stress fixture;
- public-HTTPS and WSS transport seams for a future M08/M09 staging backend.

### What is proven before Sites provisioning

- JavaScript syntax passes;
- the source bundle builds without dependency installation;
- no localhost/127.0.0.1 hard dependency exists;
- external HTTPS and WebSocket seams exist;
- responsive layout source exists;
- no production credential is committed;
- source verifier confirms the task/event stress fixture is present.

Local verification result on 2026-09-29:

```text
status: ok
source bytes checked: 25,420
runtime dependencies: none
HTTPS seam: present
WebSocket seam: present
task fixture: 96
stress fixture: 2,000 events
build: dist/index.html + dist/styles.css + dist/app.js
```

### Frontend runtime result — PASS

The owner-only ChatGPT Site deployed successfully and the actual shell was exercised on Marvin's
phone. The browser stress fixture reported:

```text
PASS · 2,000 events · avg center render 0.9 ms · total 346 ms
```

Mobile workspace switching and Orchestrator Chat also worked. This clears the frontend-hosting
performance gate by a large margin. The remaining feasibility question is live public HTTPS/WSS
connectivity and production-grade authentication.

### Live-connectivity staging fixture

`experiments/chatgpt-sites-acc/staging/` now provides a synthetic ACC backend specifically for that
next test:

- `GET /api/state`: bearer-authenticated ACC-shaped state;
- `POST /api/ws-ticket`: short-lived one-time WebSocket ticket;
- `GET /events?ticket=...`: live WebSocket events;
- explicit Sites Origin allowlist;
- synthetic data only;
- no local ACC control token;
- host-neutral Dockerfile.

Local verification: **6/6 staging tests pass** and the server compiles.

The remaining operational step is to deploy this fixture to a public TLS-terminating host and put its
HTTPS base URL plus a new staging-only token into the Site's Connection panel.

### What only the Sites runtime can prove

- Sites recognizes/builds the existing project;
- owner-only deployment succeeds;
- actual bundle/build timeout limits;
- rendered desktop/tablet/mobile quality;
- browser performance under the stress fixture;
- actual external HTTPS/WSS egress behavior;
- version/redeploy workflow;
- Site analytics/custom-domain behavior;
- Sites identity integration.

## Decision rule

Do not replace conventional ACC Web hosting based on documentation alone.

If the owner-only Site successfully deploys the experiment, remains responsive under the stress
fixture, and later connects cleanly to M08/M09 staging over HTTPS/WSS, Sites becomes a preferred
hosting candidate for the OpenAI-native ACC Web surface.

If it fails those runtime tests, keep Sites for project mini-apps/plugin hosting and use conventional
hosting for ACC Web.
