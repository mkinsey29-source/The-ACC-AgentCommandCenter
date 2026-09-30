# ACC module checklist

**Source of truth:** `main` plus `docs/ACC-PLATFORM-DIRECTION-2026-09-29.md`.  
**Rule:** `READY FOR REVIEW` is not `COMPLETE`. A module becomes `COMPLETE` only after independent
top-level review, integration, and its acceptance checks pass on the integrated revision.

| ID | Module | Status | Hard dependencies | Notes |
|---|---|---|---|---|
| M00 | Repository cleanup & salvage | IN PROGRESS | — | UI-drafts and PR #25 have salvage decisions; obsolete branches cleaned separately. |
| M01 | Shared domain/contracts | COMPLETE (v1) | — | `acc/contracts.py`; shared provider/connector protocols and capability syntax. Reviewed and integrated in PR #27. |
| M02 | State authority & synchronization contract | COMPLETE (v1) | M01 | `acc/state_authority.py`. Platform shared-state authority; node resource authority; credentials stay with their holder; online-only leases; read-only account replicas; offline revision envelopes. Contract only: the sync transport is M09/M21. |
| M03 | General work/task/capability model | COMPLETE (v1) | M01 | `acc/domain.py`; existing task model generalized without breaking coding callers; direct and conversational creation share one normalizer. |
| M04 | Worker & Decision providers | PARTIAL | M01, M03 | Existing adapters/routing remain; migrate behind contracts next. |
| M05 | Connector/provider framework | COMPLETE (v1) | M01, M03 | `acc/connectors.py`; facets/boundaries and atomic configured/healthy/enabled state. The in-memory registry is not yet persisted or wired into routing (M04/M16). Individual connectors are later modules. |
| M06 | Knowledge Engine / Second Brain | PARTIAL | M02, M05 | Strong Obsidian lifecycle exists; storage backend must be generalized to `KnowledgeProvider`. |
| M07 | Review/evidence/acceptance engine | PARTIAL | M03 | Strong coding path exists; general artifact acceptance remains. |
| M08 | Accounts/auth/permissions/entitlements | NOT STARTED | M01, M02 | Required for hosted product. |
| M09 | Hosted Platform API/events | PARTIAL | M01, M02, M08 | Current API is loopback/local only. |
| M10 | Global Command Center UI shell | DESIGN PARTIAL | M09 | Preserve command-center shell, Master Control, Orchestrator Chat, Play-by-Play, Attention. |
| M11 | Workspace framework | NOT STARTED | M10 | Defines center/rail modules and connector recommendations. |
| M12 | Individual workspaces | PARTIAL | M11 | Coding draft exists; other workspaces not built. |
| M13 | ACC Plugin | NOT STARTED | M08, M09 | ChatGPT/Codex discovery/control surface. |
| M14 | OpenAI baseline workers | PARTIAL | M04, M09 | Existing OpenAI-facing pieces do not yet satisfy new platform baseline. |
| M15 | Storage/workspace providers | PARTIAL | M05, M06 | Obsidian exists; ACC-managed/local/Drive adapters remain. |
| M16 | App/service connectors | PARTIAL | M02, M05 (hosted grants: M08) | Build independent adapters against shared contract. Local/node-held credentials can start now; platform-held OAuth grants need M08. |
| M17 | Engine/tool connectors | PARTIAL | M05 (remote dispatch: M18) | Blender/Unity represented; Unreal planned. Code against `ExecutionNode`; runs locally until M18 exists. |
| M18 | Desktop/local execution node | NOT STARTED | M02, M05, M09 | Tauri + background local execution node. |
| M19 | Orchestrator/connection layer | PARTIAL | M09, M13 | Salvage generic health telemetry from PR #25; retire Remote-specific assumptions. |
| M20 | Usage/cost/performance | PARTIAL | M04, M09 | Existing routing evidence is a base. |
| M21 | Reliability/offline/recovery | PARTIAL | M02, M04, M09, M18 | Existing local recovery strong; hosted/offline sync remains. |
| M22 | Commercial packaging/onboarding | NOT STARTED | M08, M13 | Account, entitlements, connector suggestions, Desktop download. |
| M23 | System integration/release gates | NOT STARTED | Required release modules | OpenAI-only and extended-customer acceptance scenarios. |

## Parallelization gate

After M01/M02/M03/M05 are independently reviewed and integrated, the following lanes can proceed
largely in parallel against frozen contracts:

- M04 Workers/Decisions
- M06 Knowledge
- M07 Review/Evidence
- M08 Auth
- M09 Platform API
- M10/M11 UI shell/workspace framework after M09 contract exists
- M13 Plugin after M08/M09
- M15/M16/M17 connector implementations after M05

Each module should own its files, list the spine contracts it consumes, and provide focused contract
tests before asking for review.

## Parallel assignment map (after spine v1)

Every lane consumes the frozen v1 spine: `acc/contracts.py`, `acc/domain.py`,
`acc/state_authority.py`, `acc/connectors.py`. **No lane may change those files.** A spine change
needs a spine-version bump and its own independent review.

`acc/core.py`, `acc/server.py`, `acc/bridge.py`, `acc/conversation.py` and `acc/workflow.py` are
shared integration points. A lane may make small, additive edits there (register a route, a tool,
or a hook), and must rebase on current `main` right before review, because two lanes touching
the same function will conflict.

| Lane | Owned paths (new or primary) | May also change | Hard deps | Acceptance tests |
|---|---|---|---|---|
| M04 Workers/Decisions | `acc/routing.py`, `acc/worker_prompt.py`, driver modules (`acc/<driver>.py`), new `acc/decisions/` | additive hooks in `acc/workflow.py` | M01, M03 | `test_routing` (run with `TYPESAFE_API_KEY` unset), all adapter tests, and a `DecisionProvider` conformance test that includes the deterministic fallback |
| M06 Knowledge | `acc/knowledge.py`, new `acc/knowledge_providers/` | `KnowledgeProvider` adapters only | M02, M05 | `test_knowledge`, and checkout/check-in via both the vault and one new provider |
| M07 Review/Evidence | new `acc/evidence.py`; review/acceptance paths in `acc/workflow.py`, `acc/controls.py` | additive fields on review records | M03 | `test_workflow`, `test_readiness_review`, `test_job_backed_workflow`, and non-code artifact acceptance |
| M08 Auth/entitlements | new `acc/auth/` | none in the local server | M01, M02 | Token and entitlement tests. Credentials follow `policy_for('credential')`. |
| M09 Platform API/events | new `acc/platform/` (hosted service) | none in the loopback `acc/server.py` | M01, M02, M08 | API contract tests, and sync-envelope conflict/idempotency tests |
| M15/M16/M17 Connectors | new `acc/connector_packs/<id>.py`, one file per connector. Not `acc/connectors/`, which would shadow `acc/connectors.py`. | the catalog entry's `implemented` flag for that connector only | M05 (+ M02/M08/M18 per row above) | Per-connector conformance tests against its facet protocols, plus the atomic state-transition test |

Known pre-existing test issues that are **not** lane regressions (see `60_Review/LESSONS.md`):
`test_routing.test_deterministic_policy_routes_without_credential_or_approval` fails whenever a real
`TYPESAFE_API_KEY` is set in the environment.
`test_conversation.test_switch_during_direct_turn_queues_safe_handoff_then_activates` fails
intermittently on `main` too; its teardown can run before the runner's exit is confirmed.

