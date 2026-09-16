# Laptop setup: Linux / Omarchy and Windows

ACC runs with Python 3.10+ and Git. The dashboard does not require an API key or a GPU. Hermes, authenticated providers, a local language-model runtime, GitHub CLI login, and local speech packages are separate host capabilities; setup does not pretend to install or authenticate them.

## First launch

The implementation is currently in the open `temporary` → `main` PR. For a new checkout while that PR is unmerged:

```bash
git clone --branch temporary https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter.git
```

For an existing ACC checkout, first inspect and preserve local changes, then fetch and select the existing `temporary` branch. Do not reset or discard local work.

Choose the Git project ACC should work on, not necessarily the ACC checkout itself. Replace the example paths below with your folders. Run commands in a terminal so that first-time setup can prompt.

Linux / Omarchy:

```bash
/path/to/The-ACC-AgentCommandCenter/start-acc.sh init --project "$HOME/projects/my-project"
/path/to/The-ACC-AgentCommandCenter/start-acc.sh doctor
/path/to/The-ACC-AgentCommandCenter/start-acc.sh
```

Windows PowerShell:

```powershell
& 'C:\tools\The-ACC-AgentCommandCenter\start-acc.ps1' init --project 'C:\projects\my-project'
& 'C:\tools\The-ACC-AgentCommandCenter\start-acc.ps1' doctor
& 'C:\tools\The-ACC-AgentCommandCenter\start-acc.ps1'
```

If local PowerShell policy prevents scripts from running, invoke the Python entry point directly; no policy change is needed:

```powershell
py -3 'C:\tools\The-ACC-AgentCommandCenter\acc\setup.py' init --project 'C:\projects\my-project'
py -3 'C:\tools\The-ACC-AgentCommandCenter\acc\setup.py' launch
```

The scripts work from any current directory. A launch without saved settings prompts for the project in an interactive terminal. For unattended setup, use `init --yes --project PATH`. The launcher starts the saved project and opens the browser with its ACC session token. Keep the terminal running; Ctrl+C stops the server. Use `launch --no-browser` when opening the dashboard separately.

