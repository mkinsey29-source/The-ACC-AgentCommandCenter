# ACC — complete design handoff

**Status: design stage. None of this is built.** This is the single source of
truth for what the ACC is becoming. It is self-contained: nothing else needs to
be read to understand the plan.

**If you are a coding agent reviewing this:** check every decision in sections
3–7 against the code, and report where it conflicts, where it is already
satisfied, and where it is too vague to build without guessing. Section 8 lists
the conflicts already found — extend it, don't assume it is complete. Section 9
is the build checklist. Do not implement anything yet.

**If you are a new chat session picking this up:** sections 1–7 are the product,
section 8 is what stands in the way, section 9 is the work, section 11 is where
everything lives in the repo, and section 12 is what has already been drawn.

---

## 1. Summary

The ACC is a single-operator command centre for running many AI coding agents
across many projects from one laptop. The operator speaks — usually literally,
by voice — to an orchestrator agent. The orchestrator reads intent, classifies
the work, writes a precise brief, delegates it to sub-agents, and sees the work
through review, merge and push without further human involvement.

The human is deliberately **not in the normal path**. Completion, review, merge
and push are automatic. The operator is interrupted only when something stops
progress that no agent can clear.

At a glance:

| | |
| --- | --- |
| Program | A **desktop app** — Tauri shell, existing Python core bundled as a background process |
| Instances | **One ACC.** A single program running every project and every agent |
| Display | One laptop screen, ~1440px wide, **dense** |
| Centre | Three tabs: **AGENT VIEW / TASK VIEW / TERMINAL VIEW** |
| Bottom | Orchestrator input line on one side, play-by-play activity feed on the other |
| Concurrency | As many agents as the machine can carry, one per branch per repository |
| Money | Cloud agents metered as a percentage of their own allowance; caps pause tasks |
| Replaces | `acc/web` entirely |

---

## 2. How the operator works

1. Talks to the orchestrator — by voice or typing — on the bottom line.
2. The orchestrator echoes what it understood and **starts immediately**. It
   does not ask for confirmation.
3. It classifies the work by type, routes it to the right agent, and writes the
   brief. The operator can read the exact brief each sub-agent was given.
4. Agents work in their own copies, each on its own branch.
5. Finished branches become local pull requests, reviewed by an agent, merged
   into the local clone, reviewed again, pushed to GitHub.
6. The operator sees it happen in the feed, and is interrupted only by blockers.

---

## 3. The shell

### Layout

- **Centre console** with three peer tabs. Each tab owns the whole console.
- Within a tab: **list plus detail**, the detail taking most of the width. The
  shape **does not change with task stage** — many tasks across many projects
  are live, so the view stays stable and scannable.
- **Bottom rail, split in two**: orchestrator input line | activity feed.
- **Side rails** are contextual and may differ between agent view and task view.
- **No welcome screen.** Opening the ACC shows where you left off and what is
  running, with blockers surfaced. No "while you were away" summary — background
  workers mean something is always in flight.

### Disclosure ladder

The terminal is the only top-level destination. Everything else opens from where
it lives, at the size it needs:

1. **Inline** — readable in place
2. **Small popover** — a few fields, a short list
3. **Large modal** — a diff, a review, a form with consequences
4. **Full page** — the terminal, and anything else that earns it

Rail modules are **glanceable and expand when there is more to see**. GitHub
activity is the worked example: a small readout in the rail, a full panel when
clicked.

### The three views

- **Agent view** — the whole console is agents, idle ones included, each
  clickable for its prior work. An agent holding several tasks shows that.
- **Task view** — the whole console is tasks across all projects, each labelled
  with project, agent and state. Tasks belonging to one plan are **flat rows
  tagged with the plan**, not nested. Project colour coding is fine.
- **Terminal view** — a **Cursor-shaped page**: file columns, a project selector
  (each project already has its folder attached) or a file manager, the working
  area, and the shell. It is a **real editor** — open, write, save — with a
  **real PTY** underneath that must run neovim. All of it works offline. The
  same session also appears as a live strip in the shell.

Both views share a **grouping switcher**: flat list by default, grouped by
project on demand.

### Panels

- **Master command panel** — collapsed to a bar; expands on click into a flyout
  so it never occupies the rail. Holds a toggle per agent and a toggle per
  setting. Not one master switch.
