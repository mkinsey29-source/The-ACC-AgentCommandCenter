# ACC deployment and orchestration options — 2026-10-10

**Status:** a clarification of `docs/ACC-PLATFORM-DIRECTION-2026-09-29.md`. It supersedes nothing there,
except that item 2 of that document's section 13 is read as described under "Orchestrator chat" below.
Recorded from the owner's decisions on 2026-10-10.

## Decisions

1. **The desktop node is part of the first paid release.** The plugin is one way to reach it.
2. **Three separate paths reach the node.** Each is a setting, not a fixed choice.

   | Path | What it carries | How it is reached |
   |---|---|---|
   | Orchestrator chat | Instructions to the orchestrator and its replies | The Claude or ChatGPT remote features driving the app on the owner's computer, plus the local MCP bridge (`acc/bridge.py`, `acc/orchestrators.py`) |
   | Screen | Tasks, errors, approvals and the ability to give the orchestrator its next task | A private route to the node's own UI (for example Tailscale), or the hosted platform, where the node connects outbound |
   | Records | A readable record of all activity | Written to a storage provider the user connects (Google Drive, Box, Dropbox or another), through the M15 provider adapters |

3. **No lock-in.** ACC offers each option and chooses none for the user. Sign-in mode (trusted local, private
   with login, public with login) is separate from where the server listens (loopback, LAN, tailnet, custom, or
   a Docker host), and an unsafe combination, such as a non-loopback bind with no token, is refused.
4. **Authority stays in the node.** The node's local store holds task state, leases and quotas. A storage
   provider holds readable records only, and commands never arrive through files in it.
5. **Remote-driven agents are gated.** An agent driven remotely cannot raise its own permissions, and a
   destructive action on local files needs an approval that appears in Attention.

## Relation to the direction document

- The direction document made the ACC Plugin the preferred OpenAI integration and kept the bridge as a
  "compatibility/local fallback". That stays true for distribution and hosted use. For the desktop node the
  bridge, driven through the vendors' remote features, is a supported orchestrator channel rather than only a fallback.
- The hosted platform, web app and plugin MCP server are unchanged. What is open is whether the first paid
  release needs the hosted relay or can ship with the private route alone.

## Open questions

- Hosted relay in the first paid release, or private route only.
- Desktop shell framework (Tauri or Electron).
- How a Python runtime is shipped inside the desktop app.
- How the plugin reaches the node when it runs in a vendor's cloud. Vendor connector networking has not been checked.
- How paid access is checked when there is no hosted platform in the loop.

## Where the working notes are

The ACC Audit Roadmap and the ACC Mining Map (Claude Docs, private to the owner) carry the plan, the shared
checklist items SC-7 to SC-9 for this work, and the reviewed external repositories. No code from those
repositories was run or copied.