Settings are saved under `~/.acc` (Windows: the user's home `.acc` folder):

| File | Purpose |
| --- | --- |
| `launcher.json` | Absolute project, Python, agents and state paths; port and diagnostic endpoint |
| `agents.json` | Host-only copy of the example adapters, with automatic conversation initially disabled |
| `hermes-mcp.json` | Exact mergeable MCP configuration with absolute executable, bridge and token-file paths |
| Project hash directory | Project database, recordings and session token |

Set `ACC_CONFIG_DIR` before setup and every launch to use another host settings folder. Keep this folder outside Git repositories. On Linux newly written JSON files have owner-only permissions. On Windows use a private user-owned folder. Existing adapter files and unknown launcher settings are preserved. Rerunning `init` refreshes the generated MCP fragment; it does not overwrite Hermes settings, remove project state, or change logins. Changing the project chooses a separate project state directory. Use `--state-dir PATH` to reuse an existing ACC state directory explicitly.

`init --yes --agents PATH` adopts an existing agents JSON file without copying or changing it. `--port 8766` changes the saved ACC port. Preserve a stable port and project path when recovering browser outbox messages. If the checkout moves, rerun `init` to update the MCP bridge path. If the Python installation moves, update the `python` value in `launcher.json` and rerun `init`.

## Finish the host connections

1. Install Python, Git, and Hermes on the laptop using their official installation instructions. Ensure the terminal running ACC can find `git` and `hermes` on PATH. Install GitHub CLI if GitHub operations are needed and run `gh auth login` yourself. ACC does not collect or print these credentials.
2. Configure and authenticate the actual Hermes profiles referenced in your host `agents.json`. The shipped names such as `acc-online`, `acc-deepseek`, `acc-claude`, and `acc-local-builder` are examples, not created accounts or installed profiles. Use your installed Hermes version's supported profile/login configuration; setup does not invent provider schemas or invoke paid models to check them.
3. Install a CPU-capable local language-model runtime and a suitable model yourself; start its loopback endpoint. Configure your local Hermes profiles to use it. `local: true` is an operator declaration and must match the real profile. A reachable server does not prove that a model is loaded or the profile is correct.
4. In ACC's orchestrator settings, select the intended agents and enable automatic handling only after those profiles are ready. Fresh setup keeps handling disabled, so merely launching ACC does not trigger paid inference. If adopting an existing agents configuration or state database, its existing routing and enabled state remain in effect.
5. For an external desktop orchestrator, merge the generated `hermes-mcp.json` fragment into the supported MCP configuration of the host. It uses `mcp_servers.acc` exactly as ACC's Hermes connector generator does. Start ACC before connecting, because the server creates the referenced token file on first launch. Other desktop clients may require a different outer configuration key; retain the generated command and argument array when adapting it. ACC does not install a connector into ChatGPT or configure a phone connection.

The default diagnostic endpoint is `http://127.0.0.1:11434/v1/models`. Change it with `init --yes --local-endpoint http://127.0.0.1:8080/v1/models` to the actual read-only model-list endpoint of your runtime. This only changes the diagnostic check, not Hermes provider routing. Only loopback HTTP URLs without credentials are accepted; diagnostics disable redirects and proxies.

## Optional CPU voice preparation

Local voice uses the existing `acc/transcribe.py` adapter with CPU/int8 defaults. Do the following explicitly while online. These commands download Python packages and public model files; they do not call a paid transcription service. The separate virtual environment avoids modifying the Omarchy system Python.

Linux / Omarchy:

```bash
python3 -m venv "$HOME/.acc/voice-env"
"$HOME/.acc/voice-env/bin/python" -m pip install faster-whisper huggingface_hub
"$HOME/.acc/voice-env/bin/python" -c 'from huggingface_hub import snapshot_download; from pathlib import Path; snapshot_download(repo_id="Systran/faster-whisper-base", local_dir=str(Path.home()/".acc"/"models"/"whisper-base"))'
/path/to/The-ACC-AgentCommandCenter/start-acc.sh init --yes \
  --voice-python "$HOME/.acc/voice-env/bin/python" \
  --voice-model-dir "$HOME/.acc/models/whisper-base"
```

Windows PowerShell:

```powershell
py -3 -m venv "$HOME\.acc\voice-env"
& "$HOME\.acc\voice-env\Scripts\python.exe" -m pip install faster-whisper huggingface_hub
& "$HOME\.acc\voice-env\Scripts\python.exe" -c "from huggingface_hub import snapshot_download; from pathlib import Path; snapshot_download(repo_id='Systran/faster-whisper-base', local_dir=str(Path.home()/'.acc'/'models'/'whisper-base'))"
& 'C:\tools\The-ACC-AgentCommandCenter\start-acc.ps1' init --yes `
  --voice-python "$HOME\.acc\voice-env\Scripts\python.exe" `
  --voice-model-dir "$HOME\.acc\models\whisper-base"
```

This uses the public [converted Whisper base model](https://huggingface.co/Systran/faster-whisper-base) and the documented [Hugging Face local-directory download API](https://huggingface.co/docs/huggingface_hub/guides/download). If a package does not provide a wheel for your Python version/platform, create the voice environment with a compatible supported Python version. Speech speed and accuracy must be checked on your actual CPU and microphone.

The voice setup option checks for `model.bin`, `config.json`, and `tokenizer.json`, then explicitly replaces only the top-level `transcription` entry in the selected agents file. Other agent settings are retained. This is the sole setup option that edits an existing agents file. Restart ACC after changing it. At runtime the adapter requires a local model directory and disables Hugging Face downloads; it never treats a missing path as an online model name.

After installing, run `doctor`, launch ACC, allow microphone access, and record a short sentence. Check the recognized text before using it as an instruction. Then repeat disconnected from the network. Package import and model-file presence are not proof of successful transcription. The default server's loopback browser origin is intended for a browser on the same laptop.

## Diagnose and verify

Use `doctor`, its alias `status`, or `doctor --json`. Checks report Python, Git and project status, GitHub CLI and its separate authentication status, Hermes executable availability, local endpoint reachability, configuration, and optional voice package/model files. Provider credentials are never printed. Exit code 1 means a base startup requirement failed; optional integrations may show TODO with exit code 0.

Automated setup tests cover idempotence, configuration/token preservation, separate project state, argument-safe paths with spaces and shell characters, subprocess timeouts, missing authentication and voice capability, and a real Bash launch from another working directory. Native PowerShell, real Hermes profile authentication, model quality, and browser microphone capture still require host verification. Setup and diagnostics do not launch inference.

See [Conversation workflow](CONVERSATION-WORKFLOW.md) and [Hermes connector](HERMES-CONNECTOR.md) for routing and MCP handoff details.


## GitHub and switching on the laptop

Authenticate GitHub CLI with `gh auth login` and ensure ordinary Git pushes work for the project remote. ACC polls PRs and remote commits every 30 seconds by default; **Refresh** asks for an immediate fetch of remote activity. The display keeps its last-success time when offline. Local edits and runner output continue independently.

For a managed task, **Switch after current step** records the new role assignment immediately and applies it when that step finishes. It does not terminate the model midway. Use **Stop now** when an immediate interruption is needed, then inspect retained work and resume with the chosen assignment. On Windows, native descendant-process containment still needs verification; adapters must remain attached and must not daemonize.

After managed acceptance, **Preview commit and PR** shows the repository, branch, changed paths, outgoing commits and PR description. Publishing commits reviewed files, pushes `temporary`, and creates or reuses its PR to `main`. It never merges. Unrelated edits must be resolved first. Code snapshots currently have a 10,000-file / 100 MiB limit; arbitrary clean filters (including LFS) require manual publication. These limits matter when ACC manages a large Unity or Blender asset repository.
