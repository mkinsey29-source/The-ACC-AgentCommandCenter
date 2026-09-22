# ACC command centre — design decisions

Status: **design stage. Nothing here is implemented.** This records what was
decided in a design Q&A about the ACC's new interface and the behaviour behind
it. It is written to be reviewed by a coding agent.

**If you are the reviewing agent:** check each decision against the current code
and say where it conflicts, where it is already satisfied, and where it is
underspecified enough that you could not build it without guessing. Section 13
lists the conflicts already known; add to it rather than assuming it is
complete. Do not implement anything from this document yet.

---

## 1. What the ACC is becoming

A single-operator command centre for running many AI coding agents across many
projects from one laptop. The operator talks to an orchestrator agent, which
classifies the work, writes a precise brief, delegates it to a sub-agent, and
sees it through review, merge and push without further human involvement. The
human is not in the normal path — only in the exceptions.

## 2. Platform and framing

| Decision | Value |
| --- | --- |
| Replaces | `acc/web` entirely. Not a second UI, not a re-skin. |
| Target display | One laptop screen, roughly 1440px wide |
| Density | Dense. Maximum readouts per screen, small type, mission-console aesthetic — hairlines, mono numerics, bracketed panel headers, no gradients |
| Fleet size | 8–9 agents configured today; design for 6–12 |
| Concurrency | Up to ~20 tasks in flight across projects and branches |

## 3. Shell layout

- **Centre console** with three peer tabs: **AGENT VIEW / TASK VIEW / TERMINAL
  VIEW**. Each tab owns the whole centre console.
- **Bottom rail, split in two**: the orchestrator input line on one side, the
  activity feed on the other.
- **Side rails** are contextual — they may hold different panels in agent view
  than in task view. *Their contents are not yet decided.*
- The centre reads as list plus detail, the detail taking most of the width. Its
  shape **does not change with task stage**: many tasks across many projects are
  live at once, so the view stays stable and scannable.
- **No welcome screen.** Opening the ACC shows where you left off and what is
  running now, with blockers surfaced. There is no "while you were away"
  summary; background workers mean something is always in flight.

## 4. Disclosure ladder

The terminal is the only top-level destination. Everything else opens from where
it lives, at the size it needs:

1. **Inline** — readable in place.
2. **Small popover** — a few fields, a short list.
3. **Large modal** — a diff, a review, a form with consequences.
4. **Full page** — the terminal, and anything else that earns it.

Every surface gets a tier assigned as it is designed.

## 5. Interaction and input

- **Voice is first-class.** The microphone is always present on the orchestrator
  line; talking is the preferred way to give instructions.
- The orchestrator line takes **plain conversation** — no command syntax. The
  orchestrator reads intent: a question, a correction, or a new task.
- The conversation **expands upward** from that line over the shell.
- The **command palette is a terminal feature only.** The rest of the shell is
  mouse-driven with fixed hotkeys for tabs and panels.
- The **terminal is a real PTY**: any command, full-screen TUI programs
  included. Neovim must run in it. This requires real terminal emulation, not a
  log view. It appears both as a live strip in the shell and as the full tab.
- The **master command panel** is collapsed by default and expands on click. It
  holds a toggle per agent and a toggle per setting, not one master switch.
- The **activity feed** may be a full play-by-play. It is ambient, never an
  interruption, and clicking an entry jumps to where it is happening.
- Both views share a **grouping switcher**: flat list by default, grouped by
  project on demand. Grouped by project, agent view shows which agents are in
  that project and what each is doing.

## 6. Attention and interruption

Because completion, review, merge and push are automatic, the human is not in
the normal path. The shell interrupts **only for things that stop progress and
cannot be cleared without a human**:

- something needs logging into
- a task you flagged is waiting on your review
- no agent is available to take the work
- a usage cap is hit
- the orchestrator asked you a question

Routine agent activity, ordinary errors, and "shall I push this?" never
interrupt. An error an agent cannot resolve causes reassignment, not a question.

## 7. Orchestrator

- A **conversational agent with sub-agents**. It reads intent, answers from its
  own knowledge or by asking a sub-agent, and delegates tasks itself.
- It **echoes what it understood and starts**; it does not wait for
  confirmation.
- The ACC shows **the exact instruction set it gave each sub-agent**. The
  operator brainstorms loosely; the orchestrator writes the precise brief; the
  operator can read what was actually delegated.
- That brief is **editable from the screen where it is displayed**, and both the
  orchestrator and the assigned agent see the edit.
- It bridges to a remote-capable assistant so it can be driven from a phone —
  ChatGPT's remote feature or Claude's equivalent. *Which one is not settled.*
- Inside the ACC, any configured agent can be assigned as the one you talk to.

## 8. Agents, routing and failover

- **Completion matters more than identity.** ACC reassigns automatically rather
  than blocking on a human, except where an agent is pinned to a task or the
  work class belongs to a specific agent.
- **Routing is by work type**, predetermined. Each task type — heavy coding,
  rendering, reviewing, and so on — has a preferred agent and a backup, with
  separate local assignments for offline work. The orchestrator classifies the
  task, then routes it. *The task-type list itself is deferred to the build
  stage.*
- The routing table gets **its own screen**, reachable from agent view.
- Failover normally walks **down** the cost ladder.
- Failover may escalate to a pricier agent **only if that agent reports its own
  usage**, and only within **5 percentage points of that agent's current
  allowance**. An agent at 25% of its weekly or monthly allowance may reach 30%
  on a failover task, then stops.
