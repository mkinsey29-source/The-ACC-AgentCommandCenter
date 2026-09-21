# Agent 3D Studio connector

## What it is

The `agent-3d-studio` provider fulfills `mesh.generate`, `texture.generate`, and
`rig.generate` capability jobs by asking a coding-agent CLI (Claude Code by default) to
build the asset itself, using this project's own skill chain (`3d-production-routing` →
`image-reference-workflow` / `character-sheet-pipeline` → `img2threejs` →
`materials-to-game` / `blender-game-animation`), instead of calling a remote
mesh-generation API such as Meshy or Tripo. It is `local: True` in the provider catalog:
no credential is required by ACC itself.

## Prerequisite

The managed project directory (the one passed to ACC's `--project`) needs the 3D skill
chain available under its own `.claude/skills/` — copy or symlink the relevant skills
from an ACC-Workspace checkout (`3d-production-routing`, `image-reference-workflow`,
`character-sheet-pipeline`, `img2threejs`, `materials-to-game`,
`blender-game-animation`) into the game project ACC manages. The worker spawns the
agent CLI with that project directory as its working directory, so it only sees skills
installed there.

## Job contract

Submit a job through the usual integration API:

```json
{"capability": "mesh.generate", "provider": "agent-3d-studio",
 "input": {"reference_image": "concept/tank_front.png", "subject": "tank",
           "target_engine": "unity", "output_dir": "Assets/Vehicles"}}
```

`input` is passed to the agent as-is — add whatever fields the chosen skill needs
(multiview references, target polycount, and so on).

On completion, the agent must write a JSON manifest to the path it was given, shaped as:

```json
{"status": "succeeded", "summary": "...",
 "artifacts": [{"kind": "model", "uri": "Assets/Vehicles/tank.glb", "metadata": {}}]}
```

The worker hashes each declared file and reports it to ACC as a job artifact; a
declared path that is missing, or that resolves outside the project, fails the job
rather than being reported as succeeded.

## Running the worker

```sh
python3 -m acc.agent3d --token-file /path/to/token --workspace /path/to/managed/project
```

`--executable` defaults to `claude`; pass `--extra-args "--model sonnet"` (a single
shell-quoted string) to forward flags to the agent CLI. `--once` processes a single job
and exits, for use as a supervised worker command instead of a long-running poller.

Prefer a scoped `--allowedTools` list over `--permission-mode bypassPermissions` for
real use: `img2threejs`'s gates run entirely through `python3 forge/*.py` and
`python3 scripts/*.py` subprocess calls, which `acceptEdits` does not cover, but a
narrow allowlist (e.g. `--extra-args "--allowedTools 'Bash(python3 forge/*.py *)' \
'Bash(python3 scripts/*.py *)' 'Bash(chmod +x *)' Write Edit"`) grants exactly those
without opening the agent up to arbitrary shell execution. `bypassPermissions` also
refuses to run at all under a root/sudo process, by Claude Code's own design.

## Verification status

Covered by `tests/test_agent3d.py` against a scripted stand-in for the agent CLI
(claim → run → finish, success, an agent-reported failure, and a crashed process). No
live Claude Code (or other coding-agent CLI) invocation has been exercised end-to-end
yet, so the actual quality of agent-produced 3D assets through this path is unverified.

## Reviewed delivery through a managed workflow

Submitting a bare job (above) gets the asset built and its artifacts hashed, but nothing
reviews it and nothing commits, pushes, or PRs it — the file just lands directly in the
tracked project tree the moment the job succeeds. To get the same independent-review and
git-delivery guarantees code tasks already have, configure `agent-3d-studio` as a
**job-backed implementer** on a managed workflow task instead of submitting the job bare.

Add a `kind: 'job'` agent to `agents.json`:

```json
{"agents": [
  {"id": "asset-builder", "kind": "job", "capability": "mesh.generate",
   "provider": "agent-3d-studio", "local": true}
]}
```

Then configure a task's workflow with it as the implementer, same as any other adapter:

```json
{"implementer": "asset-builder", "reviewer": "reviewer-agent", "coordinator": "coordinator-agent"}
```

A job-backed adapter can only serve as the implementer — `specification()` rejects it for
the reviewer or coordinator role, since only the implement stage has a shape (artifacts,
no verdict) that maps onto an async job. Once configured, ACC's own `start()` submits the
job (instead of spawning a subprocess) and dispatches; a running `agent3d.py` worker
elsewhere claims and finishes it exactly as it would a bare job. When it succeeds, ACC
freezes a git snapshot of the artifact-containing project tree — the same
`snapshots.freeze()` used for code tasks — and hands off to the normal reviewer →
coordinator → accept → publish pipeline. That gets you both gaps closed by reusing
existing, tested machinery instead of new one-off mechanisms:

- **Review**: an independent reviewer inspects the frozen snapshot, same as for code.
- **Delivery ("point A to point B")**: the artifact sits as an *uncommitted* change,
  protected by ACC's single-writer workspace lock, until the existing
  preview → commit → push → PR flow delivers it — never before.

## Stopping a job-backed step

`stop(task_id)` never discards in-flight work it cannot actually interrupt. ACC has no
process of its own to kill for a job claimed by a remote worker, so the **default behavior
mirrors offline mode**: the same way offline mode lets an already-running cloud step reach
a safe boundary instead of killing it, `stop()` on a claimed job-backed step just disables
the workflow (no further steps get scheduled) and lets the job run to completion. A success
still advances into `coordinate` normally — it is not thrown away just because a stop was
requested — a failure still holds as usual. A still-*queued* (not yet claimed) job is
cancelled outright either way, since there is no work in progress to lose.

`stop(task_id, force=True)` is the "absolutely unwanted, stop it now" escape hatch, and is
what a run's own timeout escalates to automatically (a stuck job is the emergency case, not
the graceful one). It cannot kill the remote worker's process directly either — instead it
sets `cancel_requested` on the job (`IntegrationHub.request_cancel`), which `agent3d.py`'s
own lease-renewal loop (`LeaseKeeper`) checks on its next check-in (every
`max(5, lease_seconds // 3)` seconds) and acts on by killing the *local* process it does
own, then reporting back through the normal `finish()` call like any other failure.

**Crash recovery**: a job-backed step interrupted by an ACC restart is marked `interrupted`
exactly like a subprocess-backed one (same blanket rule in `Coordinator.__init__`), but the
confirmation gate before clearing it (`process_tree_inspected`) is still worded for a local
PID — for a job-backed task the operator should actually confirm the external job/worker is
stopped or reconciled, and nothing in the code enforces that distinction yet.

Covered by `tests/test_job_backed_workflow.py`: successful completion through to an
accepted, snapshot-referenced review; a failed job holding the task; the graceful default
stop on a claimed job (including that a success it produces still gets accepted); a forced
stop flagging cancellation; a timeout escalating to a forced stop; stopping/cancelling a
still-queued job; the reviewer/coordinator role restriction; and restart recovery marking an
outstanding job-backed step interrupted. `tests/test_agent3d.py` additionally proves the
real mechanism end to end: a real worker process claims a job, keeps a real subprocess
running, and — when `request_cancel` is called mid-flight — actually kills that subprocess
and reports the cancellation back, across real process boundaries rather than a scripted
stand-in.
