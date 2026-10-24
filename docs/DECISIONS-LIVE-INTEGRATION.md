# Decisions shadow adapter and Sentry read-only voice prototype

Task DL-20261024; Codex implementer/self-validation, October 24, 2026 America/New_York. This is an opt-in implementation slice, not an activated integration or an accepted Android voice feature. No production or staging entrypoint imports `acc/experimental`. Existing providers, routing, authentication, databases, Android source, signing and OTA settings are unchanged. No mandatory reviewer gate, merge, deployment, scheduler edit, API test or device test.

## Sources and branch reconciliation

| Source | Observed state / use |
| --- | --- |
| ACC live branches | Active Android branch `feature/android-build9-claude-github-20261021`, head `6f70bf4587246cd2d38e6908f79db0bb2de8b43a`; isolated implementation branch `feature/decisions-live-shadow-20261024` starts there. Main remains `66db9181dc6ac01f267cd00dd4e9b71c23af2b6f`. |
| Current handoff | `mobile/session-notes/android-build9-20261021/handoff.md` and lessons read. Native/CI evidence belongs to source `99376eb0d33f528a1e8f5aae9dd81af480413fc9`, distinct from later documentation head. |
| SkillOpt | Only live branch main, head `1a44050fc53aa4938a7a3927b6cca550491bfe51`; new branch `feature/decisions-shadow-companion-20261024`. No repository AGENTS.md/current project handoff found. Its README, sleep/backend/evidence interfaces and current Drive policies were read. |
| Drive instructions | [Root v3.2](https://drive.google.com/file/d/1BPuaOz5tZ3wDrhoGuBmgWEgQ4evGI35U/view), [ACC v3.4](https://drive.google.com/file/d/1DShX5lan_mvZhgMeXVofd21z4RGGNmk0/view), repository/module policies. Owner single-agent exception supersedes obsolete reviewer/PR35 wording in older branches. No policies edited. |
| ACC continuity | [Baseline](https://drive.google.com/file/d/1DTEePzI4USQGdCTXx7NPJ-39cPWAMWf4/view), [canonical handoff](https://drive.google.com/file/d/17UVhtgeKrD5kPsmoncehNRGfpOsq84Uu/view), live review folder, newer repository Build9 handoff. Canonical Drive records lag current code. |
| Orchestrator report | [October24 10:12 Central, as of15:12UTC](https://drive.google.com/file/d/1uBxDlqJt2-7wKzA_1pX7e9EmMhq-gkNC/view). Reading it is not a fresh cross-project audit. |
| SkillOpt/Second Brain | [Learning AGENTS v1.1](https://drive.google.com/file/d/1hYo9u8ksqenB7Ia8iLA_h1XvqrRQFW3c/view), current installed learning instructions/source map, [capture/scoring contract](https://drive.google.com/file/d/14Y3GZFEl6jPT3xzqxx9uSOn8BUOMRji6/view), [October24 baseline](https://drive.google.com/file/d/1ViyPFwS2TBFUEx-XLgiLIxeZZtDQ-pFv/view), [Second Brain AGENTS v3.2](https://drive.google.com/file/d/1VM5OEzBfDF8Okwfue0jvqtBd0za5s0B9/view), [handoff](https://drive.google.com/file/d/1VM4v7OfIV3nT_kFN19uiXqUXtquE35dp/view). New baseline supersedes old handoff progress claims. |
| Collector | [Current configuration v2.3](https://drive.google.com/file/d/1hu4e4xWdIq2nrcVppPN7kiAM_lPrpzrY/view), updated through October23. All capture/recovery/checkpoint rules unchanged. |

Collector automation ID `6acfafafb2f08191b6bca7684c4555ac` was looked up using the configuration's exact identity. The tool returned “not found or not accessible in the current context.” This does not prove deletion or inactivity. No scheduler mutation, replacement task or workaround was attempted.

## Integration map

| Area | Existing seam | New seam / explicit limit |
| --- | --- | --- |
| Shared Decisions | Existing stdlib provider HTTP pattern and `DecisionProvider` concept in `acc/contracts.py` | `DecisionsAdapter.observe(Context, minimized_facts, existing_result)`; typed sources/criteria, named choices, schema/probability validation, timeout/fallback, off/shadow only. Async observation does not alter the frozen synchronous provider protocol or register a worker. |
| Application Orchestrator | `acc/orchestrators.py`, `acc/routing.py::AgentRouter`, optional TypeSafe evaluator and SQLite `agent_outcomes` | `experimental/consumers.py::observe_orchestrator` accepts a frozen snapshot after all inventory reads. Recommendation accompanies unchanged result. No routing/TypeSafe replacement or automatic dispatch. |
| Chase tasks | Existing Chase-owned Orchestrator/Collector runtime | Separate runtime; no supported authenticated invocation from Chase into this backend was established. DL-03 stays blocked. Editing a prompt/configuration is not API integration. |
| Sentry reads | `PlatformApi.project_state`, `AuthService.authorize`, `PlatformReadRepository.project_snapshot`; existing permissions and `acc.web` entitlement | `SentryReads` narrows a fresh authenticated snapshot to task IDs/statuses and attention IDs/severity. Text and voice use this read facade. No command/coordinator/dispatcher call. |
| Voice | `acc/voice.py` local recording/transcription, Android direct ChatGPT sign-in, optional advanced runtime, official Claude browser handoff | Separate `PrototypeApplication`, WebRTC browser and backend-owned Live sideband. GPT-Live manages speech; client delegation calls our read facade. Working voice/provider choices remain unchanged. |
| Persistence | Backend M09 SQLite projections; Android `mobile/src/storage.ts`, device-local Sentry Markdown and tasks | Existing backend reads only; no schema or state writes. Android local records are **not proven mirrored** into this backend. Native read-only mapping/APK/device acceptance remain DL-04. No automatic memory upload. |
| SkillOpt | `skillopt_sleep/backend.py`, `evidence.py`, cycle/judge/gate/intake paths | New `decision_shadow.py::observe_attempt` takes the shared ACC adapter explicitly. Not an optimizer Backend, scorer, collector, checkpoint or adoption hook. Cross-repo test uses actual companion+adapter. No second Decisions implementation or SkillOpt SDK dependency. |

The requested three category groups are defined in `CATEGORIES`; SkillOpt also asks one bounded workflow-family question. Recommendations never grant permission or change evidence. Even a guarded `verified_outcome` recommendation leaves `verified_completion=false` and the existing outcome unchanged. Missing scope/inventory stops before provider access. Unresolved IDs, gates, denied writes, deferred tests and source/as-of times are copied intact.

## Official documentation reviewed

- [Decisions](https://developers.openai.com/api/docs/guides/decisions), [create reference](https://developers.openai.com/api/reference/resources/decisions/methods/create): `POST /v1/decisions`, `gpt-6-luna`, named choice answers with probabilities/confidence. Invalid/refused/ambiguous answers fall back.
- [Live delegation](https://developers.openai.com/api/docs/guides/live-delegation): `gpt-live-1`, client delegation; transcript deltas carry speech text, delegation metadata carries IDs. Return bounded commentary using that delegation ID.
- [Decisions voice](https://developers.openai.com/api/docs/guides/decisions-voice): native Decisions delegation exists; it is not enabled here because our backend retains control of context/results.
- [WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc), [server controls](https://developers.openai.com/api/docs/guides/voice-server-controls): trusted-server SDP exchange, attached sideband, graceful session.close/session.closed. The SIP-specific HTTP hangup route is not used for WebRTC.

Routes are documentation-verified, not tested against a real API project. No SDK upgrade. Optional sideband dependency is isolated in `acc/experimental/requirements-voice.txt`.

## Security and data flow

Existing ACC auth, tenant/project ownership, exact HTTPS Origin, size/time bounds and strict JSON checks precede reads/session creation. No bootstrap identity or secret endpoint. Host privately in one process; no wildcard CORS.

The Decisions caller must supply already scoped/minimized facts, excluding secrets/unrelated content **before** invocation. This is not a universal redactor or discovery service. Unknown scope short-circuits before serialization. API keys are resolved only server-side through `OPENAI_API_KEY`; no key was found here. Organization/project headers are explicit. HTTP origin is fixed to OpenAI, redirects/retries are disabled and bodies/timeouts bounded.

The browser uses an existing ACC backend session token in memory only, never a provider key or ChatGPT account token. No credentials in URLs, browser storage, APK, Git or reports. Audio goes to OpenAI only after deliberate Start voice plus server enablement. Backend transcript buffers are transient. Only bounded authenticated status facts return to Live; no transcript/audio files are saved. The text companion includes evidence URL/version/hash, observed/as-of time, unresolved IDs and explicit unknown original source time. Recorded statuses are not independently verified completion.

Audit objects contain source references/versions/hashes, source/as-of times, model/config, elapsed latency, numeric usage and bounded error codes, excluding raw facts/transcripts/provider exception text. They return beside results and are not appended to Collector/SkillOpt state. Durable audit persistence can later use an explicitly selected existing evidence path.

Server sideband handles transcript corrections concurrently with reads, rejects duplicate delegation IDs and fences late results by revision, rechecking auth before returning results. Browser mutes previous audio upon new transcript input and only unmutes a companion covering that input timestamp. Real speech/playback remains untested. Current intent fallback accepts a small exact status/blocker grammar; other speech asks clarification. There is no general planning/provider/task execution capability.

Defaults: 45-second idle timeout, 180-second absolute session limit, one session globally. Only user input refreshes idle time. Browser stop/background/disconnect, server auth revocation/off switch/watchdog/shutdown request graceful close. Unknown creates/closes stay held and are never automatically retried. Process/network loss may leave remote closure/final usage unconfirmed; the local watchdog is not a guaranteed vendor billing cap.

Real calls require explicit `ApiTestPermit` account/project, dollar allocation, conservative per-request reservation and max request count. Failed requests consume reservations. These are local experiment controls, **not measured cost or an enforceable vendor dollar cap**. Before testing, reconcile current rates, maximum session exposure and the owner's limit. Never assume ChatGPT subscription coverage.

## Reproducible tests

From ACC:

```bash
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY python -m unittest discover -s tests -p 'test_decision_support.py'
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY python -m unittest discover -s tests -p 'test_sentry_prototype.py'
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY SKILLOPT_CHECKOUT=/absolute/SkillOpt python -m unittest discover -s tests -p 'test_skillopt_decisions_bridge.py'
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY python -m unittest discover -s tests -p 'test_platform_api.py'
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY python -m unittest discover -s tests -p 'test_mobile_runtime.py'
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY PYTHONPATH=. python tests/evaluate_decision_support.py
node --check acc/experimental/web/sentry.js
```

From SkillOpt:

```bash
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY python -m unittest discover -s tests -p 'test_decision_shadow.py'
```

Passed self-validation:12 Decisions/HTTP,14 Sentry/app/sideband,1 cross-repo,2 SkillOpt tests; nearby regressions25 existing PlatformApi +14 existing mobile runtime tests. JavaScript syntax/Python compilation pass. Existing source/tests are untouched. No APK rebuild, independent review, real API call, rendered browser/audio test or device test is claimed.

[Fourteen frozen cases and comparison](session-notes/decisions-live-20261024/mock-comparison.json): unchanged/changed, stale summary, denied write, missing scope, recovered history, unsupported success, CI-only, ambiguity, failure, blockage, inventory gaps and Sentry routing/approval intents.14/14 guard results match;14/14 original results preserved; zero lost IDs, unauthorized actions or claim promotions. Mock false alerts/missed changes are zero for the supplied fake answers. Local mock latency is recorded, not vendor latency. Real model accuracy/error rates, real workflow comparison, API latency and measured cost remain null/not run. API requests/spending: zero.

## Activation checklist — stop before live testing

- [ ] DL-01: owner identifies API organization/project, confirms secure server secret injection, and chooses secure key reuse/provisioning. Never put a key in chat.
- [ ] DL-02: owner gives explicit dollar test cap; agree request/session limits, duration, current rates, conservative exposure and abort conditions.
- [ ] Verify Decisions/Live access and API permissions in that exact project within the approved allocation; run frozen fixtures and label real API results separately.
- [ ] Compare actual recommendations against expected labels and the same existing-workflow results: false alerts, missed changes, abstentions, p50/p95 latency and measured cost with pricing/as-of attribution. Require zero ID loss, unauthorized actions and claim promotions.
- [ ] Verify real sideband/protocol, corrections, stale context, backend failure, revocation, disconnect/background, final usage/closure and text fallback. Mock sockets do not satisfy this.
- [ ] DL-03: prove a supported authenticated call route in the existing Chase-owned runtime; preserve owner/runtime/schedule and every inventory check.
- [ ] DL-04: define approved read-only Android status mapping and verify exact APK/device mic/playback/lifecycle. No automatic memory upload.
- [ ] Preserve all source times, gates, deferred tests and unresolved IDs. DL-05 summary overwrites remain denied/unresolved.
- [ ] Merge, deployment and scheduled activation need separate authorization; this prompt grants none.

## Composition and rollback

For a later authorized private test, construct `PrototypeApplication(existing_platform_api, origin=private_https_origin)` using existing auth/read state. It serves `/experimental/sentry` and same-origin account/project reads; voice remains off. An already provisioned ACC backend session is required; this is not a replacement default onboarding flow. After DL-01/02 only, explicitly construct `OpenAIHTTP(permit)`, use it with `DecisionsAdapter(Config(mode='shadow'), http)`, and separately opt into `live_http=http, voice_enabled=True`. No production/staging factory or scheduler was wired here.

Rollback needs no migration: keep the composition unmounted, `Config()`/mode off and voice disabled. An active private test must run `await app.live.shutdown()` and confirm closure before removing its server. On uncertainty retain the session identity and reconcile usage/closure before another create. Added files/feature branches can be removed without restoring any provider, collector, database, optimizer, APK or ledger state.
