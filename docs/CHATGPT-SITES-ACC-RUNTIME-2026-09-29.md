# ACC Sites runtime result — 2026-09-29

Tags: #acc #sites #testing #cloud-environment

## Result

**PARTIAL: owner-only hosting succeeded; browser acceptance remains unverified.** Do not mark S01 passed or PR #28 ready to merge yet.

Production URL: https://acc-web-feasibility.marvinkinsey.chatgpt.site

The supplied frontend was preserved byte-for-byte. Only `.openai/hosting.json` was added to the separate Sites checkout, declaring `static.directory: dist` and the registered project ID. No D1, R2, connectors, secrets, or backend tunnels were added. The source GitHub branch was not deployed as the Sites repository; its isolated experiment was copied into the Sites-owned source repository.

## Saved and deployed identity

- GitHub source: `mkinsey29-source/The-ACC-AgentCommandCenter`, branch `temporary/chatgpt-sites-acc-feasibility`, directory `experiments/chatgpt-sites-acc/`.
- Site project: `appgprj_6abc82b1fb708191a6b2df66a6602c02`.
- Saved version: `appgprj_6abc82b1fb708191a6b2df66a6602c02~appgver_46f1a4b635f4819195af919016ec6db5`, version 1.
- Sites source commit: `103952aff4514db9e97efeaca219197761b44f62`.
- Deployment: `appgdep_6abc83bf7ce88191b505c253b7112b5d`, status `succeeded` at `2026-09-30T03:37:44.386090Z`.
- Access was checked: custom allowlist with one owner, no groups. Private deployment also enforced owner-only access.
- Saved version was approved by Marvin before deployment.

## Acceptance evidence

| Gate | Result | Evidence / limitation |
|---|---|---|
| Save existing frontend | PASS | Archive-backed version 1 saved |
| Local check and build | PASS | `npm run check` and `npm run build`, no dependencies |
| Owner-only deployment | PASS | Native private deployment succeeded; owner-only access checked |
| Desktop layout | NOT RUN | Browser testing capability unavailable |
| Tablet/mobile layout | NOT RUN | Browser testing capability unavailable |
| Nine tabs switch | NOT RUN | Nine tab definitions and handler present; no browser interaction claim |
| Five minutes demo streaming | NOT RUN | 1,100 ms interval in source; no browser soak claim |
| 2,000-event stress | NOT RUN | Fixture verified in source; no measured browser performance |
| Storage policy | SOURCE CHECK ONLY | Endpoint configuration uses localStorage; token uses sessionStorage |
| HTTP/HTTPS APIs | NOT VERIFIED IN SITE RUNTIME | Source uses `fetch`; production URL is HTTPS. This does not prove external requests work. Plain HTTP from an HTTPS page needs separate mixed-content assessment |
| WebSocket API | NOT VERIFIED IN SITE RUNTIME | `new WebSocket` is present; no connection attempted |
| Redeploy stable URL | NOT RUN | Saved version retained; only first publication performed |
| Analytics | NOT RUN | No browser visits made by this agent |
| Production ACC auth | OUT OF SCOPE | Waits for M08/M09 staging contract |

No private/local ACC backend was contacted. No external staging endpoint was contacted.

## Source observations to check visually

The supplied CSS hides Attention/active workers at widths <=1,050 px and Master Control at <=720 px. No alternate mobile access is implemented. The tablet header places actions in the same grid row with a negative margin. Several text sizes are 10–13 px. These are source observations, not measured rendering failures; the design was deliberately preserved for this approved first hosting test.

## Issues and lessons

### Prompt was not on the default branch
**Status:** Resolved.
**Symptom:** Local workspace had no prompt; default-branch file fetches returned 404.
**Root cause:** The experiment resides on the dedicated feasibility branch.
**Fix:** Listed branches and fetched the exact file on `temporary/chatgpt-sites-acc-feasibility`.
**Lesson:** Resolve the branch as well as the repository for isolated deployment experiments.

### Network context blocked Git clone and Sites push
**Status:** Resolved.
**Symptom:** `Failed to connect to browser-proxy port 8889 after 0 ms: Couldn't connect to server`.
**Root cause:** The default command networking context could not reach the configured proxy.
**Fix:** Retried the clone and Sites workflow with approved network escalation. Kept the source credential in memory/stdin; never persisted it.
**Lesson:** A proxy connection error can be an execution-context issue; retry the authorized operation in the permitted networking context.

### Browser acceptance cannot be completed in this runtime
**Status:** Unresolved.
**What was tried:** Read the Sites preview instructions, checked the execution profile (`SITES_MANAGED_LINUX_CONTAINER=1`) and available skills. The required control-browser skill is unavailable. The managed preview guidance also says plain static assets have no compatible development server and live Sites URLs are not reachable from its cloud-browser runtime.
**Blocked on:** An authorized browser testing environment that can exercise this static private Site, or owner testing of the deployed version.
**Lesson:** A successful deployment and source fixture check do not establish browser layout, event-stream stability, stress performance, or external transport availability.

### Drive target resolution
**Status:** Resolved.
**Symptom:** Keyword search with a small result count returned an instruction file rather than LESSONS.md; a remembered review folder ID did not contain the actual reports.
**Fix:** Used the exact `name = 'LESSONS.md'` filter and its returned parent ID to resolve `60_Review/`.
**Lesson:** Use exact name filters and returned parent IDs for shared indexes; do not infer their location from a remembered identifier.

## Next test

Open the production URL as its owner. Test 1,440 px desktop, 1,024/820 px tablet and 390 px mobile; switch all nine tabs; let demo streaming run for five minutes; press Stress test and record its PASS/CHECK text and measured times. Verify fetch and WebSocket availability without contacting any private/local backend. Keep live authentication and real HTTPS/WSS endpoint tests pending until M08/M09 staging exists. Keep the conventional-host fallback and PR #28 draft until the acceptance gate passes.
