# ACC Platform Direction — OpenAI-First, Multi-Agent by Design

**Decision date:** 2026-09-29  
**Status:** Architectural direction. This document supersedes earlier ACC architecture decisions wherever they conflict, but it does **not** claim the announced OpenAI capabilities are already implemented in ACC. Section 17 records which OpenAI capabilities were verified as available, in preview, or without a documented programmatic interface on 2026-09-30.  
**Repository:** `mkinsey29-source/The-ACC-AgentCommandCenter`

## 1. Product decision

ACC is no longer defined primarily as a local desktop command center with a browser companion.

ACC becomes a **platform** with one shared control plane and three first-class user surfaces:

1. **ACC Plugin for ChatGPT/Codex** — discovery, conversational control, voice-driven control where supported, compact project/agent/task views, permissions, and direct access to ACC capabilities.
2. **ACC Web** — the universal, zero-install Command Center. This is the default full UI for Android, iPhone, Chromebook, Windows, Linux, macOS, and any supported modern browser.
3. **ACC Desktop** — an optional Windows/Linux power client and local execution node for capabilities that require or benefit from the user's machine.

All three surfaces use the same ACC account, projects, agent history, task state, decisions, reviews, knowledge, permissions, and usage data.

The desktop application remains important, but it is **not required to begin using ACC**.

## 2. Non-negotiable architecture principle

**ACC must be fully functional using the OpenAI platform alone.**

A customer who only uses ChatGPT/OpenAI should be able to use ACC without installing Claude, DeepSeek, Gemini, Grok, a local model, HarnessX, or any other third-party agent runtime.

At the same time, ACC remains **provider-extensible**. Other agents, harnesses, local models, and creative/development tools are optional capabilities that can improve redundancy, specialization, cost control, experimentation, and user choice.

This means:

- OpenAI is the complete baseline path.
- Other providers are additive, not required.
- No core ACC data model may assume one model vendor.
- No project should become unusable if an optional provider disappears.
- ACC owns project/workforce state; model providers perform work.
- "OpenAI-complete" means OpenAI alone is sufficient, not that OpenAI is always the routed default. When other workers are enabled, the user's routing and cost policy chooses among them (section 8).

## 3. Commercial/product model

ACC is the paid product. The ChatGPT/Codex plugin is a distribution and control surface for that product.

Expected customer flow:

```
Plugin Directory / ChatGPT / Codex
              |
          ACC Plugin
              |
      connect ACC account
              |
       ACC Web immediately
              |
      optional ACC Desktop
```

A user should be able to discover ACC, connect/sign in, and use the web Command Center without downloading software. Desktop installation is offered only when local execution is useful.

ACC billing/entitlements live in the ACC platform. The plugin checks ACC entitlements and exposes the capabilities available to that account. Do not make the architecture depend on in-plugin subscription purchasing; if OpenAI distribution/payment rules expand later, ACC can add that route without changing the core platform.

Where OpenAI supports ChatGPT identity and eligible plan-backed app usage, ACC should use those capabilities to reduce onboarding friction and duplicate API-key setup. These capabilities must remain optional integrations behind stable ACC auth/billing interfaces because availability and eligibility can change.

## 4. Target architecture

```
                         USER
                          |
              +-----------+-----------+
              |           |           |
          ChatGPT       ACC Web    ACC Desktop
          / Codex      zero-install   optional
              |           |           |
          ACC Plugin      |       local execution node
              +-----------+-----------+
                          |
                     ACC PLATFORM
                          |
          +---------------+----------------+
          |               |                |
      ACC Project     ACC Decision      ACC Knowledge
        State            Engine          / History
          |               |                |
          +---------------+----------------+
                          |
                   AGENT DISPATCHER
                          |
        +-----------------+------------------+
        |                 |                  |
   OpenAI path       External agents     Tool/engine lanes
        |                 |                  |
 Codex Cloud /       Claude / Gemini /   Blender / Unity /
 Codex Harness /     DeepSeek / Grok /   Unreal / Android /
 Agents API /       local models / etc.   other MCP/tools
 ChatGPT Work
```

Artifacts and connected apps sit beside the dispatcher through `WorkspaceProvider` and `AppConnector` (section 6.8). They hold user-facing content; they do not hold ACC task state.

## 5. What ACC owns

ACC's defensible product value is **not** another foundation model or generic coding agent.

