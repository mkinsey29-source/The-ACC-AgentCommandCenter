# ACC architecture spine v1

**Status:** Spine v1. Independently reviewed and corrected in PR #27 (2026-09-30); see `docs/ACC-MODULE-CHECKLIST.md` for module status.  
**Scope:** M01 shared contracts, M02 state authority/sync policy, M03 provider-neutral task model, M05 connector/provider boundaries.

## Purpose

This spine is deliberately small. It freezes the contracts that downstream modules may consume so
Plugin, Web, Desktop, Knowledge, worker adapters, connectors, and workspaces can be implemented in
parallel without redefining core concepts.

It preserves the proven Python coordinator, persistence, routing, review, integration-job, and
knowledge behavior. This is an extraction/generalization pass, not a rewrite.

## M01 — shared contracts

`acc/contracts.py` is the stable seam for:

- `WorkerProvider`
- `DecisionProvider`
- `ExecutionNode`
- `CapabilityConnector`
- `WorkspaceProvider`
- `AppConnector`
- `KnowledgeProvider`

It also owns shared capability/provider identifier validation. Provider-specific modules must not
invent a competing capability syntax.

These interfaces are intentionally small. An implementation may expose more methods internally, but
cross-module dependencies should target the contracts above.

## M02 — state authority and synchronization

`acc/state_authority.py` settles the authority rule.

### Cloud-connected account

The ACC Platform is the system of record for shared account/project/work state:

- account (nodes hold a read-only replica; account/entitlement changes are never queued offline)
- project/workstream
- task/run
- decision/review
- artifact metadata
- knowledge metadata
- usage
- event history
- writer/branch/resource leases (granted online only)

Desktop/local nodes keep durable replicas so already-authorized local work can continue offline.
An offline node may keep working under a lease it already holds until that lease expires, but it
cannot acquire, renew or transfer a platform lease offline; a queued lease grant could otherwise
produce two writers after reconnect.

### Node authority

The execution node remains authoritative for:

- actual local resource/process state;
- machine-local availability.

Local resource truth is reported as status, not overwritten by the platform, and the platform or
Web UI never becomes the authority for it.

### Credentials

A credential stays with the component that uses it (`authority: holder`): the node's OS credential
store for local tools and node-run connectors, and the platform's secret store for connectors the
platform itself runs (for example a zero-install, OpenAI-only user's cloud mail grant, where no node
exists). Credentials are never copied between platform and nodes by the sync protocol, and a
credential held by one side is never readable by the other.

### Offline reconciliation

Replicated mutations use:

- globally unique/idempotent `operation_id`;
- entity `base_revision` (a per-entity sync counter; not the task's requirements `revision`);
- optimistic revision checks for mutable entities;
- operation-id deduplication for append-only events.

A rejected revision conflict becomes an attention/reconciliation item; ACC never silently performs
last-writer-wins over changed project/task state.

A permanently local-only ACC deployment keeps its local store as system of record. Connecting that
deployment to an ACC account later is an explicit import/reconciliation event, not an invisible
authority flip.

The module is a contract and validation layer. It does **not** claim the current local prototype
already implements hosted synchronization.

## M03 — provider-neutral work/task contract

The existing task fields remain valid:

- `task_area`
- `required_capabilities`
- `risk`
- `priority`
- `depends_on`

The generalized contract adds:

- `workstream_id`
- `inputs`
- `permissions`
- `expected_artifacts`
- `acceptance_requirements`
- `resource_requirements`
- `data_classification`
- `workspace_scope`

All new fields have conservative defaults, so existing coding workflows remain valid.

Example non-coding task:

```json
{
  "title": "Prepare vendor renewal response",
  "instruction": "Review the thread and draft a response. Do not send it.",
  "task_area": "communications.email",
  "required_capabilities": ["email.read", "email.draft"],
  "permissions": ["email.read", "email.draft"],
  "expected_artifacts": ["artifact.email-draft"],
  "resource_requirements": ["account.gmail"],
  "inputs": {"thread_id": "example"},
  "acceptance_requirements": [
    "Draft addresses the requested renewal terms.",
    "No outbound message is sent without policy authorization."
  ],
  "data_classification": "confidential",
  "risk": "high"
}
```

`task_area` remains useful for historical performance grouping. `required_capabilities` is the
primary worker/connector eligibility input.

## M05 — connector/provider boundaries

A product may implement multiple facets. Facets describe what ACC is allowed to expect from the
connection.

### `AppConnector`

An authenticated SaaS/account integration: mail, calendar, document suites, business systems, etc.
It reads/writes remote service data and performs account actions.

### `CapabilityConnector`

An executable tool/engine lane: Blender, Unity, Unreal, local MCP tools, device tooling, etc. These
often require an `ExecutionNode` and an exclusive resource lease.

### `WorkspaceProvider`

A location for user-facing project artifacts/workspaces. It is never authoritative ACC task state.

### `KnowledgeProvider`

A storage/retrieval backend for the ACC Knowledge Engine. The knowledge lifecycle remains ACC-owned.

### Multi-facet examples

- Google Drive: `AppConnector + WorkspaceProvider + KnowledgeProvider`.
- Obsidian: `WorkspaceProvider + KnowledgeProvider` (through local files/vault semantics).
- Local folder: `WorkspaceProvider + KnowledgeProvider`, requiring a local execution node.
- Blender/Unity/Unreal: `CapabilityConnector`, requiring a local execution node and exclusive
  resource lease.

`acc/connectors.py` keeps **configured**, **healthy**, and **enabled** separate. Enabling a connector
does not make an unhealthy connector healthy, and an unconfigured connector cannot be enabled.

The suggested connector catalog is discovery metadata, not a claim that every connector is already
implemented. `implemented` is explicit. A connector is eligible for work only when it is implemented, enabled and
healthy; enabling a catalog entry that has no adapter never makes it eligible.

## Downstream module rule

A downstream agent gets:

1. owned files/paths;
2. the contracts it may consume;
3. the contracts it may change;
4. dependencies;
5. acceptance tests.

Changing a spine contract requires a spine-version change and independent review. Workspace or
connector implementations should not casually edit task/state/provider contracts.

## Acceptance for this branch

- shared dotted-capability validation has one source;
- direct task creation validates generalized fields before persistence;
- existing callers remain valid because defaults preserve the old contract;
- state authority and offline mutation envelopes are deterministic and testable;
- connector facet/state semantics are deterministic and testable;
- integration/routing modules consume the shared capability syntax;
- focused architecture-spine tests pass;
- the full existing suite passes on the reviewed revision (independent review, 2026-09-30).
