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

`--executable` defaults to `claude`; pass `--extra-args --model sonnet` (or similar) to
forward flags to the agent CLI. `--once` processes a single job and exits, for use as a
supervised worker command instead of a long-running poller.

## Verification status

Covered by `tests/test_agent3d.py` against a scripted stand-in for the agent CLI
(claim → run → finish, success, an agent-reported failure, and a crashed process). No
live Claude Code (or other coding-agent CLI) invocation has been exercised end-to-end
yet, so the actual quality of agent-produced 3D assets through this path is unverified.
