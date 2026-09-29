# ACC Platform Direction — OpenAI-First, Multi-Agent by Design

**Decision date:** 2026-09-29  
**Status:** Architectural direction. This document supersedes earlier ACC architecture decisions wherever they conflict, but it does **not** claim the announced OpenAI capabilities are already implemented in ACC.  
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
 Codex Harness       DeepSeek / Grok /   Unreal / Android /
                    local models / etc.   other MCP/tools
```

## 5. What ACC owns

ACC's defensible product value is **not** another foundation model or generic coding agent.

ACC owns the software-project/workforce layer:

- project identity and current state;
- task graphs, dependencies, priorities, and acceptance requirements;
- agent capability registry;
- worker availability and resource leases;
- routing evidence and historical performance;
- branch/worktree/workspace ownership rules;
- delegated instruction records;
- execution history and structured progress;
- test/build/review evidence;
- independent-review policy;
- merge/publication gates;
- local/cloud execution selection;
- errors, retries, switch-outs, and recovery handoffs;
- Second Brain/project knowledge and lessons;
- usage/cost accounting;
- user attention and approval policy;
- cross-session continuity;
- web/plugin/desktop synchronization.

Models and harnesses are workers beneath this layer.

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

**OpenAI Decisions is the preferred primary provider when generally available and suitable.** It replaces TypeSafe Jev as a required architectural dependency for bounded semantic decisions such as:

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

TypeSafe is therefore demoted from a core dependency to a benchmark/fallback provider.

### 6.5 Dots

Dots are **optional supervisory agents**, not a required ACC control layer.

ACC should be able to monitor and control Codex through its direct OpenAI integrations. Do not insert a Dot merely to watch Codex.

A user's Dot may still be valuable for personal 24/7 supervision, cross-application follow-up, reminders, or deciding when to contact the user. A Dot can call ACC through the plugin where permissions allow, but ACC must operate normally without one.

### 6.6 Symphony

Treat OpenAI Symphony as a useful orchestration reference/specification, not as ACC's product identity.

Study and reuse applicable patterns such as continuous task pickup, dependency handling, isolated workspaces, restart/recovery behavior, and parallel independent tasks. ACC remains broader because it manages multiple providers, reviews, project knowledge, local execution, creative engines, web/desktop/plugin surfaces, usage, and policy.

### 6.7 ChatGPT identity and plan-backed usage

Where available and eligible:

- support Sign in with ChatGPT as a low-friction identity path;
- support OpenAI-authorized plan-backed app usage where permitted;
- do not require every customer to understand or manually configure API keys for the baseline experience.

These are integration conveniences, not the canonical ACC identity or billing database.

## 7. Harness strategy

ACC no longer chooses one universal harness.

### Codex Harness — production OpenAI worker runtime

Use the native Codex harness for Codex.

### DeepSeek Harness — optional provider-neutral/non-OpenAI worker runtime

Use behind an adapter when it is mature enough and useful for DeepSeek, compatible providers, open models, or local deployments.

Do not make ACC commercially dependent on an unstable developer-preview interface. Version/fence it behind ACC's worker adapter contract.

### HarnessX — Harness Lab, not production control plane

HarnessX moves out of ACC's primary execution path.

Use it as an optional experimentation/optimization system:

- compare harness configurations;
- evaluate trajectories;
- test tool/memory/context strategies;
- optimize worker behavior;
- feed validated configurations into an ACC Harness Registry.

HarnessX may help ACC learn *how* a worker should run. The ACC Decision Engine chooses *which* eligible worker/path should run. The production harness actually runs it.

## 8. Multi-agent support

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
- optional offline operation.

Desktop and Web must share a service/API/state model; they are not separate products.

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
- a semantic decision model can recommend routing but cannot fabricate availability.

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
2. **A bespoke ChatGPT Remote bridge as the core remote strategy** -> ACC Plugin is the preferred OpenAI conversational/control integration; bridge work may remain as compatibility/local fallback.
3. **TypeSafe Jev as a central router** -> `DecisionProvider` abstraction, with OpenAI Decisions preferred when ready and TypeSafe as fallback/benchmark.
4. **HarnessX in the production path** -> HarnessX becomes optional Harness Lab/optimization infrastructure.
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
- local creative-engine integrations.

## 15. Implementation order

### Phase 1 — architecture contracts
- Define shared ACC Platform API/state model for Plugin, Web, and Desktop.
- Define `WorkerProvider`, `DecisionProvider`, `ExecutionNode`, and `CapabilityConnector` interfaces.
- Keep current Python core where useful; avoid rewriting proven persistence/review logic merely for technology preference.

### Phase 2 — OpenAI-complete baseline
- ACC Plugin.
- Auth/account connection.
- ACC Web as authenticated universal full UI.
- Codex Cloud worker integration.
- Codex native harness/App Server event integration.
- OpenAI-first task execution and review path.
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
3. create/import a project;
4. send natural-language work through ChatGPT/ACC;
5. have Codex work in the cloud;
6. see live/structured progress;
7. run ACC review/gates;
8. continue from phone/tablet;
9. optionally install Desktop later for local-only capabilities.

No third-party model is required.

### Scenario B — extended customer
The same user can additionally connect Claude/DeepSeek/Gemini/Grok/local workers and Blender/Unity/Unreal. ACC can route suitable work to them without changing the project/task model, and can fall back to the OpenAI baseline if those integrations are unavailable.

That combination — **OpenAI-complete by default, multi-agent and multi-tool by choice** — is the new ACC platform direction.
