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
| Master command panel | Left rail, top, one arm/halt toggle over six subsystem switches | `Main.dc.html`, `Command.dc.html` |

Everything else on the draft — system health, model infrastructure, alert log,
status strip — is a proposal, not a decision.

## Layout contract

1600 x 1000 reference frame.

- Top bar, 54px: workspace path, Git branch and dirty count, project mode,
  lease countdown, clock.
- Left rail, 300px: master command, system health, model infrastructure.
- Centre, fluid: agent/task tabs above, terminal (330px) below.
- Right rail, 320px: token usage, alert log.
- Status strip, 34px: fleet and task counters, session tokens, billed cost.

The master toggle is wired through the whole draft: halting flips agent
statuses to HELD, empties the health meters, unloads the model list, and turns
the terminal's completion source off. That is the behaviour the panel is
claiming, so the draft demonstrates it rather than describing it.

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
- The master panel's six subsystems are a guess at the right seams. Terminal
  writes and cloud egress are the two that clearly matter; the rest are open.
- Left and right rails are the least settled parts of the draft.
