# Google Antigravity integration

ACC integrates with Antigravity first as an interactive MCP client. ACC remains the source of task ownership, numbered history, requirements, safe handoffs, and evidence. Antigravity remains the development surface that reads and reports through ACC.

## Host configuration

Run ACC first, then register the generated `~/.acc/.../hermes-mcp.json` command and arguments in Antigravity's supported MCP configuration. The outer configuration key may differ; preserve the generated executable, bridge path, local URL, and token-file argument exactly. Do not copy the token into the repository.

The connection is verified only when Antigravity can call `acc_state`, read the same numbered task shown in the dashboard, claim or create work, report evidence, and leave the worktree under ACC's one-writer policy.

## Interactive contract

1. Read `acc_state` and, when continuing a conversation, `acc_conversation_read`.
2. Create or revise the numbered task through ACC rather than keeping a private task list.
3. Before editing, confirm the task, revision, branch, project mode, active writer, and dependencies.
4. Report actual changed files and checks. A separate reviewer records its own findings.
5. If unavailable or quota-limited, stop writing. After termination is confirmed, use `acc_create_recovery_handoff` so the successor receives a numbered recovery task.
6. Use publication preview and publication tools only after bound review acceptance.

## Background control boundary

ACC does not launch Antigravity in the background until the installed version exposes a supported noninteractive CLI or API with start, streamed events, terminal completion, and cancellation. UI automation is not a worker adapter. Antigravity is not an offline fallback unless its actual model/runtime is local and explicitly configured that way.

## Laptop acceptance check

- Antigravity discovers ACC tools through its documented MCP setup.
- A small isolated task appears with the same Task number in both products.
- Its file/check report is saved in ACC and survives restart.
- Switching or recovery proves the old writer has stopped before another writer starts.
- Disconnecting Antigravity does not corrupt task ownership or falsely mark work accepted.