- **Issues module** (settled) — carries **only what needs you**: blocked,
  waiting, or failed with nowhere to fail over to. Visually emphasised.
  An empty panel means nothing needs you.
- **Accepted rail candidates, none final** — token usage meters, local pull
  requests, machine headroom, GitHub activity.
- **Activity feed** — a full play-by-play is fine. It is ambient, never an
  interruption; clicking a line jumps to where it is happening.

### Input

- **Voice is first-class.** The microphone is always on the orchestrator line.
  Talking is faster than typing and is the preferred way to give instructions.
- The line takes **plain conversation** — no command syntax.
- The conversation **expands upward** from the line over the shell.
- The **command palette is terminal-only**. The rest of the shell is
  mouse-driven with fixed hotkeys for tabs and panels.

---

## 4. Agents, routing and failover

- **Completion matters more than identity.** ACC reassigns automatically rather
  than blocking on a human — except where the operator pinned an agent, or the
  work class belongs to one.
- **Routing is by work type**, predetermined: each type (heavy coding,
  rendering, reviewing, …) has a preferred agent and a backup, with separate
  local assignments for offline work. The orchestrator classifies, then routes.
  *The type list itself is deferred to the build stage.*
- The routing table gets **its own screen**, reachable from agent view.
- **Every agent has a backup.**
- Failover walks **down** the cost ladder.
- Failover may escalate to a pricier agent **only if that agent reports its own
  usage**, and only within **5 percentage points of that agent's current
  allowance** — an agent at 25% may reach 30%, then stops. An agent that does
  not report usage is not eligible for cost-escalating failover at all.
- **Local models are the offline path and run serially**, one task at a time.
- **One agent per task, per branch, per repository.** An agent may hold several
  tasks at once on different branches.
- Fleet size today: 8–9 agents. Design for 6–12.

### How a request fans out

A large request becomes **several tasks running at the same time**, not a chain.
One agent in Blender, another in Unity, another coding, another reviewing —
whatever the work needs, running concurrently. Sequential work that lives inside
one branch stays with the one agent holding that branch. The orchestrator knows
which is which. A small request stays one task with one agent.

---

## 5. Money and budgets

- Token caps **pause the task at the cap**, at the next step boundary, the same
  way any other hold works.
- The meter's unit is **percentage of an agent's own allowance**, because the
  failover rule is written against percentages.
- **The meter is for cloud agents only.** Local agents cost nothing.
- Its purpose is watching the **rate of depletion**, so the operator can switch
  an agent off, reassign its task, or find out why it is burning an allowance.
- **Reporting usage is part of the cloud driver contract.** A driver that can
  report tokens must, instead of discarding what the provider returns.
- **The orchestrator is never capped and never failed over.** Running it out of
  credit is the operator's problem, solved by swapping it. Its usage is shown so
  that choice is informed; nothing acts on it.

---

## 6. Projects, concurrency and the work pipeline

- **Multiple projects at once**, one folder per project on the desktop,
  GitHub-backed. ACC is pointed at the folder.
- Default view is one list showing task, agent and project together. A project
  switcher exists for filtering and grouping, not as the only way to see work.
- **No two agents on the same branch in the same repository.** Different
  branches and different repositories run in parallel.
- **No worktree machinery needed.** Agents work the way remote sessions already
  do: each takes its own copy and works a branch in it. ACC's job is to refuse
  two agents the same branch, not to manage checkouts.
- **No need to watch an agent's console** — the branch is the console.
- **Concurrency is whatever the machine can carry.** CPU, memory, VRAM and
  network decide; local models serial underneath. Because the limit is dynamic,
  the shell must always say *why* a run is waiting.
- **Project priority decides contention.** When there is room for everything,
  everything runs. *How priority is set is not yet decided.*

### The pipeline

```
agent's own copy → [agent review] → local clone → [agent review] → GitHub
```

- **Both gates are run by an agent, not the operator.**
- A finished branch becomes a **local pull request inside the ACC** — a
  reviewable item the reviewer agent approves and merges, leaving the diff on
  record.
- The default path is **seamless**, with no approval waits.
- **Review is opt-in**: stated when the task is issued, or switched on later
  with a manual-review-after-completion toggle on the task.
