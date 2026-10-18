# PR25 test environment

This setup validates PR25 at `4a5efb980615011e60a0ea45a20d974c4c570975`.
No application code, native host settings, model credentials or user projects are changed.
Test state lives in temporary directories. No paid models are called.

## Local Linux or Windows

Use Python 3.12 and Git; Node 22 enables the JavaScript syntax check.
Python must include venv/ensurepip. On Debian/Ubuntu installations where it is
missing, install the matching `python3.12-venv` package first. A fix branch's
actual commit is recorded in results.json rather than assumed to be the original PR25 head.
From this checkout run:

```sh
python tools/pr25-tests/bootstrap.py --sdk
.local/pr25-venv/bin/python tools/pr25-tests/run.py --suite all
```

On Windows PowerShell the second command is:

```powershell
& .\.local\pr25-venv\Scripts\python.exe tools/pr25-tests/run.py --suite all
```

`--suite focused` runs conversation/setup; `full` runs the existing suite; `sdk`
runs existing external-client interoperability; `bridge` runs the real six-step
SDK discussion/privacy check, a workflow tracked by parent ID, and the stale-client regression. SDK dependencies
are optional and pinned in `tools/pr25-tests/requirements-sdk.lock`.
Each invocation creates a new `.local/pr25-results/` evidence folder. Exit 1
means a check failed; logs/results.json preserve failures and unrun native checks.
The runner removes TYPESAFE_API_KEY before each command. A missing SDK is
explicitly failed by the SDK-installed check; a skipped test is not native acceptance.

## GitHub Codespaces

Select this helper branch, then `.devcontainer/pr25/devcontainer.json` when
creating or rebuilding a Codespace. It provides Python 3.12, Node 22 and installs
the same SDK lock. Run the same command above in its terminal.
An existing Codespace can instead use a separate checkout/worktree of this branch
and run bootstrap.py without rebuilding. Preserve existing uncommitted work.
No ACC application is started and no service port is forwarded automatically.
Keep any manually forwarded ports private and stop the Codespace after tests.

For a GitHub CLI already authenticated for Codespaces, the equivalent remote
command is:

```sh
gh codespace ssh -c YOUR_CODESPACE -- 'cd /workspaces/The-ACC-AgentCommandCenter && .local/pr25-venv/bin/python tools/pr25-tests/run.py --suite all'
```

This assumes that checkout is already on this helper branch and bootstrapped.
Do not put an access token into the command or paste one into chat.

## What this can establish

Automated tests exercise actual Linux processes, stdio JSON-RPC, the MCP Python
SDK, loopback HTTP, SQLite and discussion-only flow. The SDK fixture is named as
a test client; its successful milestone is not a ChatGPT product connection.
These checks work locally or in a Codespace. A Codespace is a remote Linux host,
not a substitute for native Windows/Omarchy, microphones, local inference hardware,
or a supported ChatGPT desktop/remote MCP connection.

The remaining native acceptance is the six-tool discussion described in
`docs/LAPTOP-SETUP.md`, executed through the actual intended host. Record its real
client/version and verify the dashboard/doctor. Do not relabel SDK evidence as
that native-host acceptance. The PR25 historical server expects loopback Host
headers; opening a forwarded Codespaces web URL is not proof its host boundary works.

## Existing findings

PR25's existing GitHub review identifies stale client attribution: a new MCP
process can inherit the preceding verified timestamp while replacing the displayed
client. The dedicated regression deliberately fails until this behavior is fixed.
The existing SDK test selects `tasks[0]`, which can be a child record rather than
the parent it created. The added SDK workflow check selects the parent by its id
to distinguish that test-fixture failure from actual workflow execution.
The existing full suite may also expose runner teardown/orphan cleanup failures;
all failures are retained rather than marked passing. This environment does not
change those application behaviors or authorize a merge.