ACC owns the **project/work/workforce layer**. Software development is one major workload, not the boundary of the product:

- project/workspace identity and current state;
- workstreams that may be software, business, research, communication, planning, data, creative, administrative, or mixed;
- task graphs, dependencies, priorities, and acceptance requirements;
- agent capability registry;
- worker availability and resource leases;
- routing evidence and historical performance;
- branch/worktree/workspace ownership rules;
- delegated instruction records;
- execution history and structured progress;
- test/build/review evidence where applicable;
- document, communication, research, meeting, analysis, and business-work artifacts/evidence where applicable;
- independent-review policy;
- merge/publication gates;
- local/cloud execution selection;
- errors, retries, switch-outs, and recovery handoffs;
- Second Brain/project knowledge and lessons;
- usage/cost accounting;
- user attention and approval policy;
- cross-session continuity;
- web/plugin/desktop synchronization.

Agents, models, harnesses, connected apps, document/workspace systems, and local tools are workers/capabilities beneath this layer.

## 6. OpenAI platform mapping

### 6.1 ACC Plugin

The ACC plugin becomes the native OpenAI-facing control surface.

It should expose ACC capabilities through permissions/tools rather than reducing ACC to a small chat command set. The target tool surface includes projects, tasks, agents, runs, branches, reviews, errors, builds, knowledge, decisions, usage, local nodes, and engine integrations.

The plugin should support normal conversational use:

- "Continue the mobile build."
- "What is Codex working on?"
- "Show blocked tasks."
- "Have Codex fix the failed test and send it through review."
- "Open Hearth and Havoc."
- "What changed while I was away?"

Compact plugin UI should show status and actions; the full Command Center remains ACC Web/Desktop.

### 6.2 Codex Cloud

Codex Cloud becomes ACC's preferred OpenAI cloud coding worker path.

ACC should use Codex cloud execution for work that does not require a user's local machine. ACC tracks project/task state above Codex and consumes Codex state/events rather than duplicating Codex's own low-level agent loop.

A user's computer should not need to stay awake for cloud-only tasks.

**Integration route (unverified dependency).** As of 2026-09-30, OpenAI documents starting Codex Cloud tasks from ChatGPT/Codex, the Codex CLI, and the GitHub, GitLab, Linear and Slack integrations. It does not document a general API through which a third-party service such as ACC can create, monitor and cancel Codex Cloud tasks. The Codex SDK and App Server documented today drive a Codex process that ACC (or a host ACC controls) runs. Until OpenAI documents a Codex Cloud task API, ACC's cloud coding worker must use one of those documented routes, for example an ACC-hosted Codex App Server, the Agents API hosted sandbox, or a supported integration, and must not assume direct control of Codex Cloud tasks.

### 6.3 Open-source Codex harness / App Server

For Codex execution, prefer OpenAI's own open-source Codex harness and supported App Server/event interfaces instead of inserting a generic third-party harness between ACC and Codex.

ACC should adapt Codex's native:

- sessions/threads;
- tools;
- sandboxes;
- approvals;
- MCP/skills;
- streamed events;
- diffs;
- execution state;
- cloud/local transitions where supported.

ACC's Agent View and Play-by-Play should consume real Codex events where available.

### 6.4 Decisions API

Create a vendor-independent `DecisionProvider` interface in ACC.

**OpenAI Decisions is the preferred primary provider when generally available and suitable.** It takes over from TypeSafe Jev as the planned semantic provider for bounded decisions such as:

- task classification;
- agent/model routing;
- capability tier selection;
- retry/switch/escalate decisions;
- review-required classification;
- issue severity/category;
- attention routing;
- safe parallelization classification.

ACC supplies the evidence: project policy, task type, tools, cost, prior successes/failures, review history, workload, and availability.

The decision service does **not** establish factual states. Tests, branch state, credentials, build results, permissions, and merge gates remain deterministic checks from real systems.

Until OpenAI Decisions is sufficiently available and proven, retain interchangeable fallbacks:

```
DecisionProvider
  - OpenAI Decisions
  - TypeSafe Jev
  - deterministic ACC rules
```

TypeSafe is therefore a benchmark/fallback provider rather than the planned primary. The current code (`acc/routing.py`, `docs/AUTONOMOUS-ROUTING.md`) already treats Jev as optional semantic evidence over deterministic eligibility and scoring, so `DecisionProvider` generalizes that existing seam rather than removing a hard dependency.

