# ACC module checklist

**Source of truth:** `main` plus `docs/ACC-PLATFORM-DIRECTION-2026-09-29.md`.  
**Rule:** `READY FOR REVIEW` is not `COMPLETE`. A module becomes `COMPLETE` only after independent
top-level review, integration, and its acceptance checks pass on the integrated revision.

| ID | Module | Status | Hard dependencies | Notes |
|---|---|---|---|---|
| M00 | Repository cleanup & salvage | IN PROGRESS | — | UI-drafts and PR #25 have salvage decisions; obsolete branches cleaned separately. |
| M01 | Shared domain/contracts | READY FOR REVIEW | M00 | `acc/contracts.py`; shared provider/connector protocols and capability syntax. |
| M02 | State authority & synchronization contract | READY FOR REVIEW | M01 | Platform shared-state authority; node secret/resource authority; offline revision envelopes. |
| M03 | General work/task/capability model | READY FOR REVIEW | M01 | Existing task model generalized without breaking coding callers. |
| M04 | Worker & Decision providers | PARTIAL | M01, M03 | Existing adapters/routing remain; migrate behind contracts next. |
| M05 | Connector/provider framework | READY FOR REVIEW | M01, M03 | Facets/boundaries/state semantics implemented; individual connectors remain later modules. |
| M06 | Knowledge Engine / Second Brain | PARTIAL | M05 | Strong Obsidian lifecycle exists; storage backend must be generalized to `KnowledgeProvider`. |
| M07 | Review/evidence/acceptance engine | PARTIAL | M03 | Strong coding path exists; general artifact acceptance remains. |
| M08 | Accounts/auth/permissions/entitlements | NOT STARTED | M01, M02 | Required for hosted product. |
| M09 | Hosted Platform API/events | PARTIAL | M01, M02, M08 | Current API is loopback/local only. |
| M10 | Global Command Center UI shell | DESIGN PARTIAL | M09 | Preserve command-center shell, Master Control, Orchestrator Chat, Play-by-Play, Attention. |
| M11 | Workspace framework | NOT STARTED | M10 | Defines center/rail modules and connector recommendations. |
| M12 | Individual workspaces | PARTIAL | M11 | Coding draft exists; other workspaces not built. |
| M13 | ACC Plugin | NOT STARTED | M08, M09 | ChatGPT/Codex discovery/control surface. |
| M14 | OpenAI baseline workers | PARTIAL | M04, M09 | Existing OpenAI-facing pieces do not yet satisfy new platform baseline. |
| M15 | Storage/workspace providers | PARTIAL | M05, M06 | Obsidian exists; ACC-managed/local/Drive adapters remain. |
| M16 | App/service connectors | PARTIAL | M05 | Build independent adapters against shared contract. |
| M17 | Engine/tool connectors | PARTIAL | M05 | Blender/Unity represented; Unreal planned. |
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
