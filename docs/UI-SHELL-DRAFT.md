# ACC command shell — UI draft

First visual draft of the ACC shell in the mission-control aesthetic. The
artboard sources live in `docs/ui-drafts/` as Design Component files
(`.dc.html`); they render inside the design canvas, not as standalone pages.

## Settled elements

These four were fixed before the draft and each has a home in the layout:

| Element | Where it sits | Artboard |
| --- | --- | --- |
| Agent view / task view | Centre column, tabbed, fills the upper two-thirds | `Main.dc.html` |
| Agent token usage meter | Right rail, aggregate over per-agent meters | `Main.dc.html`, `Tokens.dc.html` |
| Offline terminal (Cursor-shaped) | Centre column, lower third, tabbed | `Main.dc.html`, `Terminal.dc.html` |
| Master command panel | A 40px bar at the top of the left rail that expands into a flyout; the full panel is a separate screen | `Main.dc.html`, `Command.dc.html` |

Everything else on the draft — system health, model infrastructure, alert log,
status strip — is a proposal, not a decision.

## Layout contract

1600 x 1000 reference frame.

- Top bar, 54px: workspace path, Git branch and dirty count, project mode,
  lease countdown, clock.
- Left rail, 300px: the collapsed master command bar, system health, model
  infrastructure, local changes.
- Centre, fluid: agent/task tabs above, terminal (330px) below.
- Right rail, 320px: token usage, alert log.
- Status strip, 34px: fleet and task counters, session tokens, billed cost.

## The master command panel

It is collapsed by default: a 40px bar in the left rail carrying the state it
is responsible for — project mode, how many agents are assignable, how many
systems are on — and a chevron. Clicking it opens a 430px flyout anchored
beside the rail, over the centre column, so the rail keeps its room for health,
models and local changes whether the panel is open or shut.

Inside the flyout, project mode sits at the top and the rest are sections that
open independently: FLEET, SYSTEMS, WORKFLOW ROLES, SERVICES. Each section
header carries its own summary, so a shut section still tells you where it
stands. A FULL PANEL button opens the whole thing as its own screen, which is
where the less frequent settings live — the provider catalog, the probe column,
eligible roles, contingency.


It is a panel of toggles, not one switch. Every agent has its own, and so does
every setting ACC already carries:

- **Fleet** — one row per configured agent (nine: seven model drivers, one
  job-backed adapter, one local command tool). Each row carries the agent's
  kind, its probe result, its eligible workflow roles, and an *assignable*
  toggle. Assignable is policy and the probe is fact: `ollama` and `lmstudio`
  are live reachability calls, the CLI drivers are executable or key-file
  checks, and an unreachable agent stays unassignable whatever the switch says.
  That is why the panel shows both instead of one merged state.
- **Project mode** — online/offline as two positions, since `acc/controls.py`
  treats it as one authoritative project-wide setting rather than a feature to
  switch off.
- **Conversation routing** — route-to-agents on/off, preferred agent, local
  fallback (which must declare `local: true`).
- **Managed workflow defaults** — implementer, reviewer, coordinator,
  correction rounds 1–10, with the constraints ACC enforces stated in the panel
  rather than hidden behind a validation error.
- **GitHub** — sync toggle, remote, source branch, protected base, refresh
  interval 15–300s.
- **Integration queue** — queue toggle, default data classification and
  workspace scope, and the provider catalog with its policy caps.
- **Capture and terminal** — voice capture, terminal writes, inline
  suggestions.
- **Contingency** — force stop and recovery handoff, kept apart from the
  ordinary toggles.

## Switching an agent off follows the code, not a new invention

`stop()` already decided this, and the draft shows what it decided. Toggling an
agent off stops it being offered new work; a run in flight finishes; background
coordination is disabled so the task pauses *before* the next step; a job
already claimed by a remote worker keeps running and a success it produces is
still accepted. Force stop is the separate escalation, and even then a remote
job is asked to stop itself, because ACC has no process there to kill. The
agent row reads FINISHING, the task reads PAUSING AFTER STEP, and the detail
pane says why.

Offline mode behaves the same way for the same reason: the supervised cloud
step in flight reaches a safe boundary, the next one is blocked, and queued
provider jobs move to `blocked_offline` instead of failing.

## Vocabulary

Statuses and drivers come from the real code, not invented labels: task states
from `acc/core.py` (queued, running, awaiting_review, accepted, paused, failed,
interrupted), drivers from `examples/all-drivers-agents.json` (claude-code,
ollama, lmstudio, dsh, grok-build, gemini), project mode and offline holds from
`acc/controls.py`.

## Palette and type

| Token | Value | Use |
| --- | --- | --- |
| Ground | `#060b14` | Page |
| Panel | `#0b1622` | Panel fill |
| Panel head | `#0f2436` / `#103049` | Header strips; the lighter one marks a primary panel |
| Line | `#17394e` / `#1f4d66` | Hairlines; the brighter one marks a primary panel |
| Cyan | `#5fd0e8` | Primary accent, selection, live values |
| Green | `#43e39a` | Running, armed, local (unbilled) |
| Amber | `#ffb84d` | Idle, held, approaching a cap |
| Red | `#ff6070` | Failed, alert, destructive |
| Violet | `#a98bff` | Billed cost, accepted work |
| Text | `#d6e7f4` / `#8fa8bd` | Body / secondary |

Chakra Petch for panel headers and command labels, IBM Plex Mono for all
numerics and terminal text, IBM Plex Sans for prose. No gradients; depth comes
from hairlines and header strips.

## Open questions

- Token accounting has no backend yet. `acc/snapshot()` carries no usage
  figures; only some drivers (`grok_build`, `claude_code`) report usage at all.
  The meter needs a per-run usage record and a per-task cap before it can show
  anything real.
- The terminal's inline suggestion implies a local completion model attached to
  the shell. Which local model, and whether suggestions are per-keystroke or
  only on demand, is undecided.
- Per-agent *assignable* is a new stored setting. Nothing in `core.py` holds
  it today: `available` is probed, and the only per-agent policy that exists is
  whether a role is configured. It needs a home in the settings table and a
  check at assignment time.
- The flyout shows six of the nine configured agents; the job-backed and tool
  adapters appear only in the full panel. Whether that split is the right one is
  open.
- The flyout closes only by its own bar or × today. Click-away, Esc and a
  pinned mode are undecided.
- Left and right rails are otherwise the least settled parts of the draft.