- **GitHub gets a full activity panel** — pull requests, checks, review
  comments — not only failures.

---

## 7. Attention, and the orchestrator

### What interrupts

Only things that **stop progress and cannot be cleared without a human**:

- something needs logging into
- a task flagged for review is waiting
- no agent is available to take the work
- a usage cap is hit
- the orchestrator asked a question

A blocker arrives three ways at once, none modal: **the orchestrator tells you**
(including on your phone through ChatGPT), the **issues module** lights up, and
it is **noted in the play-by-play**.

Routine agent activity, ordinary errors and "shall I push this?" never
interrupt. An error an agent cannot resolve causes reassignment, not a question.

### The orchestrator

- A **conversational agent with sub-agents**. Reads intent, answers from its own
  knowledge or by asking a sub-agent, delegates tasks itself.
- **Echoes what it understood and starts.** No confirmation step.
- The ACC shows **the exact brief given to each sub-agent**. The operator
  brainstorms loosely; the orchestrator writes the precise instruction set.
- That brief is **editable from the screen it appears on**, and both the
  orchestrator and the assigned agent see the edit.
- **An edit takes effect immediately** — the run restarts or adjusts. The edit
  exists because the direction is wrong, and spending tokens on a wrong
  direction is waste. This is the force-stop case, not the graceful one.
- It bridges to a remote-capable assistant so it can be driven from a phone —
  ChatGPT's remote feature or Claude's equivalent. *Which one is not settled.*
- Inside the ACC, any configured agent can be assigned as the one you talk to.

---

## 8. Architecture

| Decision | Value |
| --- | --- |
| Shell | **Tauri** (Rust) — small binary, low memory |
| Core | The existing **Python** — drivers, job queue, leases, git, workflows — bundled and run as a background process. Nothing rewritten in another language |
| Instances | **One ACC** for every project and agent |
| State | **One SQLite database** in the app's folder |
| Lifetime | The core **keeps running when the window closes**. The UI is a view onto a service that is always working |
| Transport | The core keeps its **localhost HTTP server and token**; the window is a client, and the existing MCP bridge already speaks it |
| Exposure | **Never listens beyond localhost.** The phone talks to ChatGPT; ChatGPT reaches the desktop; the orchestrator drives the ACC locally |
| Credentials | Provider keys in the **OS keychain**, handed to a driver only as it runs |
| Configuration | **Files for shape, database for state** — agent definitions and the routing table stay version-controllable files; allowances, usage and flags live in the database |
| Retention | **Prune noise, keep the record** — routine events age out; reports, reviews, evidence, usage and task history are permanent |
| Migration | **None needed.** The ACC has not been built, so schemas and numbering can be designed freely |

---

## 9. Definition of done for the first working version

Several agents complete their tasks on one project **concurrently, each in its
own branch**: the orchestrator plans, delegates to sub-agents, they finish, the
reviewers review the pull requests, the work merges to main, is reviewed and
synced — and nothing goes wrong.

---

## 10. Conflicts with the code as it stands

Each of these is a decision the current implementation contradicts.

| Decision | What the code does now | Where |
| --- | --- | --- |
| Many agents run at once | One supervised runner — `self.running_task` is a single slot | `acc/core.py` |
| One ACC for all projects | One `Coordinator` bound to one project folder, started per project with its own state directory and database | `acc/core.py`, `acc/__main__.py` |
| A desktop app | A Python `http.server` on localhost with a token, opened in a browser | `acc/server.py`, `acc/web/*` |
| A dense three-tab shell | A stacked document-flow dashboard with dialogs | `acc/web/*` |
| Automatic reassignment on failure | `recovery_handoff` refuses a replacement until the writer stopped, and requires an explicit `process_tree_inspected` acknowledgement from a human | `acc/controls.py`, `acc/server.py` |
| Agents switchable as policy | `available` is a probe result — reachability, executable or key-file presence — with no stored policy flag | `acc/core.py` |
| Usage drives caps and failover | Drivers report usage (`grok_build`, `claude_code`); ACC records none of it, and no cap exists | `acc/core.py`, drivers |
| Routing by work type | Roles are configured per task; no task-type concept, no preferred/backup table, no cost ladder | `acc/workflow.py` |
| Review is automatic | `review()` requires a human decision; managed acceptance requires the bound reviewer result plus a coordinator decision | `acc/core.py` |
| Local pull requests | No merge-to-local-clone concept; publication goes branch → GitHub PR | `acc/github.py` |
| Keys in the OS keychain | Every driver reads an `api_key_file` path from `agents.json`; `worker_prompt.subprocess_env()` strips `*KEY*`/`*TOKEN*`/`*SECRET*` from subprocess environments | all drivers, `acc/worker_prompt.py` |
| One instruction fans out to parallel tasks | Workflow steps are sequential roles on one task; there is no plan grouping several tasks | `acc/workflow.py` |
| A real PTY and editor | No terminal of any kind | — |
| Notifications | No notification mechanism | — |

