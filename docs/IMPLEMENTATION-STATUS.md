# Implementation status — September 16, 2026

## Delivered in the initial PR

The local coordinator, persistent task/event database, browser dashboard, command runner, local Git observer, and stdio orchestrator bridge are implemented. The application has no third-party runtime dependency and no fake preloaded activity.

The dashboard starts with an empty task list. It can create real tasks and run real local commands, show output as it arrives, inspect local Git, stop supervised work, reassign idle tasks, and record instructions/evidence/review acceptance. Configured external worker commands receive JSON task packets.

## Evidence

- 15 automated tests passed on the hosted Linux environment before publication.
- Tests execute real subprocesses and cover output arriving before completion, failing commands, run deadlines, parent/child termination, exclusive workspace ownership, persistence/replay, interrupted-run recovery, requirement revisions, review/run mismatch, real Git edits/commits, unavailable providers, configured adapter packets, HTTP authentication/origin rejection, MCP-to-HTTP task creation, and SSE cursor replay.
- Python compilation and frontend JavaScript syntax checks passed.
- Browser click/layout verification was attempted, but Chromium is not installed and its download timed out. Do not claim browser interaction or visual acceptance from syntax checks.
- No paid provider calls were made. No live Claude/DeepSeek/Grok/local-model inference or user's native Remote MCP host was validated.
- Windows paths and termination code are authored, but native Windows behavior is unverified. POSIX process-group evidence does not establish Windows job containment.

## Deliberate first-build simplifications

The planned TypeScript interface is initially dependency-free JavaScript to make the first local build runnable without a bundler. The transport and task model remain separable for later TypeScript adoption. Local file/Git observations use one-second reconciliation rather than an OS watcher. Runner output uses direct events, independent of those observations.

Stage A is partially demonstrated with actual local command workers and a tested bridge contract. It is not fully accepted until the user's orchestrator host and one real coding-model adapter are connected and exercised.

## Next work, in order

1. Run the interface on the user's Windows or Linux machine and verify keyboard, responsive layout, task creation, live updates, reconnect, and controls.
2. Configure the actual orchestrator MCP connection and one real coding-worker host. Verify credentials, packet handling, tool events, cancellation, and a real bounded coding task.
3. Add immutable change snapshots, idempotent control commands, explicit worker acknowledgement, and verified Windows process containment. Then implement safe active reassignment to a second model.
4. Add task dependencies, native pipeline evidence, automated fixed-snapshot review, and task-linked local commit/remote PR actions.
5. Add a prepared local-model fallback, connectivity-aware ownership transfer, and tested online return.

Automatic active takeover, live requirement injection into running workers, automatic offline fallback, remote GitHub state, pushes, PR creation, and direct Unity/Blender control are not implemented by this PR. The UI labels these limits. Continue using the user's existing pipeline under its single coordinator.

## Repository workflow

`main` contains only the bootstrap README until the implementation PR is merged. Initial implementation source: reusable `temporary`. Preserve it after merge. Do not merge without user instruction. The full agreed direction is in `PROJECT-PLAN.md`.
