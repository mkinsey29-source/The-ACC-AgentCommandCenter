# ACC agent knowledge protocol

This protocol applies to every ACC worker that can access a configured project vault: remote
orchestrators, managed workflow agents, direct provider adapters, local models, and external
workers using the ACC MCP bridge. The project vault is the only vault in scope for that task.

## Required lifecycle

### 1. Checkout before work

Before changing project files or acting in an external tool, the worker must:

1. Call `acc_knowledge_checkout` with the task's assigned `scopes`, unless ACC already supplied
   `packet.knowledge` from `task.knowledge_scopes`.
2. Read the returned notes from those vault folders, including any disproven, obsolete, or
   superseded warnings.
3. Open the checkout note at `absolute_path`.
4. Replace the `ACC:CHECKOUT-SYNTHESIS` placeholders with:
   - what was learned;
   - how it applies to the assigned task;
   - conflicts, missing evidence, or uncertainty;
   - prior mistakes or failed approaches to avoid.
5. Read the latest pending note in every matching review thread.
6. Complete every review acknowledgement. Record agreement without creating a duplicate review. If
   the latest review conflicts with the evidence or planned approach, explain why and create/link a
   follow-up review.
7. Only then begin the assigned work.

If a remote worker cannot access the checkout note's local `absolute_path`, it must synthesize the
retrieved context before working and return `knowledge.checkout_synthesis` with nonempty `learned`,
`application`, `conflicts`, and `mistakes_to_avoid` strings. ACC writes that synthesis into the
checkout note before accepting the check-in.

The checkout note is retained even when the task is canceled, paused, reassigned, or never reaches
execution. It records how existing knowledge was interpreted for a new problem.

### 2. Separate facts from review material

Do not create a note for every command. Record information when at least one of these occurs:

- a decision changes the direction, architecture, workflow, or expected output;
- an error, failed attempt, loop, or tool limitation changes the approach;
- a workaround or procedure succeeds and is likely to be useful again;
- a prior note is confirmed, contradicted, superseded, or shown to be obsolete;
- a handoff, summary, reusable prompt, or operating constraint is produced;
- a worker learns a non-obvious fact about a project, tool, provider, person, or process;
- work stops before completion and another worker needs enough context to continue safely.

Use `acc_knowledge_note` for a distinct durable concept only when its status and evidence are stated.
Keep evidence-backed outcomes separate from material that needs judgment:

- check-ins contain the completed outcome, evidence-backed learnings, validated decisions, and checks;
- every correction, solved issue, unresolved issue, unfinished item, failed loop, or workaround gets
  its own `pending-review` thread in `Reviews/`;
- a review may target any note, including another review, by calling `acc_knowledge_review`;
- creating a review adds links in both directions but does not promote either note to truth.

Use links to the checkout, task, evidence, and related notes. Never delete a historical conclusion
merely because it became wrong.

### 3. Correct prior knowledge

When evidence contradicts an existing note:

1. Use `acc_knowledge_rebuttal` to create a correction note.
2. Cite the test, tool output, review, or observed result that supports the correction.
3. Let ACC link the correction in both directions and mark the original `disproven`, `obsolete`, or
   `superseded`.

Meanings are intentionally distinct:

- `hypothesis`: plausible and untested;
- `supported`: evidence exists but is not conclusive;
- `verified`: confirmed by tests, direct evidence, or review;
- `disproven`: evidence shows the conclusion is wrong;
- `superseded`: a newer conclusion replaces it;
- `obsolete`: it may once have been correct but no longer applies.

Normal retrieval does not recommend inactive conclusions. Diagnostic checkout still retrieves them
as historical warnings so workers do not repeat known mistakes.

### 4. Check in before completion

Before reporting completion or handing work to another worker, create a check-in with
`acc_knowledge_checkin`. Managed workers return the same information in `result.knowledge`, and ACC
creates the note automatically.

Every factual check-in contains:

