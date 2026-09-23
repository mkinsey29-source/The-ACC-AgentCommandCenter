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
| `acc_knowledge_transition` | Change lifecycle status without deleting history. |
| `acc_knowledge_rebuttal` | Create a correction and mark/link the original conclusion. |

These are knowledge operations, not low-level file operations. Folder selection, safe paths,
frontmatter, backlinks, warning banners, and atomic writes stay behind the module interface.

## Automatic workflow behavior

For each managed worker run, ACC:

1. Searches the configured project vault.
2. Creates a checkout note and places its context and absolute path in `packet.knowledge`.
3. Instructs the worker to complete the synthesis section before work. A remote worker that cannot
   access the local note returns the synthesis in its structured result for ACC to write.
4. Requires structured learning fields in the final result.
5. Creates a linked check-in note before advancing the workflow.

Job-backed implementers receive the same checkout in the integration-job input and must return the
same `knowledge` result object.

External orchestrators and Obsidian AI-agent plugins use the MCP tools directly. This keeps ChatGPT,
Claude, Grok, DeepSeek, local models, and future workers on the same lifecycle and note schema.

## Status and correction behavior

Inactive notes are preserved. A transition to `disproven`, `superseded`, or `obsolete` adds one
managed warning banner. Repeating the transition updates the same banner rather than duplicating it.
A rebuttal creates a verified correction note, links it to the original, and links the original back
to the correction.

See [AGENT-KNOWLEDGE-PROTOCOL.md](AGENT-KNOWLEDGE-PROTOCOL.md) for the mandatory worker instructions.