### 6.5 Dots

Dots are **optional supervisory agents**, not a required ACC control layer.

ACC should be able to monitor and control Codex through its direct OpenAI integrations. Do not insert a Dot merely to watch Codex.

No developer API for dots is documented as of 2026-09-30; a dot reaches external services through plugins. A user's Dot may still be valuable for personal 24/7 supervision, cross-application follow-up, reminders, or deciding when to contact the user. A Dot can call ACC through the plugin where permissions allow, but ACC must operate normally without one.

### 6.6 Symphony

Treat OpenAI Symphony as a useful orchestration reference/specification, not as ACC's product identity.

Study and reuse applicable patterns such as continuous task pickup, dependency handling, isolated workspaces, restart/recovery behavior, and parallel independent tasks. ACC remains broader because it manages multiple providers, reviews, project knowledge, local execution, creative engines, web/desktop/plugin surfaces, usage, and policy.

### 6.7 ChatGPT identity and plan-backed usage

Where available and eligible:

- support Sign in with ChatGPT as a low-friction identity path;
- support OpenAI-authorized plan-backed app usage where permitted;
- do not require every customer to understand or manually configure API keys for the baseline experience.

These are integration conveniences, not the canonical ACC identity or billing database. As of 2026-09-30, Sign in with ChatGPT and plan-backed usage are launched only with a limited set of partners, so the baseline must also work with ACC's own sign-in and an OpenAI API key.


### 6.8 ChatGPT Space and ChatGPT Work

ChatGPT Space is a strong optional native artifact/workspace layer for ACC's OpenAI-only path.

Use it conceptually for work products such as:

- project briefs and plans;
- meeting agendas, notes, decisions, and follow-ups;
- research summaries;
- business plans and operating documents;
- handoffs and status pages;
- shared team pages and nested supporting pages;
- uploaded/reference files and collaborative working material.

Space can reduce the need to create a Google Doc merely to obtain an editable collaborative document inside ChatGPT. However, ACC must **not** assume a public developer/plugin API for Space until OpenAI documents one. Until then, treat Space as a native user/agent surface that ACC can deep-link to or use through supported ChatGPT capabilities, not as ACC's canonical database.

ChatGPT Work is also part of the OpenAI-complete baseline for non-coding work. Where available, ACC should be able to delegate work that uses connected apps/files and produces documents, spreadsheets, presentations, reports, Sites, research, or other finished artifacts rather than forcing every task through Codex.

**How ACC reaches OpenAI non-coding workers.** As of 2026-09-30 there is no documented API for a third-party service to hand a task to ChatGPT Work and read back the result. The documented routes are:

- **User-in-the-loop:** the user works in ChatGPT/Work, and ChatGPT calls the ACC Plugin to read tasks and record progress, artifacts and results.
- **Agents API (public beta):** OpenAI's programmatic agent runtime, with sessions, an OpenAI-hosted sandbox, MCP connections and computer use. This is the programmatic OpenAI worker for non-coding tasks that ACC dispatches itself.
- **Workspace agent API triggers (research preview; Business, Enterprise, Edu and Teachers plans):** these queue a run but return no run ID or result, so ACC cannot use them as a tracked worker on their own.

ACC must not record a Work task as complete without a result it can see, whether through the plugin, an Agents API session, or an artifact in a `WorkspaceProvider`.

Add two provider abstractions alongside execution workers:

```
WorkspaceProvider
  - ChatGPT Space (when programmatic integration is officially exposed)
  - ACC internal workspace/artifact store
  - Google Drive
  - Box / Dropbox / SharePoint / future stores

AppConnector
  - Gmail / Outlook
  - Google Calendar
  - Drive / Docs / Sheets / Slides
  - business systems
  - future plugins/MCP services
```

ACC task/project state remains authoritative. Space, Drive, and other stores hold user-facing artifacts and collaborative content.

`AppConnector` covers authenticated SaaS/account integrations (mail, calendar, documents, business systems). `CapabilityConnector` covers executable tool/engine lanes such as Blender, Unity, Unreal and local MCP tools, which may need resource leases and an execution node. A product may implement multiple facets: for example Google Drive can be an `AppConnector`, `WorkspaceProvider`, and `KnowledgeProvider`; Blender is a `CapabilityConnector`. The shared boundary is codified in `acc/contracts.py` and `docs/ARCHITECTURE-SPINE.md`.