**Already aligned, not conflicts:** offline mode behaves as designed — a
supervised cloud step reaches a safe boundary and the next is blocked, and
queued provider jobs move to `blocked_offline` rather than failing. `stop()`
already lets a claimed job finish rather than discarding work it cannot
interrupt, and already has the `force=True` escape hatch the brief-edit rule
needs. Tasks already carry permanent sequential numbers.

---

## 11. Build checklist

Ordered roughly by dependency. Nothing here is started.

### Foundation

- [ ] One-program architecture: a single ACC owning every project and agent,
      replacing one-coordinator-per-project
- [ ] Single SQLite store, with `project` as a first-class field throughout
- [ ] Project records: folder path, GitHub remote, colour, priority
- [ ] Background-service lifetime: the core survives the window closing
- [ ] Tauri desktop shell wrapping the bundled Python core
- [ ] Keychain-backed credentials, keeping the credential-filtered subprocess
      environment

### Concurrency

- [ ] Branch-scoped writer leases: one writer per branch per repository,
      replacing the single-runner slot
- [ ] Load-aware admission on CPU, memory, VRAM and network headroom
- [ ] Per-agent concurrency: local models serial, others bounded
- [ ] Project priority as the tiebreaker when the machine is full
- [ ] The waiting reason exposed to the UI for every queued run

### Routing, failover and budgets

- [ ] Work-type routing table: type → preferred agent → backup, with separate
      offline assignments (file-based)
- [ ] Per-agent assignable flag, distinct from the probe result
- [ ] Automatic failover down the cost ladder
- [ ] Capped cost escalation: usage-reporting agents only, +5 percentage points
      of current allowance
- [ ] Usage accounting: per-run records, per-task caps, pause at the cap
- [ ] Usage reporting added to every capable cloud driver as a contract
- [ ] Agent allowances and rolling percentage used
- [ ] Orchestrator exempt from caps and failover, usage still displayed

### Pipeline

- [ ] Local pull requests: a reviewable item per finished branch
- [ ] Agent-run review at both gates, replacing the human decision
- [ ] Merge into the local clone, then push to GitHub
- [ ] Per-task manual-review override, settable at issue time or later
- [ ] GitHub activity: pull requests, checks and review comments

### Orchestrator

- [ ] Bridge to a remote-capable assistant beyond the existing MCP bridge
- [ ] Intent reading, work-type classification and routing
- [ ] Parallel decomposition: one request becoming several concurrent tasks
- [ ] Delegated instruction records — the brief per sub-agent, stored and
      readable
- [ ] Brief editing, visible to both orchestrator and agent, taking effect
      immediately via force-stop
- [ ] Plan tagging on tasks

### Shell

- [ ] The dense three-tab shell at 1440px, replacing `acc/web`
- [ ] Bottom rail: orchestrator line and play-by-play feed
- [ ] Voice capture on the orchestrator line
- [ ] Master command panel: collapsed bar, flyout, per-agent and per-setting
      toggles
- [ ] Issues module carrying only what needs the operator
- [ ] Contextual side rails per view
- [ ] Grouping switcher, project labels and colour coding
- [ ] Event feed click-through to the task, agent or run
- [ ] Notification rules limited to blocking events
- [ ] Retention policy: prune routine events, keep the record

### Terminal page

- [ ] A real PTY that runs full-screen TUI programs including neovim
- [ ] File columns, project selector and file manager
- [ ] A real offline editor: open, edit, save
- [ ] Local completion model for inline suggestions
- [ ] Command palette scoped to this page
- [ ] Live strip in the shell sharing the session

---

## 12. Open questions

