# ACC design decisions

Running record from the design Q&A. Decisions here are settled unless revisited;
open questions are listed at the end, and anything requiring code that does not
exist yet is in the backlog. Nothing here has been implemented — this is the
design stage.

## Shell structure

- The new shell **replaces** `acc/web` entirely rather than living beside it.
- Target display is a **laptop screen, roughly 1440px wide**. One frame, dense.
- **Dense is the goal.** Maximum readouts per screen, small type, mission-console
  aesthetic: hairlines, mono numerics, bracketed panel headers, no gradients.
- Centre has three peer tabs: **AGENT VIEW / TASK VIEW / TERMINAL VIEW**.
- The terminal is also a **live strip** in the shell; the same session opens as
  the full tab when you need to work in it.
- **Bottom rail is split in two**: the orchestrator input line on one side, the
  activity feed on the other.
- Side rails are **contextual** — they can hold different panels in agent view
  than in task view. What goes in them is not yet decided.
- Each tab owns the **whole centre console**. In agent view the centre is
  entirely agents, idle ones included, each clickable for its prior work; in
  task view it is entirely tasks. Within a tab it reads as list plus detail,
  the detail taking most of the width.
- The centre layout **does not change shape by task stage**. Several tasks
  across several projects are live at once, so the view stays stable and
  scannable; a task at review stage opens its review inside the same pane.

## Disclosure ladder

Only the terminal is a top-level destination. Everything else opens from where
it lives — a rail panel, the task view, the agent view — at the size it needs:

1. **Inline** — readable in place, no interaction needed.
2. **Small popover** — a few fields or a short list.
3. **Large modal** — needs room: a diff, a review, a form with consequences.
4. **Full page** — its own screen: the terminal, and anything else that earns it.

Each surface gets assigned a tier as it is designed.

## Interaction

- The command palette is a **terminal feature only**. The rest of the shell is
  mouse-driven with fixed hotkeys for tabs and panels.
- The terminal is a **real PTY**: any command, full-screen TUI programs
  included. Neovim must run in it. That means real terminal emulation, not a
  log view.
- The orchestrator **echoes what it understood** and starts; it does not wait
  for confirmation.
- The ACC shows **the exact instruction set the orchestrator gave each
  sub-agent**. You brainstorm loosely; it writes the precise brief; you can read
  what was actually delegated.
- The **orchestrator line** in the bottom rail is plain conversation. The
  orchestrator is a conversational agent that reads intent, answers from its own
  knowledge or by asking a sub-agent, and delegates tasks itself. Typing does
  not need a command syntax.
- The conversation **expands upward** from that line over the shell.
- The **master command panel** is collapsed by default and expands on click; it
  holds a toggle per agent and per setting.
- The **activity feed** may be a full play-by-play. It is ambient, never an
  interruption, and clicking an entry jumps to where it is happening.
- **Voice is a first-class input on the orchestrator line.** The microphone is
  always there; talking is faster than typing and is the preferred way to give
  instructions.
- **No welcome screen.** Opening the ACC shows where you left off and whatever
  is running now, with anything blocking surfaced. No "while you were away"
  summary — background workers mean there is always something in flight.

## Attention

Because completion, review, merge and push are all automatic, the human is not
in the normal path at all. The shell interrupts only for things that **stop
progress and cannot be cleared without a human**: something needs logging into, a review is waiting, no agent
is available, a usage cap is hit, the orchestrator asked a question.

Routine agent activity, ordinary errors and "shall I push this?" never
interrupt. If an agent hits an error it cannot resolve, the next agent is
assigned instead of asking.

## Agents and routing

- Completion matters more than identity. ACC reassigns automatically rather
  than blocking on a human, **except** where an agent is pinned to a task or the
  work class belongs to a specific agent.
- Routing is **by work type**, predetermined: each task type (heavy coding,
  rendering, reviewing, and so on) has a preferred agent and a backup, with
  separate local assignments for offline work. The orchestrator classifies the
  task, then routes it.
- The routing table gets **its own screen**, reachable from agent view.
- Failover normally walks **down** the cost ladder — a cheaper agent first.
- Failover may escalate to a **pricier agent only if that agent reports its own
  usage**, and only within **5 percentage points of that agent's current
  allowance**. An agent at 25% of its weekly or monthly allowance may reach 30%
  on a failover task and then stops.
- Every agent has a backup.
- 6–12 agents configured is the realistic fleet size.

## Budgets

