# Muse Spark Contributor workflow

Muse Spark 1.3 Contributor is a low-cost cloud coding worker for non-confidential game logic. ACC remains the orchestrator and the only bridge between the primary project and the isolated `HearthandHavoc-Meta` repository.

## Safety contract

- Jobs must declare `data_classification: public` and `workspace_scope: isolated_repository`.
- The Meta repository is a new repository, not a fork. It receives no primary Git history.
- Copy only the owned module, required interfaces, Unity `.meta` files, tests, and reviewed project-memory excerpts.
- Never copy credentials, `.env` files, user data, financial data, private licensed assets, build outputs, or unrelated modules.
- A repository branch records authorship but is not an access control. Use a separate repository credential restricted to `HearthandHavoc-Meta`.

## Handoff sequence

1. ACC grants one active writer exclusive ownership of one module, beginning with construction/building rules.
2. ACC creates a short-lived task branch in `HearthandHavoc-Meta` and materializes the task capsule at the same Unity-relative paths.
3. The Muse worker implements pure C# logic and tests. It cannot publish directly to the primary repository.
4. The worker returns its commit, changed paths, checks, token usage, and cost through the fenced integration-job result.
5. ACC verifies the allowlist, imports the patch onto the primary repository's `temporary` branch, and runs Unity/project checks.
6. A different provider reviews the imported snapshot. Accepted work merges through a PR; both temporary branches are deleted.

## Initial module order

1. Construction requirements, queues, pause/resume, and upgrades.
2. Resource production and Auralite logistics.
3. Battle calculations: damage, armor, morale, and targeting.
4. Enemy tactics and simulation scenarios.

Configure the provider only after creating the Meta account. Keep the key outside Git:

```json
{"integrations":{"providers":[{"id":"muse-spark-contributor","enabled":true,"credential_env":"META_MODEL_API_KEY"}]}}
```

The provider starts as unverified. A small disposable task must prove authentication, cancellation, result parsing, path restrictions, and cost reporting before production work.