## 7. Harness strategy

ACC no longer chooses one universal harness.

### Codex Harness — production OpenAI worker runtime

Use the native Codex harness for Codex.

### DeepSeek Harness — optional provider-neutral/non-OpenAI worker runtime

Use behind an adapter when it is mature enough and useful for DeepSeek, compatible providers, open models, or local deployments. ACC already ships this adapter (`driver: 'dsh'`, `acc/deepseek_harness.py`); keep it behind the worker-adapter contract rather than rebuilding it.

Do not make ACC commercially dependent on an unstable developer-preview interface. Version/fence it behind ACC's worker adapter contract.

### HarnessX — Harness Lab, not production control plane

HarnessX stays out of ACC's primary execution path. It was already deferred on 2026-09-22 (integrated-graphics hardware) and has never been implemented in ACC. The Xiaomi HarnessX paper has no confirmed official public release (see `docs/IMPLEMENTATION-STATUS.md`), so "Harness Lab" is a role that HarnessX or another harness-optimization tool may fill once one is actually available.

Use it as an optional experimentation/optimization system:

- compare harness configurations;
- evaluate trajectories;
- test tool/memory/context strategies;
- optimize worker behavior;
- feed validated configurations into an ACC Harness Registry.

HarnessX may help ACC learn *how* a worker should run. The ACC Decision Engine chooses *which* eligible worker/path should run. The production harness actually runs it.

## 8. General work and multi-agent support

ACC is a **general work/project command center**, not a coding-only agent manager.

A task should be defined by required capabilities, inputs, permissions, artifacts, acceptance checks, and risk — not by whether it is "coding."

### Example work domains

- **Software/engineering:** code, tests, CI, debugging, release work.
- **Business operations:** plans, SOPs, vendor comparisons, process design, KPI reviews.
- **Communication:** read/triage email, draft/reply, prepare updates, summarize threads.
- **Meetings/calendar:** prepare agendas, gather context, schedule/reschedule, produce notes/follow-ups.
- **Research/analysis:** web research, document review, market/competitive research, data analysis.
- **Documents/data:** create/edit documents, spreadsheets, presentations, reports, trackers.
- **Creative/product:** website design, copy, branding assets, prototypes, image/video workflows.
- **3D/game development:** Blender, Unity, Unreal, asset pipelines, builds and testing.
- **Administrative/project management:** task planning, dependencies, approvals, recurring work, handoffs.

### Capability-based routing

ACC should route by capabilities such as `code.edit`, `email.read`, `email.send`, `calendar.schedule`, `docs.author`, `spreadsheet.analyze`, `research.web`, `browser.use`, `design.web`, `blender.edit`, `unity.build`, or `unreal.package`.

The same task graph/review/continuity system can therefore coordinate a mixed project. For example, a website launch might simultaneously include:

- a research agent comparing competitors;
- a business agent drafting positioning;
- a design agent preparing page structure and visuals;
- a coding agent building the site;
- an email/calendar agent coordinating stakeholder review;
- a document agent maintaining the launch plan.

The orchestrator/Decision Engine selects an eligible worker or app path based on required capabilities, evidence, cost, policy, and availability.

Capability routing extends the existing dotted capability vocabulary and role/tier metadata in `acc/routing.py`. These routing rules from earlier decisions still apply:

- An agent's **configured**, **healthy/connected** and **enabled by the user** states are separate. A disabled agent is never routed to, whatever its health or capability.
- Cheaper agents remain valid defaults where appropriate. A more expensive agent (OpenAI included) does not silently become the default because it might perform better.
- Routing changes that would alter standing policy are recommendations the user reviews, not silent global changes.
- Every queued task shows its real waiting reason (lease, disabled agent, provider limit, budget, local serial slot, engine reservation, prerequisite, recovery hold).

### Optional external agents

OpenAI-only operation is complete, but ACC should support optional installed/connected workers including:

- Claude / Claude Code;
- DeepSeek / DeepSeek Harness;
- Gemini;
- Grok;
- local Ollama/LM Studio/open models;
- future providers through a stable worker-adapter contract.

Optional agents add:

- second opinions and independent reviews;
- provider redundancy/outage tolerance;
- specialist capability;
- cost/performance choice;
- local/offline operation;
- cross-model verification;
- experimental comparison.