- Token caps **pause the task at the cap**, at the next step boundary, the same
  way any other hold works.
- The meter's primary unit is **percentage of an agent's own allowance**, not
  raw tokens, because that is what the failover rule is written against.

## Projects and concurrency

- ACC handles **multiple projects at once**. Every task is labelled with its
  project — colour coding is fine — and the default is one list showing task,
  agent and project together.
- A **project switcher** exists for changing how that list is filtered or
  grouped, not as the only way to see work.
- Clicking a task expands it to what is executing; the same work can be reached
  from agent view by looking at what an agent is doing.
- The concurrency rule is **no two agents on the same branch in the same
  repository at the same time**. Different branches and different repositories
  run in parallel.
- **No worktree machinery is needed.** Agents already work the way remote
  sessions do: each takes its own copy of the repo and works a branch in it.
  ACC's job is to refuse two agents the same branch, not to manage checkouts.
- Work reaches GitHub through two gates: an agent's copy is **merged into your
  local clone after review**, and the local clone is **pushed to GitHub after a
  separate review**. Both reviews are done **by an agent, not by you**.
- **The default path is seamless.** A task is completed, reviewed, merged and
  pushed without waiting on your approval. You review only when you explicitly
  say you want to review that task.
- **There is no need to watch an agent's console.** What an agent pushes to its
  branch is visible locally, and the branch is the reflection of its console.
  Review happens before anything reaches main.
- **You opt into reviewing** either when you issue the task, or later from the
  task screen with a manual-review-after-completion toggle.
- **GitHub gets a full activity panel** — pull requests, checks and review
  comments inside the ACC, not just failures.
- Both views share a **grouping switcher**: a flat list by default, grouped by
  project on demand. In agent view, grouping by project shows which agents are
  in that project and what each is doing.
- **One agent can hold several tasks at once**, on different branches.
- One agent per task, per branch, per repository. Twenty branches can carry
  twenty agents on twenty tasks in one repository; the rule is only that no two
  share a branch.
- Projects live one folder per project on the desktop and are GitHub-backed.
  ACC is pointed at the folder.

## Orchestrator

- Integrates with a remote-capable assistant so it can be driven from a phone —
  ChatGPT's remote feature, or Claude's equivalent. One of the two; which is not
  yet settled.
- Inside the ACC, any configured agent can be assigned as the one you talk to.

## Backlog — required before this UI can show real data

Each of these is absent from the current code.

1. **Usage accounting.** Drivers report usage (`grok_build`, `claude_code`);
   ACC discards it. Needs per-run usage records, per-task caps, and a pause at
   the cap.
2. **Agent allowances.** A stored weekly or monthly allowance per paid agent and
   a rolling percentage used, so the 5-point failover band can be enforced.
3. **Task-type routing table.** Type → preferred agent → backup, with separate
   offline assignments. Types themselves are deferred to the build stage.
4. **Automatic failover** down the cost ladder, with the capped escalation rule.
5. **Per-agent assignable flag.** `available` is probed, not chosen; there is no
   stored policy saying an agent may not be given work.
6. **Multi-project coordinator.** One `Coordinator` per project folder today,
   with no cross-project task list.
7. **Branch-scoped writer leases.** Today one running task per coordinator;
   the rule should be one writer per branch per repository. No checkout
   management needed — agents bring their own copy — only the refusal to put two
   agents on one branch.
13. **Delegated instruction records.** The orchestrator's brief to each
    sub-agent, stored and readable per task.
14. **Per-agent task concurrency**, so one agent holding several tasks does not
    exceed what the machine or the provider can run.
12. **Two-gate merge pipeline.** Review before an agent's copy merges into the
    local clone, and a separate review before the local clone pushes to GitHub.
8. **A real PTY**, plus a local completion model for the terminal's inline
   suggestions.
9. **Orchestrator bridge** to a remote-capable assistant, beyond the existing
   MCP conversation bridge.
10. **Notification rules** limited to blocking events, including when the window
    is not focused.
11. **Event feed click-through** from a feed line to the task, agent or run it
    came from.

## Open questions

- What each side rail holds in agent view and in task view, and how large they
  are.
- The task-type list itself — deferred to the build stage.
- Which remote assistant becomes the orchestrator bridge.

## Where the current drafts disagree with this

The artboards in `docs/ui-drafts/` predate most of the above. They still show a
1600px frame, a single project, one writer, raw-token meters, and no terminal
tab, bottom rail or orchestrator line. They will be redrawn once the rail
contents are settled.