- a plain-language outcome summary;
- evidence-backed learnings;
- validated decisions;
- actual evidence and checks;
- a link to the checkout note.

Reusable supported or verified outcomes go in `completed_knowledge`. ACC creates each item as a
durable note inside its assigned task scope. Each `review_items` entry becomes a separate `Reviews/`
thread containing the situation, how the worker handled it, the outcome, remaining uncertainty, and
evidence. The check-in links every created knowledge note and review thread.

Use empty arrays for categories with nothing to report. Never invent a learning to make the check-in
look complete. Exception arrays and `review_items` describe the current run only; acknowledge earlier
review history without copying its issues into the new check-in. An unrun check must never be
reported as passed.

If ACC rejects a result or check-in schema, that rejection is itself a `failed-loop` review item.
ACC records it automatically against the checkout, preserves the worker's files, and exposes it to
the next scoped checkout. A corrected result does not erase the rejected attempt or turn it into a
factual check-in.

The same routing applies when a subprocess or integration job exits nonzero, times out, is stopped,
or fails before returning a valid check-in. Stops become `unfinished-work`; failures and timeouts
become `failed-loop`. ACC records observed process/job state as evidence and explicitly marks worker
handling unknown when no trustworthy result exists. Check-in publication is transactional: checkout
acknowledgements, factual check-in, scoped knowledge, and review notes commit together or roll back.

## Managed worker result

When `packet.knowledge` exists, `result.json` must include:

```json
{
  "knowledge": {
    "checkout_synthesis": {
      "learned": "Required when the worker cannot edit the checkout note directly.",
      "application": "How the retrieved knowledge guided this task.",
      "conflicts": "Conflicts or uncertainty, or 'none'.",
      "mistakes_to_avoid": "Known failed approaches, or 'none'."
    },
    "review_acknowledgements": [
      {
        "path": "Reviews/latest-review.md",
        "disposition": "agree",
        "note": "Why this review is accepted for the current task."
      }
    ],
    "learnings": [],
    "issues": [],
    "solutions": [],
    "loops": [],
    "decisions": [],
    "corrections": [],
    "unvalidated": [],
    "evidence": [],
    "completed_knowledge": [
      {
        "title": "Reusable verified outcome",
        "body": "What future workers need to know.",
        "scope": "Blender",
        "type": "procedure",
        "status": "verified",
        "evidence": ["Exact check or artifact reference"]
      }
    ],
    "review_items": [
      {
        "title": "GPU initialization failure",
        "kind": "solved-issue",
        "situation": "What happened.",
        "handling": "What the worker tried and why.",
        "outcome": "What changed or remained unfinished.",
        "uncertainty": "What is still not proven, or 'none'.",
        "evidence": ["Log or artifact reference"]
      }
    ]
  }
}
```

The arrays contain concise statements. Large logs and artifacts remain in their original locations
and are linked as evidence instead of copied into every note.

## Recursive review

Use `acc_knowledge_review` to assess a pending review, correction, hypothesis, or prior review. State
one verdict: `supports`, `challenges`, `mixed`, or `needs-evidence`. Address how the prior worker
handled the situation, whether you would use the same approach, better alternatives, and missing
evidence. Include findings and evidence.
ACC creates the assessment as another `pending-review` note in `Reviews/` and adds a backlink to the
target. This recursion preserves the agent conversation and lets later agents challenge a review
without overwriting the earlier reasoning.

A review is not truth merely because a reviewer wrote it. Promote or reject the target with the
explicit lifecycle tools only after the required evidence or authority exists.

Checkout returns only the latest pending leaf in each relevant review branch. Earlier reviews remain
linked as the thread history. This arms the worker with current solutions and failure patterns while
avoiding repeated review of already-answered ancestors.

## Truth and authority

Embeddings and semantic judgments rank candidate notes; they never decide truth. Status changes are
deterministic ACC operations. Review creation never changes truth status. `verified` and `disproven`
require cited evidence or reviewer approval.
Project-vault selection is configuration, not a model decision.