- [ ] What each side rail holds in agent view and task view, and how large
- [ ] The work-type list itself — deferred to the build stage
- [ ] How a project's priority is set: orchestrator, projects list, or both
- [ ] Which remote assistant becomes the orchestrator bridge
- [ ] What happens to in-flight runs when the laptop sleeps, as opposed to the
      window closing: local subprocesses suspend, cloud calls may time out, and
      resume behaviour on wake is undecided
- [ ] How rail modules signal trouble — colour, size, motion — deliberately
      deferred

---

## 13. The repo as it stands

~5,500 lines of Python with 25 test modules. Everything below exists and works;
none of it has been shaped for the decisions above.

| Module | Lines | What it is |
| --- | --- | --- |
| `acc/core.py` | 1014 | Coordinator, task store, run supervision, git state, stop and recovery |
| `acc/integrations.py` | 563 | Durable job queue: provider catalog, capabilities, data classification, workspace scope, budgets, fenced leases, retry and cancel; shared project memory |
| `acc/github.py` | 377 | GitHub activity polling, publish preview and publish |
| `acc/conversation.py` | 362 | Conversation store and routing settings |
| `acc/workflow.py` | 259 | Managed workflows: implementer, reviewer, coordinator, correction rounds, fallbacks |
| `acc/server.py` | 240 | Localhost HTTP API with token auth |
| `acc/setup.py` | 231 | First-run setup |
| `acc/bridge.py` | 224 | MCP stdio bridge to the running coordinator |
| `acc/agent3d.py` | 198 | Agent 3D Studio job connector |
| `acc/controls.py` | 157 | Project mode, scheduling, role switching, recovery handoff |
| `acc/archive.py` | 84 | Archive search and export |
| `acc/voice.py` | 79 | Recordings and local transcription |
| `acc/worker_prompt.py` | 82 | Shared prompt building, result extraction, credential-filtered subprocess env |
| `acc/snapshots.py` | 67 | Read-only snapshots for independent review |

**Drivers** (all sharing `worker_prompt.py`): `hermes`, `deepseek_harness`
(dsh), `deepastra`, `ollama`, `lmstudio`, `openai_compatible`, `gemini`,
`antigravity`, `claude_api`, `claude_code`, `grok_api`, `grok_build`,
`mimo_code`. Each is documented in `docs/DRIVERS.md` with a worked example in
`examples/all-drivers-agents.json`.

Task states: `queued`, `launching`, `running`, `stopping`, `processing_result`,
`publishing`, `interrupted`, `paused`, `failed`, `awaiting_review`, `accepted`.

---

## 14. The visual drafts

Four artboards exist in `docs/ui-drafts/` as Design Component files
(`.dc.html`), rendered on a private canvas at
<https://claude.ai/artifact/CU2MZJPyBrZUtC5JMBno71>. `docs/UI-SHELL-DRAFT.md`
holds the palette, type scale and layout contract.

**They predate most of this document.** They still show a 1600px frame, a single
project, one writer, raw-token meters, and no terminal tab, bottom rail,
orchestrator line or issues module. They are useful for the aesthetic only.

### What changed during this design session

1. **First drafts** — four artboards: the shell, an offline terminal detail, the
   master command panel and the token meter, built from the reference images'
   aesthetic and the repo's own vocabulary.
2. **Master command panel corrected** — it had been drawn as a single arm/halt
   switch. It is a panel of toggles: one per agent, one per setting. The
   shutdown semantics were rewritten to match `stop()`, which already lets an
   in-flight step finish and pauses before the next.
3. **Panel collapsed** — reduced to a bar that expands into a flyout so it never
   occupies the rail.
4. **Everything else** — superseded by this document: 1440px, three tabs, the
   Cursor-shaped terminal page, the bottom rail, multi-project, the pipeline and
   the routing model. The drafts will be redrawn once the rails are settled.

### Aesthetic, for whoever redraws it

Deep navy ground (`#060b14`), panel fill (`#0b1622`), cyan accent (`#5fd0e8`),
green for running and local, amber for held and idle, red for failure, violet
for billed cost. Chakra Petch for panel headers, IBM Plex Mono for every
numeric and terminal line, IBM Plex Sans for prose. Hairlines and header strips
carry the structure; no gradients, no shadows, no rounded cards.
