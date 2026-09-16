# Implementer-to-reviewer handoff: executed simulation

September 16, 2026. ACC baseline: `fd33addd893b53fb8e1d62cbc5a8e3104fbd99a9`.

## Result

A real implementation sub-agent produced code and tests. A separate real review sub-agent inspected a fixed copy against the original requirements and wrote independent tests. ACC ran the validation commands through its actual stdio MCP → local HTTP → coordinator path and recorded the review outcome.

The valid implementation reached `accepted`. A deliberately broken variant failed independent tests and remained failed. Stale approval attempts were rejected. No DeepSeek or Claude provider was involved; the participants were this session's sub-agents acting in those two roles.

## What was handed off

The exercise implements a small pure Python job-progress function, using the game's exact-level staffing rule. Work requires every staff, material, and energy prerequisite; shortages retain progress. The exercise does not change HearthandHavoc or claim to implement its full simulation.

- Implementer: `/root/handoff_implementer`.
- Reviewer: `/root/handoff_reviewer`, started with fresh context containing the requirements and snapshot location, not the implementer's conversation.
- Snapshot: `sha256:edcbb60e68b29865a261387e8beac3bdf8ac5cb44917aeccf6c86ca5b79b4765`.
- Contents: original requirements, implementation, implementer tests, and handoff notes. Hashes were checked before and after review. The original copy was made read-only; this is an integrity check, not a security boundary against privileged code.

## Executed sequence and evidence

| Step | Observed result |
| --- | --- |
| Record tasks through actual MCP stdio | Task creation persisted in ACC. |
| Implementation agent writes code | Three deliverables: implementation, tests, handoff. |
| ACC launches implementer validation | 16 tests passed; state became `awaiting_review`, not accepted. |
| Freeze a fixed code version | Four payload hashes recorded in a manifest. |
| Independent agent inspects and tests snapshot | 10 test methods, 428 target calls passed, including a 160-case exact-rational gate/progress matrix. Verdict: APPROVE within the documented numeric domain. |
| Run those independent tests through ACC | Exit code 0; actual process output persisted. |
| Try to approve an earlier run | Rejected: run ID no longer matches. |
| Try to approve the wrong requirement revision | Rejected: revision no longer matches. |
| Attach reviewer findings and acceptance | Valid current run/revision accepted with the snapshot ID as evidence reference. |
| Inject a known defect into a separate copy | Changed exact-level staffing to allow higher-level substitution. This defect was injected by the orchestrator, not found in the implementer's original work. |
| Run independent tests on that negative control | 15 failing assertions/subcases across the 10-method suite; runner exit code 1; ACC state `failed`. |
| Replay approval against a failed run | Rejected: only completed runs awaiting review can be accepted. |
| Recheck original snapshot | Hashes unchanged. |

The reviewer identified a specification boundary: unrestricted arbitrary-size real inputs cannot always be preserved in a finite float return. This was documented, not silently treated as solved. The review report explains the limitation and its reproduction.

## What ACC did versus what the orchestrator did

**ACC did:** persist task records, start real validation subprocesses, stream/store their output, distinguish completion from acceptance, reject stale run/revision approvals, reject acceptance of failed runs, and store attributed findings.

**The supervising orchestrator did:** launch the two sub-agents, create the fixed copy and manifest, assign the reviewer, collect its response, verify the hashes, and submit acceptance. ACC did not autonomously launch or route these model sessions. Reports label that external delegation rather than pretending ACC owned their running processes.

This proves the manually orchestrated handoff and existing transport/gates can be exercised without DeepSeek or Claude. It does **not** prove automatic provider-to-provider routing, native host integration, model quality/cost equivalence, browser behavior, safe active takeover, or offline fallback.

ACC currently stores a supplied snapshot reference; it does not yet enforce that files still match it. Hash enforcement was performed by the simulation driver. Automated snapshotting, reviewer scheduling, change-request/correction routing, and evidence-integrity checks remain implementation work.

## Reproduce the transport and validation stages

From the repository root:

```sh
python3 examples/review-handoff/replay.py
```

On Windows, use `py -3` instead of `python3`; native Windows execution is still unverified.

The replay starts an isolated local coordinator, invokes the actual MCP bridge as a subprocess, runs both preserved test suites, tests stale approvals, injects the negative-control defect in a temporary copy, and checks snapshot integrity. It uses no provider credentials and does not launch new model sessions. Temporary runtime state is cleaned up after it exits.

Saved artifacts:

- [Preserved implementation snapshot](../examples/review-handoff/snapshot/)
- [Independent review](../examples/review-handoff/REVIEW.md)
- [Independent test suite](../examples/review-handoff/test_independent.py)
- [Replay driver](../examples/review-handoff/replay.py)
- [Replay results](evidence/review-handoff-replay.json)

## Separate outstanding PR review findings

GitHub's automatic review of the baseline additionally identified a launch-before-persistence crash window, inability to retry an already-stopping worker's failed termination, and dropped UI history when output exceeds the snapshot window. These are tracked findings in PR #1, not defects established or repaired by this handoff exercise. This report does not mark the overall PR production-ready.
