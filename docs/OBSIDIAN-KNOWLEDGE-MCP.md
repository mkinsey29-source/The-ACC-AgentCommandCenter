# Obsidian knowledge MCP

ACC treats a project-scoped Obsidian vault as a living knowledge graph. The existing ACC MCP bridge
is the agent-facing interface. Obsidian remains a local Markdown application and does not need to be
open for ACC to search or update the vault.

## Configure a project vault

Add `knowledge` to the same JSON file used for ACC agents:

```json
{
  "knowledge": {
    "enabled": true,
    "vault": {
      "id": "acc-command-center",
      "path": "/absolute/path/to/ACC-Command-Center-Vault",
      "create": false
    },
    "embeddings": {
      "provider": "ollama",
      "host": "http://127.0.0.1:11434",
      "model": "embeddinggemma",
      "timeout_seconds": 30
    }
  }
}
```

The vault path must be absolute, local, and not a symlink. Set `create` to `true` only when ACC may
create the root folder. ACC creates its managed subfolders on startup. The embedding server is
restricted to loopback HTTP so vault content is not silently sent to a remote endpoint.

Use `"provider": "none"` when Ollama is not installed. Search continues with deterministic lexical
ranking. If a configured Ollama server becomes unavailable, a search reports `lexical_fallback`
instead of blocking the task. The embedding cache lives in the ACC state directory, not in the
Obsidian vault.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `acc_knowledge_state` | Report vault and embedding readiness. |
| `acc_knowledge_search` | Search active knowledge or explicitly include inactive history. |
| `acc_knowledge_checkout` | Retrieve context and create the required pre-work synthesis note. |
| `acc_knowledge_checkin` | Record outcomes and lessons at completion or handoff. |
| `acc_knowledge_note` | Create a durable linked knowledge note. |
| `acc_knowledge_review` | Review any note, including another review, without silently promoting it. |
| `acc_knowledge_transition` | Change lifecycle status without deleting history. |
| `acc_knowledge_rebuttal` | Create a correction and mark/link the original conclusion. |

These are knowledge operations, not low-level file operations. Folder selection, safe paths,
frontmatter, backlinks, warning banners, and atomic writes stay behind the module interface.

## Automatic workflow behavior

For each managed worker run, ACC:

1. Searches only the vault folders listed in the task's `knowledge_scopes` (or the whole vault when
   the task intentionally has no scope).
2. Creates a checkout note and places its context and absolute path in `packet.knowledge`.
3. Adds the latest pending leaf from every relevant review thread to the checkout.
4. Requires the worker to synthesize the starting knowledge and acknowledge every latest review
   before task work. Agreement adds no redundant review; conflict creates a linked follow-up.
5. Creates a factual linked check-in before advancing the workflow.
6. Writes supported or verified reusable outcomes into the assigned knowledge scope.
7. Routes each correction, solved or unresolved issue, unfinished item, failed loop, and workaround
   into its own `Reviews/` thread with `status: pending-review`.

Job-backed implementers receive the same checkout in the integration-job input and must return the
same `knowledge` result object.

Knowledge-enabled model and job workers are refused on `main` and `master`; create a feature branch
before starting them.

External orchestrators and Obsidian AI-agent plugins use the MCP tools directly. This keeps ChatGPT,
Claude, Grok, DeepSeek, local models, and future workers on the same lifecycle and note schema.

## Status and correction behavior

Inactive notes are preserved. A transition to `disproven`, `superseded`, or `obsolete` adds one
managed warning banner. Repeating the transition updates the same banner rather than duplicating it.
A rebuttal creates a verified correction note, links it to the original, and links the original back
to the correction.

## Recursive review directory

Every project vault contains `Reviews/`. Review-required material is kept there instead of being
mixed into factual check-ins. Each operational event records its situation, handling, outcome,
uncertainty, and evidence in a separate thread. Each review links to the note it assesses, and that
target receives a managed backlink. Since a review is also an ordinary targetable note, agents can
review, challenge, or correct other agents' reviews without overwriting the earlier reasoning.

New review notes always start as `pending-review` and are excluded from normal recommendations.
Creating a review records a conversation; it does not decide truth. Promotion to `supported` or
`verified`, or rejection as `disproven`, remains an explicit lifecycle operation backed by evidence
or authorized review.

At checkout, ACC presents the latest pending leaf of each matching thread. The worker records
agreement directly in the checkout and the reviewed note receives an acknowledgement backlink. A
disagreement creates the next linked review. This makes the
review directory a continuously updated conversation about how agents solved, failed, corrected, or
would improve prior work, while giving each new worker those solutions before it starts.

See [AGENT-KNOWLEDGE-PROTOCOL.md](AGENT-KNOWLEDGE-PROTOCOL.md) for the mandatory worker instructions.
