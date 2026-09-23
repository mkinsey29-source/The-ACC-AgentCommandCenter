# ACC agent knowledge protocol

This protocol applies to every ACC worker that can access a configured project vault: remote
orchestrators, managed workflow agents, direct provider adapters, local models, and external
workers using the ACC MCP bridge. The project vault is the only vault in scope for that task.

## Required lifecycle

### 1. Checkout before work

Before changing project files or acting in an external tool, the worker must:

1. Call `acc_knowledge_checkout`, unless ACC already supplied `packet.knowledge`.
2. Read the returned relevant notes, including any disproven, obsolete, or superseded warnings.
3. Open the checkout note at `absolute_path`.
4. Replace the `ACC:CHECKOUT-SYNTHESIS` placeholders with:
   - what was learned;
   - how it applies to the assigned task;
   - conflicts, missing evidence, or uncertainty;
   - prior mistakes or failed approaches to avoid.
5. Only then begin the assigned work.

If a remote worker cannot access the checkout note's local `absolute_path`, it must synthesize the
retrieved context before working and return `knowledge.checkout_synthesis` with nonempty `learned`,
`application`, `conflicts`, and `mistakes_to_avoid` strings. ACC writes that synthesis into the
checkout note before accepting the check-in.

The checkout note is retained even when the task is canceled, paused, reassigned, or never reaches
execution. It records how existing knowledge was interpreted for a new problem.

### 2. Record knowledge while working

Do not create a note for every command. Record information when at least one of these occurs:

- a decision changes the direction, architecture, workflow, or expected output;
- an error, failed attempt, loop, or tool limitation changes the approach;
- a workaround or procedure succeeds and is likely to be useful again;
- a prior note is confirmed, contradicted, superseded, or shown to be obsolete;
- a handoff, summary, reusable prompt, or operating constraint is produced;
- a worker learns a non-obvious fact about a project, tool, provider, person, or process;
- work stops before completion and another worker needs enough context to continue safely.

Use `acc_knowledge_note` for a distinct durable concept. Use links to the checkout, task, evidence,
and related notes. Never delete a historical conclusion merely because it became wrong.

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

Every check-in contains:

- a plain-language outcome summary;
- new learnings;
- problems encountered;
- successful solutions and workarounds;
- loops and abandoned approaches;
- decisions made;
- corrections to prior knowledge;
- actual evidence and checks;
- a link to the checkout note.

Use empty arrays for categories with nothing to report. Never invent a learning to make the check-in
look complete. An unrun check must never be reported as passed.

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
    "learnings": [],
    "issues": [],
    "solutions": [],
    "loops": [],
    "decisions": [],
    "corrections": [],
    "evidence": []
  }
}
```

The arrays contain concise statements. Large logs and artifacts remain in their original locations
and are linked as evidence instead of copied into every note.

## Truth and authority

Embeddings and semantic judgments rank candidate notes; they never decide truth. Status changes are
deterministic ACC operations. `verified` and `disproven` require cited evidence or reviewer approval.
Project-vault selection is configuration, not a model decision.
