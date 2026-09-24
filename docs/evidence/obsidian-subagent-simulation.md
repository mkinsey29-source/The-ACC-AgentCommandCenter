# Obsidian knowledge lifecycle: live sub-agent simulation

Date: 2026-09-23 UTC  
Branch: `feature/obsidian-knowledge-connector`  
Task: `task-subagent-001`  
Run: `run-subagent-001`  
Revision: `simulation-v1`  
Snapshot: `seed-2026-09-23`

## Purpose

Validate the reasoning-dependent knowledge workflow with actual session sub-agents, in addition to the deterministic unit tests. The task was deliberately non-coding: produce an operating workflow for recovering from failed Blender renders.

## Setup

The connector created a disposable project-scoped vault using the production `KnowledgeVaults` interface. The vault contained:

- a verified render-verification procedure;
- a supported render retry procedure;
- a disproven hypothesis that failed attempts should be deleted; and
- a verified correction explaining why failure evidence must be retained.

`KnowledgeVaults.checkout()` performed lexical retrieval and generated a checkout note containing three active sources and one historical warning. The checkout and all vault data remained outside the repository's tracked files.

## Live worker run

A session sub-agent received the bound identifiers, the generated checkout path, and the original task. It was required to read every linked note, complete the four checkout synthesis fields before working, create `BLENDER_RENDER_RECOVERY.md`, and return the structured knowledge result.

Observed file order:

1. Checkout synthesis updated at `2026-09-24 07:53:47 +0900`.
2. Task artifact created at `2026-09-24 07:54:25 +0900`.
3. Connector check-in created at `2026-09-24 07:56:16 +0900`.

The artifact retained failed-attempt history, rejected the disproven deletion advice, required one-variable retries, separated preview and final-artifact verification, and defined checkout/check-in evidence.

The worker's authentic structured result was passed to `KnowledgeVaults.capture_result()`. At the time
of this simulation, the connector generated one combined check-in. The later recursive-review change
supersedes that layout: factual results remain in check-in, while issues, solutions, loops, proposed
corrections, and unvalidated claims are routed to `Reviews/`. Regression evidence for the new layout
is recorded by the knowledge tests and subsequent review simulation.

## Independent review

A fresh session sub-agent received the original task and bound run identifiers but did not receive a desired verdict. It inspected the repository branch, task artifact, checkout, check-in, source notes, and worker result.

Verdict: **approved**

All seven review criteria passed:

1. The generated checkout included linked retrieval and substantive synthesis.
2. The artifact applied active knowledge and rejected disproven guidance.
3. The artifact met every requirement in the original task.
4. The generated check-in faithfully captured the structured result and linked to checkout.
5. The disproven note remained present with status, warning banner, evidence, and correction backlink.
6. The run stayed off `main`; the worker made no tracked repository changes.
7. Limitations were reported rather than hidden.

## Limitations found

- Check-in evidence is narrative; it does not automatically attach command output or content hashes.
- Lexical retrieval records the search mode but the checkout does not display scores or ranking rationale.
- The checkout-before-work rule is enforced by completion validation and the worker contract. This run's timestamps corroborate the order, but the connector is not an operating-system write gate.
- The disposable isolated project had no commit history; the artifact was reviewed directly from the filesystem.

## Deterministic verification

`python -m unittest tests.test_knowledge` completed with 6 tests passing after the live run.

## Conclusion

The simulation exercised the real connector lifecycle with live reasoning: retrieval, pre-work synthesis, task application, structured learning capture, automatic check-in creation, correction-aware behavior, and independent review. This is distinct from the repository's scripted worker fixtures, which continue to cover deterministic protocol enforcement.
