# ACC Obsidian Knowledge MCP handoff — 2026-09-23

## Delivered

- Added `acc/knowledge.py`, a project-scoped Obsidian knowledge module.
- Extended the existing ACC stdio MCP bridge and loopback HTTP server with seven high-level knowledge
  operations: state, search, checkout, check-in, note creation, status transition, and rebuttal.
- Added automatic checkout and check-in for configured model workers and managed workflow stages.
- Added job-backed worker context plus a remote synthesis return path when the vault filesystem is not
  directly available to the worker.
- Added optional local Ollama embeddings with cached vectors and visible lexical fallback.
- Added YAML lifecycle states, bidirectional correction links, and idempotent inactive-note banners.
- Added the mandatory worker protocol and a ready-to-copy configuration example.

## Important behavior

- One ACC Coordinator/project maps to one configured vault.
- Vault selection and truth-state transitions are deterministic code decisions.
- Semantic ranking does not verify facts or authorize status changes.
- Normal retrieval excludes checkout notes and inactive conclusions by default.
- Checkout explicitly includes inactive conclusions as historical warnings.
- Knowledge-enabled model results are rejected if required check-in data or checkout synthesis is
  missing. Local-command tasks are not treated as AI workers.
- Existing SQLite shared memory remains unchanged and available.

## Verification

Focused suite after implementation:

```sh
PYTHONPATH=tests python -m unittest test_knowledge test_integrations test_workflow test_acc
```

Result: **49 passed** after including the shared worker-prompt tests.

The final full suite completed **255 tests: 253 passed, 1 skipped, and 1 failed**. The remaining
detached DeepAstra grandchild heartbeat failure reproduces in isolation and was already documented in
the prior handoff as a pre-existing host/process timing issue. No changed knowledge file participates
in that test. An earlier full run also exposed the previously noted job-backed shutdown race and an
order-dependent readiness-switch expectation; both passed immediately in isolation and did not recur
in the final full run.

## Host validation still required

1. Install or open Obsidian and choose the ACC project's actual vault path.
2. Add the `knowledge` block from `examples/knowledge-vault.json` to the private ACC configuration.
3. If semantic search is desired, install/run Ollama and pull an embedding model.
4. Exercise a real MCP client or Obsidian AI Agent plugin against the seven knowledge tools.
5. Run one live worker task and confirm checkout synthesis appears before project edits and the linked
   check-in appears at completion.
6. Evaluate retrieval quality and tune chunking/ranking before using a very large vault.

## Known limitations

- No live Obsidian plugin or AI Agent client was available in the container.
- No live Ollama embedding request was made.
- Embeddings are currently note-level, with input truncated for bounded indexing; chunk-level indexing
  is a future scale improvement.
- The direct Markdown adapter is implemented. A future Obsidian-native adapter can sit behind the same
  module interface without changing agent tools.
- Checkout ordering is an agent contract plus completion-time validation, not an operating-system write
  gate that can prove the agent made no project edit first.