- **Every agent has a backup.**
- **Local models are for offline work and run tasks one at a time**, serially.
- One agent may hold several tasks at once, on different branches.

## 9. Budgets and usage

- Token caps **pause the task at the cap**, at the next step boundary, the same
  way any other hold works.
- The meter's primary unit is **percentage of an agent's own allowance**, not
  raw tokens, because the failover rule is written against percentages.

## 10. Projects, branches and concurrency

- **Multiple projects at once.** Every task is labelled with its project —
  colour coding is fine — and the default is one list showing task, agent and
  project together. A project switcher exists for filtering and grouping, not as
  the only way to see work.
- Projects live **one folder per project** on the desktop and are GitHub-backed.
  ACC is pointed at the folder.
- The concurrency rule is **no two agents on the same branch in the same
  repository at the same time**. Twenty branches can carry twenty agents on
  twenty tasks in one repository.
- **No worktree machinery is needed.** Agents already work the way remote
  sessions do: each takes its own copy of the repo and works a branch in it.
  ACC's job is to refuse two agents the same branch, not to manage checkouts.
- **There is no need to watch an agent's console.** What an agent pushes to its
  branch is visible locally; the branch is the reflection of its console.

## 11. Work pipeline and review gates

```
agent's own copy  →  [agent review]  →  your local clone  →  [agent review]  →  GitHub
```

- **Both gates are run by an agent, not by you.**
- The default path is **seamless**: completed, reviewed, merged and pushed
  without waiting on your approval.
- **You opt into reviewing**, either when you issue the task, or later from the
  task screen with a manual-review-after-completion toggle.
- **GitHub gets a full activity panel** — pull requests, checks and review
  comments inside the ACC, not only failures.

## 12. The three views

- **Agent view** — the whole centre console is agents, idle ones included, each
  clickable for its prior work. Flat list by default; groupable by project. An
  agent working several tasks shows that.
- **Task view** — the whole centre console is tasks across all projects, each
  labelled with its project, agent and state. Selecting one expands it into most
  of the width. A task at review stage opens its review there.
- **Terminal view** — the full Cursor-like page. The same session also shows as
  the live strip in the shell.

## 13. Conflicts with the current implementation

These are decisions the current code contradicts. Each needs a deliberate change.

| Decision | Current code | Where |
| --- | --- | --- |
| Many tasks run at once across branches and projects | One supervised runner per coordinator — `self.running_task` is a single slot, and one `Coordinator` serves one project folder | `acc/core.py` |
| Automatic reassignment on failure | `recovery_handoff` refuses to name a replacement until the writer has stopped, and requires an explicit `process_tree_inspected` acknowledgement from a human | `acc/controls.py`, `acc/server.py` |
| Agents may be switched off as a policy | `available` is a probe result — reachability, executable or key-file presence — with no stored policy flag | `acc/core.py` |
| Usage drives caps and failover | Drivers report usage (`grok_build`, `claude_code`); ACC records none of it, and no cap exists | `acc/core.py`, drivers |
| Routing by work type | Roles are configured per task; there is no task-type concept and no preferred/backup table | `acc/workflow.py` |
| Review is automatic | `review()` requires a human-supplied decision, and managed acceptance requires the bound reviewer result plus a coordinator decision | `acc/core.py` |
| The UI is one dense shell | `acc/web` is a stacked document-flow dashboard with dialogs | `acc/web/*` |

Not conflicts, but worth knowing: the offline rule already behaves the way the
design wants (a supervised cloud step reaches a safe boundary, the next is
blocked), and `stop()` already lets a claimed job finish rather than discarding
work it cannot interrupt.

## 14. Backlog — what the code must gain

1. **Usage accounting** — per-run usage records, per-task caps, pause at the cap.
2. **Agent allowances** — stored weekly or monthly allowance per paid agent and a
   rolling percentage used, so the 5-point failover band can be enforced.
3. **Task-type routing table** — type → preferred agent → backup, with separate
   offline assignments.
4. **Automatic failover** down the cost ladder, with the capped escalation rule.
5. **Per-agent assignable flag**, distinct from the probe result.
6. **Multi-project coordinator** and a cross-project task list.
7. **Branch-scoped writer leases** — one writer per branch per repository,
   replacing one runner per coordinator. No checkout management required.
8. **A real PTY**, plus a local completion model for the terminal's inline
   suggestions.
9. **Orchestrator bridge** to a remote-capable assistant, beyond the existing
   MCP conversation bridge (`acc/bridge.py`).
10. **Notification rules** limited to blocking events, including when the window
    is not focused.
11. **Event feed click-through** from a feed line to the task, agent or run it
    came from.
12. **Two-gate merge pipeline** — agent review into the local clone, agent review
    out to GitHub, with a per-task manual-review override.
13. **Delegated instruction records** — the orchestrator's brief to each
    sub-agent, stored, readable, and editable with both parties seeing the edit.
14. **Per-agent task concurrency** — local models serial; others bounded by what
    the machine and the provider can run.

## 15. Open questions

- What each side rail holds in agent view and in task view, and how large they
  are.
- The task-type list itself — deferred to the build stage.
- Which remote assistant becomes the orchestrator bridge.
- How an edited brief reconciles with a run already in flight: restart the
  agent, or apply from its next step.
- Whether the orchestrator itself is subject to the same usage caps as
  sub-agents.

## 16. Status of the visual drafts

The artboards in `docs/ui-drafts/` predate most of this. They still show a
1600px frame, a single project, one writer, raw-token meters, and no terminal
tab, bottom rail or orchestrator line. They will be redrawn once the rail
contents are settled.