ACC must store agent performance independently of vendor branding: task class, capability, latency, cost, failures, successful reviews, retries, switch-outs, and tool availability.

## 9. Creative/development engine integrations

Blender, Unity, and Unreal are first-class ACC capability lanes, not side experiments.

They should be exposed through dedicated connectors/MCP tools and, when needed, executed through ACC Desktop/local execution nodes.

Examples:

### Blender
- scene/model generation and editing;
- import/export;
- scripted/headless operations where possible;
- rendering;
- asset validation.

### Unity
- project edits;
- editor automation;
- builds/tests;
- asset import;
- play-mode/editor checks where automatable.

### Unreal
- project/editor automation;
- content/import/build pipelines;
- commandlet/headless operations where supported;
- packaging/tests.

These resource-heavy or stateful tools require explicit execution leases so two agents cannot simultaneously corrupt the same project/scene/resource.

Cloud agents may prepare code/assets/instructions while a local execution node owns the actual exclusive engine operation.

## 10. Web and desktop roles

### ACC Web — universal default

ACC Web becomes the primary full customer-facing surface.

It must support:

- projects;
- agents;
- tasks;
- reviews;
- errors/attention;
- usage;
- decisions/routing visibility;
- project knowledge;
- plugin-launched deep links;
- cloud job monitoring/control;
- authenticated remote access.

It should be useful on Android/tablet without requiring ACC Desktop to be online when the work is cloud-based.

This makes the ACC Platform an internet-reachable, authenticated, multi-tenant service; the ACC Plugin's MCP server must also be reachable from OpenAI. That is new infrastructure: today's ACC server is loopback-only with a local session token. Consequences:

- A Desktop-only or offline user must still be able to run ACC locally with no public receiver, tunnel or relay.
- A Desktop node connects **outbound** to the platform. The platform and web pages never receive the local control token, and web pages never become the authority for local state.
- Provider credentials stay out of the platform where a local node or the provider's own sign-in can hold them.

### ACC Desktop — optional power mode

Desktop/Tauri adds:

- local repositories/files;
- local Git;
- local models;
- private LAN resources;
- Blender/Unity/Unreal;
- Android devices/APK workflows;
- local MCP servers;
- local execution that cannot run in cloud environments;
- optional offline operation;
- the Terminal View: real files, a real PTY able to run Neovim, and shared project/branch/task context;
- a persistent background service, so closing the window does not stop work, with state reconciliation after restart.

Desktop and Web must share a service/API/state model; they are not separate products.

