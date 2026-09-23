# Autonomous project routing

ACC can turn one conversation request into a dependency-aware task graph and run it without task-by-task operator approval. The conversation orchestrator proposes bounded actions; ACC validates the graph, assigns each workflow role, serializes writers, and runs implementation, coordination, independent review, and corrections.

## Decision boundary

ACC owns hard rules: availability, online/offline policy, declared role restrictions, required capabilities, reviewer independence, exact metrics, retry limits, and execution. TypeSafe Jev optionally supplies semantic-fit probabilities for the already eligible agents. A missing credential, service error, or low-confidence Jev result falls back to deterministic scoring; it does not request user approval.

The deterministic composite uses semantic fit, declared quality, observed reliability/acceptance, cost tier, and continuity. Raw outcomes remain stored by agent, workflow role, and task area so weights can change without rewriting history.

## Configuration

Add routing metadata to each agent and enable the top-level router:

```json
{
  "agents": [
    {
      "id": "python-builder",
      "name": "Python builder",
      "argv": ["worker", "{prompt_file}"],
      "routing": {
        "roles": ["implementer"],
        "capabilities": ["code.python"],
        "quality_tier": 4,
        "cost_tier": 2
      }
    }
  ],
  "routing": {
    "enabled": true,
    "confidence_threshold": 0.55,
    "max_failures": 3,
    "typesafe": {
      "api_key_env": "TYPESAFE_API_KEY",
      "model": "jev-latest"
    }
  }
}
```

`roles` may contain `implementer`, `reviewer`, and `coordinator`. Capabilities use dotted lowercase names. Undeclared capabilities mean unknown rather than ineligible, which keeps older configurations usable. Declared capabilities are enforced as hard requirements. Quality and cost tiers range from 1 through 5; a lower cost tier means cheaper execution.

Keep the TypeSafe key in the named environment variable or use `api_key_file`. ACC sends one request containing independent Choice questions for all three roles, following TypeSafe's parallel-question pattern. Credentials are resolved only at request time and are not persisted.

## Autonomous behavior

- Project requests may create up to 20 actions with `action_id`, `depends_on`, priority, task area, required capabilities, and risk.
- Dependency cycles and unknown references are rejected atomically.
- Low-confidence or unavailable Jev routing continues with deterministic evidence.
- A failed or malformed bounded worker result is recorded and reassigned after the old process has released the workspace.
- Repeated rejected corrections can replace the implementer and begin a fresh correction budget.
- The workflow pauses only when no eligible replacement remains, retry limits are exhausted, required credentials/tools are unavailable, or safe process ownership cannot be proven.
- Independent review is mandatory; it is an internal quality gate, not an operator approval prompt.

The coordinator snapshot exposes routing readiness, aggregated profiles, and each task's routing decision. Cost and token metrics are recorded when adapters include `cost` and `usage` in their structured result.
