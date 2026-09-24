# Scoped recursive review simulation

Date: 2026-09-24 UTC
Branch: `feature/obsidian-knowledge-connector`

## Goal

Exercise the complete scoped lifecycle with live session agents:

1. write reusable knowledge into the task's domain folder;
2. write each operational exception into its own review thread;
3. have another agent assess how the exception was handled;
4. expose only the latest review leaf during the next scoped checkout;
5. require the next worker to acknowledge agreement or conflict before task work; and
6. write the next reusable outcome back into the same domain scope.

## Seed run

The production `KnowledgeVaults` interface created:

- a verified Blender preview procedure in `Blender/`;
- a supported CPU-fallback procedure in `Blender/`;
- a factual task check-in under `_ACC/Checkins/`; and
- a separate `solved-issue` thread under `Reviews/` for GPU initialization handling.

The factual check-in did not absorb the issue narrative. It linked both the scoped knowledge note
and the review thread.

## Independent review

A live reviewer inspected the check-in, scoped procedures, and review item. It returned `mixed`:
the one-variable CPU fallback and preview gate were reasonable, but the available evidence did not
establish every claimed step or the GPU root cause. ACC created this assessment as the next
`pending-review` note and linked it to the prior review.

## Next checkout

A checkout for a new task with `scopes: ["Blender"]` returned:

- the Blender knowledge notes;
- no unrelated project folder; and
- only the independent review, which was the latest pending leaf of its thread.

The next live worker read the scoped notes and full linked review history, completed its synthesis,
recorded agreement with the cautious mixed review, and then created `NEXT_RENDER_PLAN.md`. ACC added
the agreement and checkout backlink to the reviewed note without creating a redundant review file.

At check-in, ACC wrote a new supported procedure into `Blender/`. The accepted payload claimed no
new exception, so it created no new review thread at that time.

## Contract issue found and corrected

The worker's first response used natural-language field names and copied prior review issues into the
new run. The connector rejected that shape during the simulation relay. The worker resubmitted with
the exact schema and current-run-only exception arrays. The shared worker instructions and
`result_contract` now explicitly list the required field names and state that prior review history
must be acknowledged rather than copied into a new check-in.

A later independent audit correctly found that the rejected response and resubmission were themselves
a current-run failed loop. Therefore this historical run passed the scoped checkout and normal filing
criteria but failed complete exception capture. ACC now automatically creates an idempotent
`failed-loop` review whenever result/check-in validation rejects a worker payload, even when the
worker's corrected payload omits that event. Coordinator-level regression tests now cover malformed
results, nonzero model exits, actual model timeouts, and queued job cancellation; verify scoped review
visibility on the next checkout; distinguish stopped work from timed-out failed loops; keep identical
failures attached to their current checkout; and inject multi-note commit failures through both the
managed and public MCP check-in paths to confirm full rollback. Managed subprocess and integration-job
terminal paths use the same failure-recording method.

A fresh independent session agent reviewed the corrected implementation in multiple bounded rounds.
Its final verdict was pass: all seven lifecycle criteria, terminal failure routing, current-checkout
deduplication, timeout classification, and check-in atomicity were satisfied. The original historical
worker run remains recorded as a six-of-seven run rather than being rewritten as if it had succeeded.

## Deterministic coverage

The knowledge tests now cover:

- factual/review separation;
- review of a review;
- scoped retrieval;
- latest-leaf review selection;
- agreement acknowledgements and backlinks;
- conflict creation as the next review leaf;
- scoped durable knowledge creation; and
- one review thread per structured operational exception.

An independent code audit then identified integrity gaps. The implementation was hardened so malformed
results are fully validated before vault mutation, legacy exception arrays create one transparent
review thread per item, recursive reviews inherit target scopes, agreement text cannot inject managed
markers, concurrent backlink updates are serialized, and knowledge workers are blocked on `main` and
`master`.

Result: 13 knowledge tests and 41 adjacent integration/workflow/core tests passed.
