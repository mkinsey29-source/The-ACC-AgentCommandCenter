# Live subagent handoff exercise

Date: September 16, 2026

This exercise uses actual session subagents for planning, implementation, and independent review. The responses are generated during the run, not selected from scripted verdicts. It complements the deterministic regression suite: scripted responses still make transport failures and invalid state transitions repeatable.

## What actually connects

ACC runs its ordinary coordinator, SQLite store, conversation router, supervised processes, workflow state machine, and snapshot validation. The initial message enters through the MCP bridge dispatch and authenticated HTTP endpoint. Each configured adapter is a transparent relay process: it announces the actual task packet, waits for a live agent's JSON result, and copies that result to ACC's result file.

The parent session routes those packets to session subagents. ACC's backend cannot call this session's subagent API by itself. This is a manually dispatched model transport feeding real ACC execution; it is not a claim that a production Hermes, Claude, or DeepSeek endpoint is installed.

The model roles are:

- Planner/coordinator: interprets the conversation request and, in later turns, decides the next stage from actual reports.
- Implementer: writes the requested module and tests in an isolated Git project.
- Reviewer: independently reads the frozen snapshot, evaluates the original requirements, and runs its own checks before returning a verdict.

The relay contains no implementation, response text, verdict, or transition decision. Because session subagents are dispatched externally, stopping the relay does not cancel a dispatched subagent; the parent session must cancel that agent separately. This exercise therefore does not establish production model-process containment. The orchestrating session does not preselect approval. If review requests changes, ACC can return to implementation through its normal bounded correction loop.

The exercise's adapters are marked local to exercise the permitted offline routing path. **The subagent inference itself runs through this online ChatGPT session.** This does not verify disconnected local-model performance or provider-specific output quality.

## Work request

The isolated project implements an engineer staffing allocator: jobs require exact levels; training removes staff from current capacity; any missing requirement stops the whole job. Inputs must be validated and left unchanged. This is a small, inspectable task related to Hearth and Havoc's workforce rules, not a change to the game repository.

The parent also prepares an independent numerical oracle before inspecting the implementation. It covers 729 combinations of requirements, staff, and training, 33 malformed input cases, and one higher-level substitution case. The independent reviewer is free to design additional checks.

## Reproducing the transport

From the ACC repository:

```sh
python examples/live-agent-handoff/run.py --run-dir /absolute/path/to/new-empty-run-directory
```

The run directory must be outside the managed repository. Open each `queue/*.request.json` to find the current packet and response path. Assign that packet to a live agent with the stated role. The agent must follow the packet's result contract and write its response atomically to the specified path. Implementation edits only the packet's isolated project; review inspects the snapshot and keeps scratch artifacts outside it.

The harness does not automatically call an LLM. Without a live agent supplying a response, it waits and eventually times out. The conversation planner uses ACC's 180-second deadline; workflow roles use the normal task deadline.

The result records the task, actual model decisions, workflow events, and a returning desktop-orchestrator context fetched through MCP/HTTP. The fixture module and tests are preserved separately from production ACC code.

## Evidence

Results and actor attribution are recorded in `docs/evidence/live-agent-handoff.json`; the reviewed files are under `examples/live-agent-handoff/accepted-snapshot/`. The corresponding verification commands and outcomes are recorded with the run evidence.


## Observed outcome

The actual live sequence completed on the first review round: one conversation plan, implementation, coordinator request for review, independent approval, and coordinator acceptance. Three distinct session subagents supplied five model responses. The implementer supplied 15 passing tests. The independent reviewer reran those tests, checked 4,096 combinations and 66 invalid inputs, and checked large integers and shared-input nonmutation. The parent's separate 763-case oracle also passed.

The returning desktop-orchestrator context contained the accepted task and no pending messages. Replaying the actual planner result twice returned the saved receipt and kept the task count at one; changing the reply after completion was rejected. No ACC code fix was needed to complete this live exercise.

Recheck the preserved implementation and independent review:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s examples/live-agent-handoff/accepted-snapshot -v
python examples/live-agent-handoff/verify_result.py examples/live-agent-handoff/accepted-snapshot
python examples/live-agent-handoff/independent_review.py examples/live-agent-handoff/accepted-snapshot
```

The independent review script is the reviewer's saved and rerun check, not a replacement response generated by the parent.
