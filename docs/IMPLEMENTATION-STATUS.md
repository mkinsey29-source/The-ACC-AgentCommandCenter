# Implementation status — September 16, 2026

## Current follow-up: conversation and offline handoff

ACC 0.3 adds a conversation-first UI, persistent original messages, an IndexedDB browser outbox, external MCP claim/renew/complete/release tools, fenced turn ownership, and transactional reply/task/message acknowledgement. Configured online planners can fall back to local planners; new turns return to the preferred online adapter. Generated work uses the existing implementation/independent-review pipeline and retains source-message links. Technical task controls remain available as an advanced option.

Local audio capture saves recordings before a supervised transcription command. The optional faster-whisper adapter uses predownloaded model files. Interrupted internal runs are visible and recoverable. See [conversation workflow](CONVERSATION-WORKFLOW.md) for actual behavior and setup.

Verification is recorded in `docs/evidence/conversation-tests.json`. Scripted adapters exercise real process, persistence, MCP/HTTP, review, and reconnection paths. This does not claim live speech/model quality or browser/desktop host verification.

## Follow-up: live model responses through ACC

Three live session subagents supplied planning, implementation, and independent review, with the planner returning for coordination decisions. Their five actual responses passed through a transport-only relay into ACC's normal conversation/workflow engine. The task was accepted after independent review, and reconnect/replay checks preserved one completed task. See [live-agent evidence](LIVE-AGENT-HANDOFF.md). This tests live reasoning and handoffs, while provider-specific and disconnected inference remain unverified.

## Previous follow-up: Hermes coordination

Implemented `acc/hermes.py` with Hermes profile support, file-based one-shot prompts, streaming CLI output, terminal result validation, and MCP config generation. Added persistent managed workflow stages, independent review snapshots, bound results, corrections with limits, pause/resume, visible role changes, explicit offline mode, and configured local coordinator fallback. See [Hermes setup](HERMES-CONNECTOR.md).

Launch intent and result-processing state are now recovery-visible. Failed stops can be retried; shutdown retains locks if termination is unconfirmed. The dashboard retains received stream events across snapshot refreshes instead of dropping bursts outside the last 100 events.

Test evidence is recorded in `docs/evidence/hermes-connector-tests.json`. Actual subprocess/HTTP/stdio execution is tested with scripted model responses. Hermes installation, provider inference, model quality, browser interaction, and native Windows operation are not claimed verified.

## Historical: delivered in the initial PR

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

## Remaining work

1. Configure Hermes profiles on the user's machine and execute a small live-model handoff. Validate provider login, tool permissions, model context, output contract, and cancellation.
2. Verify dashboard controls and responsive layout in a browser; validate native Windows process containment and scheduled startup.
3. Validate the new conversation fallback/return with live providers. Safe active worker reassignment and automatic recovery from partially edited failed worker runs remain separate work.
4. Add task dependencies, native pipeline evidence, larger asset-aware review scope, and task-linked commit/remote PR actions.
5. Extend idempotency beyond the conversation/recording endpoints, add retention quotas and stronger execution isolation as needed.

Live requirement injection, automatic internet detection, remote GitHub state, pushes, PR creation, and direct Unity/Blender control are not implemented by ACC itself. Continue using the existing game pipeline under its coordinator.

## Repository workflow

`main` contains only the bootstrap README until the implementation PR is merged. Initial implementation source: reusable `temporary`. Preserve it after merge. Do not merge without user instruction. The full agreed direction is in `PROJECT-PLAN.md`.

## Follow-up: real sub-agent handoff exercise

A separate implementation agent and reviewer have now exercised a manually coordinated handoff, with actual ACC stdio/HTTP validation runs. The implementation's 16 tests and the reviewer's 10 methods (428 calls) passed. A deliberately broken variant failed; stale review approvals were rejected. See [the executed simulation](REVIEW-HANDOFF-SIMULATION.md) and its replay artifacts. Model-session delegation and snapshot integrity were handled externally by the orchestrator; automatic routing was pending at that point and is implemented in the Hermes follow-up above.
