## Second Brain — repository execution and Drive entry
Version: 3.0 — 2026-10-02 approved workspace direction

### Mandatory entry before repository work
Read Drive-root [AGENTS.md](https://drive.google.com/file/d/1BPuaOz5tZ3wDrhoGuBmgWEgQ4evGI35U/view?usp=drivesdk) once per session, then [ACC-Agent Command Center/AGENTS.md](https://drive.google.com/file/d/1DShX5lan_mvZhgMeXVofd21z4RGGNmk0/view?usp=drivesdk) and that project's root_summary.md, handoff.md and relevant lessons.md.
Project folder: https://drive.google.com/drive/folders/1yozjqM3sAt9ySZUPIj77PVZ1cOj3uPIf
Do not assume these files auto-load through the connector. Record the policy versions read, task identity and current branch/commit in the handoff.
If Drive is unavailable, checkpoint access failure, use last verified policy if available and continue only independent authorized work; mark Drive writeback pending. Do not claim memory synchronization succeeded.

### Isolation, scope and standards
One owner per task branch and separate working checkout/worktree. Never let two writers modify the same active checkout/branch. In connector-only work, one writer owns the branch. Verify the designated baseline and existing unmerged work; do not overwrite or merge unrelated branches.
Read applicable module AGENTS.md, architecture and specifications. Follow the language/toolchain conventions in current source, build manifests and project documentation. Keep simulation/data contracts separate from presentation where applicable. Check syntax, typing, serialization and boundary contracts relevant to the change.
Before edits, identify concrete applicable build/test commands from the repository's own manifests and documented workflow; record them in handoff.md. Execute relevant gates before handoff/merge, tie results to the exact tested commit, and list unavailable checks explicitly. Do not invent a passing result or substitute a small test for a required device acceptance gate.
Use temporary feature branches and PRs. A designated authorized agent may merge reviewed, validated work; Marvin is not required to manually merge every PR. Preserve explicit user integration, publication and deployment gates. In ACC, PR #35's production-runtime integration gate remains; this instructions-only migration does not merge it.
Do not run unrelated CI/builds for a documentation-only change. Verify instruction links, scope, retained project rules and the exact changed diff.

### Durable memory and review
Keep summary.md (session account), lessons.md (running findings/issues/user decisions), and handoff.md (executable continuation and reviewer packet) separate in Drive. Update at the triggers in project AGENTS.md, not every minor turn.
Handoff before review, transfer or pause. Include intended/completed checklist, direction changes, branch/commit/PR, setup, validation, next-agent steps, reviewer steps, anticipated risks, blocked work and deferred laptop/device tests. Carry unresolved IDs until evidenced closure or explicit cancellation.
Save dated three-file run snapshots in session-notes/<run-id>/ at session end. Project review records go in this project's 60_Review/, not the root queue. Reviews are separately started top-level sessions; this author's spawned subagents are not independent reviewers.
The orchestrator consolidates daily root summaries and writes cross-project reports in Drive /Orchestrator_Reports/. Read root_summary.md first; older summaries need not be routinely crawled, while relevant lessons/handoffs/source evidence remain accessible.