**State authority (settled in architecture spine v1).** For cloud-connected accounts, the hosted ACC Platform is the system of record for shared account/project/work state, while Desktop/local nodes keep durable replicas for offline continuation. Actual local resource/process state remains node-authoritative and never becomes platform truth. A credential stays with the component that uses it (the node's store for local tools; the platform's secret store for platform-run connectors, e.g. a zero-install user's cloud mail grant) and is never replicated between them. Leases are granted online only, and account state is never mutated offline. Offline mutations use idempotent operation IDs plus optimistic entity revisions; conflicts surface for reconciliation rather than silently using last-writer-wins. Permanently local-only deployments keep the local store authoritative. See `acc/state_authority.py` and `docs/ARCHITECTURE-SPINE.md`.

## 11. Orchestration and control rules

ACC keeps deterministic rules around high-consequence or factual actions.

Examples:

- one writer per branch/workspace/resource lease;
- tests must actually pass before a passing state exists;
- a completed run is not automatically an accepted task;
- review is tied to a fixed code/artifact snapshot;
- stale review cannot approve changed work;
- branch/merge/publication policy is checked by code;
- credentials/permissions come from real integrations;
- a semantic decision model can recommend routing but cannot fabricate availability;
- a branch/lease is released for normal transfer only after the outgoing agent's Markdown continuity handoff exists; stalls and crashes go through explicit recovery instead;
- for cloud workers (Codex Cloud included), "cancellation requested" is not "confirmed stopped". A replacement cannot write the same branch until the previous writer can no longer write, or it works in a separate recovery workspace;
- nothing is pushed, published or synced to GitHub or another external system only because work passed review. Publication follows the user's explicit instruction or a publication policy the user set (see section 14).

AI decisions are used where interpretation is useful; deterministic software enforces facts and policy.

## 12. User attention

The goal remains maximum useful autonomy.

ACC should automatically handle ordinary routing, retries, review requests, handoffs, and cloud/local scheduling within user policy.

Surface the user primarily when:

- a required credential/login is missing;
- a material product/design decision is not established;
- permissions require confirmation;
- no eligible worker/path exists;
- a configured budget/usage constraint blocks progress;
- production/destructive/high-consequence action requires approval;
- the user explicitly opted into a review gate.

## 13. Prior decisions superseded by this document

Where older ACC documents disagree, use this document.

Specifically superseded:

1. **Desktop-first product / web as companion** -> Web is now the universal zero-install full Command Center; desktop is optional local power mode.
2. **A bespoke ChatGPT Remote bridge as the core remote strategy** -> ACC Plugin is the preferred OpenAI conversational/control integration; bridge work (`acc/bridge.py`, `acc/orchestrators.py`) may remain as compatibility/local fallback.
3. **TypeSafe Jev as the planned semantic router** -> `DecisionProvider` abstraction, with OpenAI Decisions preferred when ready and TypeSafe as fallback/benchmark. (Jev was already optional in code.)
4. **HarnessX as a possible future production harness** -> HarnessX, if it becomes available, is an optional Harness Lab/optimization tool. (It was already deferred on 2026-09-22 and was never implemented.)
5. **One generic harness for every worker** -> Native Codex harness for Codex, DeepSeek/provider-neutral harness where useful, additional adapters as needed.
6. **ACC must build its own generic always-on AI supervisor** -> Use OpenAI platform capabilities where they fit; Dots are optional and not required for Codex monitoring.
7. **Local machine required for normal use** -> Cloud-first work can operate without ACC Desktop; local machine is needed only for local-only capabilities.

Older documents remain valuable historical context and implementation evidence. Do not delete them.

## 14. What is deliberately unchanged

The new OpenAI platform direction does **not** remove these ACC principles:

- one agent/writer per isolated branch/workspace at a time;
- short-lived task branches/workspaces;
- independent review before accepted integration under project policy;
- explicit handoffs and durable task history;
- dense Command Center UI with minimal empty space;
- user-visible Play-by-Play based on real events, not hidden reasoning;
- multi-project support;
- cost/usage visibility;
- offline/local fallback where practical;
- provider independence;
- Second Brain/project continuity;
- no silent use of a personal signed-in browser profile;
- local creative-engine integrations;
- the orchestrator is a selectable role over one durable conversation, not a fixed vendor identity, with a lease against conflicting orchestration turns;
- delegated instruction records: the exact brief each worker received is stored, readable and editable;
- serialized local review/integration for local work, and GitHub/external publication only on explicit user instruction or user-set policy (2026-09-22 decision);
- configured / healthy / enabled agent states, cost-aware defaults, and routing changes as recommendations;
- local models serial on constrained hardware, and exclusive reservations for heavy local resources.

## 15. Implementation order

### Phase 1 — architecture contracts
- Define shared ACC Platform API/state model for Plugin, Web, and Desktop.
- Generalize the core domain from coding tasks to capability-based work/tasks/projects.
- Define `WorkerProvider`, `DecisionProvider`, `ExecutionNode`, `CapabilityConnector`, `WorkspaceProvider`, `AppConnector`, and `KnowledgeProvider` interfaces.
- Keep current Python core where useful; avoid rewriting proven persistence/review logic merely for technology preference.
- Implement hosted/local synchronization against the settled authority contract in section 10 and `acc/state_authority.py`.
- Implement connectors against the settled multi-facet boundary in section 6.8 and `acc/contracts.py`.

### Phase 2 — OpenAI-complete baseline
- ACC Plugin.
- Auth/account connection.
- ACC Web as authenticated universal full UI.
- Codex Cloud worker integration for coding/engineering work, through a documented route (section 6.2).
- ChatGPT Work path (via the plugin) and Agents API worker for research, connected-app work, and artifact production (section 6.8).
- Space-aware artifact/workspace UX where supported, without assuming an undocumented API.
- Codex native harness/App Server event integration.
- OpenAI-first task execution and review path across coding and non-coding work.
- DecisionProvider with deterministic fallback; add OpenAI Decisions when available/approved.

### Phase 3 — local power node
- Tauri/desktop shell.
- Local repo/execution registration.
- local models/MCP/tools.
- Blender, Unity, Unreal resource lanes.
- Android/local-device workflows.

### Phase 4 — optional worker ecosystem
- Harden Claude, Gemini, DeepSeek, Grok, local/open-model adapters.
- DeepSeek Harness where useful and stable.
- cross-provider independent review/routing.

### Phase 5 — optimization
- HarnessX Harness Lab.
- provider/task performance analytics;
- learned routing evidence;
- cost/performance optimization;
- enterprise/workspace policy.

## 16. Acceptance test for the architecture

A future ACC release satisfies this direction when both scenarios work:

### Scenario A — OpenAI-only customer
A user with only the supported OpenAI/ChatGPT ecosystem can:

1. discover/connect ACC;
2. open ACC Web without a desktop install;
3. create/import a project or workstream;
4. send natural-language work through ChatGPT/ACC;
5. use Codex Cloud for coding when coding is required;
6. use ChatGPT Work/connected apps for non-coding tasks such as research, documents, email, calendar, planning, and analysis;
7. create and organize user-facing artifacts through supported OpenAI workspace/document surfaces such as Space/Work, or ACC's own artifact store where no programmatic OpenAI surface exists, while ACC retains authoritative project/task state;
8. see live/structured progress across mixed worker types;
9. run ACC review/approval/gates appropriate to the work;
10. continue from phone/tablet;
11. optionally install Desktop later for local-only capabilities.

No third-party model is required and no coding task is required for ACC to be useful.

Some OpenAI surfaces are plan-dependent (for example, Space editing needs Pro, Business or Enterprise; dots roll out to Pro and Business Premium first). Scenario A must state which OpenAI plan it was verified on, and ACC must degrade to its own web UI and artifact store on plans without those surfaces.

### Scenario B — extended customer
The same user can additionally connect Claude/DeepSeek/Gemini/Grok/local workers and Blender/Unity/Unreal. ACC can route suitable work to them without changing the project/task model, and can fall back to the OpenAI baseline if those integrations are unavailable.

That combination — **OpenAI-complete by default, multi-agent and multi-tool by choice** — is the new ACC platform direction.

## 17. OpenAI capability status (verified 2026-09-30)

Checked on 2026-09-30 against official OpenAI sources: search results restricted to openai.com, help.openai.com and developers.openai.com (the reviewing environment could not open those pages directly), plus the `openai/codex` and `openai/symphony` READMEs read in full on GitHub. Recheck each row against the live page before implementing against it.

| Capability | Status | What is documented for ACC's use |
|---|---|---|
| Plugins in ChatGPT and Codex, universal plugin directory (MCP / Apps SDK) | Available | Build as an MCP server plus UI; submit for directory listing. |
| Plugin extensions (sidebar, composer, file-viewer panels) | Available, rolling out by plan | "Coming soon" on web for Free and Go users. |
| Plugin monetization | External checkout generally available | In-ChatGPT checkout only with saved merchant payment methods; directory listings may not advertise pricing. Matches section 3. |
| Sign in with ChatGPT and plan-backed usage | Limited partners | Launched with a limited set of partners; others use a contact process. |
| Codex Cloud | Available on ChatGPT plans (limits vary; admin-controlled in managed workspaces) | Started from ChatGPT/Codex, CLI, GitHub, GitLab, Linear, Slack. **No documented third-party task API.** |
| Codex App Server and Codex SDK | Available, open source (`openai/codex`) | JSON-RPC over stdio/WebSocket; SDK drives local Codex threads. |
| Agents API | Public beta | Sessions, OpenAI-hosted sandbox, MCP, computer use. |
| Decisions API | Limited preview; broad release announced "in the coming days" | Classification/routing over finite answer sets. |
| Dots | Rolling out to Pro and Business Premium (excluding EEA, Switzerland, UK); Enterprise beta | **No developer API documented**; dots use plugins. |
| Symphony | Open-source spec and Elixir reference (Apache-2.0), "low-key engineering preview" | Reference only. |
| ChatGPT Work | Available on eligible paid plans | **No documented API for third-party delegation**; plugins work inside it. |
| Workspace agent API triggers | Research preview (Business, Enterprise, Edu, Teachers) | Queue-only: 202 with no run ID or result. |
| ChatGPT Space | Available; create/edit on Pro, Business, Enterprise (web and desktop; read-only on mobile) | **No developer or plugin API documented.** |
