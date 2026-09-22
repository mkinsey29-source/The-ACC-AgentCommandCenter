# Model-driven workflow drivers

Every `kind: 'model'` agent in `agents.json` picks one of the drivers below via its `driver`
field. See [`examples/all-drivers-agents.json`](../examples/all-drivers-agents.json) for one
worked example of each; copy it outside the repository and fill in real paths/keys before use.
`{prompt_file}` and `{project}` substitution, the `local` flag, and the generic `argv`-based
custom adapter shape are unaffected by any of this — these are additional named drivers, not a
replacement for them. The [Hermes connector](HERMES-CONNECTOR.md) remains the way to route through
Hermes's own provider/profile system rather than a vendor's API or CLI directly.

An `api_key_file`/`key_file` path may use `~` for the current user's home directory; it is
expanded before the file is read. It should point at a local, private credential file — never a
task packet, committed file, or ACC's own database — matching the project's general "store
credentials through local credential references" convention. No provider driver here ever passes
a credential on the command line where it could leak into a process listing.

| Driver | Module | Talks to | Required fields | Notable optional fields |
| --- | --- | --- | --- | --- |
| `ollama` | `acc/ollama.py` | A local Ollama server's HTTP API | `model` | `host` (default `http://127.0.0.1:11434`) |
| `lmstudio` | `acc/lmstudio.py` | A local LM Studio (or other OpenAI-compatible) server | `model` | `host` (default `http://localhost:1234`) |
| `openai-compatible` | `acc/openai_compatible.py` | Any OpenAI-Chat-Completions-compatible endpoint | `base_url`, `model` | `api_key_file` |
| `dsh` | `acc/deepseek_harness.py` | DeepSeek Harness (`dsh`), spawned as a subprocess | — | `executable` (default `dsh`) |
| `deepastra` | `acc/deepastra.py` | DeepAstra (`launch.py`, wrapping Codex CLI) | `launcher` | `provider` (default `deepseek`), `key_file` |
| `gemini` | `acc/gemini.py` | Google's Gemini Interactions API | `api_key_file` | `model` (default `gemini-3.5-flash`), `endpoint` |
| `antigravity` | `acc/antigravity.py` | Google Antigravity CLI (`agy`), spawned as a subprocess | — | `executable` (default `agy`), `model`, `api_key_file`, `api_key_env` (default `ANTIGRAVITY_API_KEY`) |
| `claude` | `acc/claude_api.py` | Anthropic's Messages API | `api_key_file` | `model` (default `claude-opus-5`), `endpoint` |
| `claude-code` | `acc/claude_code.py` | Claude Code CLI (`claude`), spawned as a subprocess | — | `executable` (default `claude`), `model`, `api_key_file` |
| `grok` | `acc/grok_api.py` | xAI's Responses API | `api_key_file` | `model` (default `grok-4.7`), `endpoint` |
| `grok-build` | `acc/grok_build.py` | Grok Build CLI (`grok`), spawned as a subprocess | — | `executable` (default `grok`), `model`, `api_key_file` |
| `hermes` | `acc/hermes.py` | Hermes CLI, spawned as a subprocess | — | `executable` (default `hermes`), `provider`, `model`, `profile` — see [HERMES-CONNECTOR.md](HERMES-CONNECTOR.md) |

## Availability

Each driver reports `available` differently, reflecting what it can actually check without
spending money or requiring a live network call for its own sake:

- **Local HTTP drivers** (`ollama`, `lmstudio`): a live GET against the host. No process of their
  own exists to check for; a configured host that doesn't answer is unavailable regardless of
  whether any local binary is installed.
- **`openai-compatible`** also does a live GET, but — since it may point at a real paid gateway
  that requires authentication to answer at all, unlike `ollama`/`lmstudio`'s local no-auth
  servers — includes the configured `api_key_file`'s contents (if any) in that probe, so a server
  correctly rejecting an unauthenticated request isn't mistaken for one that's simply down.
- **API-key-file drivers** (`gemini`, `claude`, `grok`): whether the configured `api_key_file`
  exists on disk. Never a live authenticated call — that would either cost money or hit a rate
  limit just to report a status.
- **Subprocess/CLI drivers** (`dsh`, `antigravity`, `claude-code`, `grok-build`, `hermes`):
  whether the configured `executable` resolves via `PATH` (`shutil.which`).
- **`deepastra`** is the one exception: it checks that `launcher` is a real file on disk, since
  `launch.py` is a cloned script rather than a `PATH`-resolvable command.

None of these is proof of a working, authenticated connection — only that the adapter is
configured plausibly enough to attempt a run. The first real run is still the actual test.

## Process supervision

`hermes`, `dsh`, `deepastra`, `antigravity`, `claude-code`, and `grok-build` all spawn a real
subprocess that ACC supervises via `stop_tree()` (SIGTERM, then unconditionally SIGKILL on a
timeout). All of them inherit ACC's own process group so that reaches their children too, with one
documented exception: `deepastra.py` additionally walks `/proc` to find and kill DeepAstra's own
`codex` child, which `launch.py` detaches into its own session — see `acc/deepastra.py`'s module
docstring for why. `ollama`, `lmstudio`, `openai-compatible`, `gemini`, `claude`, and `grok` have
no subprocess of their own at all; a stop simply lets the in-flight HTTP request finish or time out.

All five subprocess-spawning provider drivers (`hermes`, `dsh`, `deepastra`, `antigravity`,
`claude-code`, `grok-build`) launch their worker CLI with a credential-filtered environment
(`worker_prompt.subprocess_env()`): anything named like `*KEY*`/`*TOKEN*`/`*SECRET*` is stripped
from what the subprocess inherits, so a model with shell/tool access can't read and exfiltrate
ACC's own unrelated secrets.

## Shared contract

Every driver in this table shares `acc/worker_prompt.py`: the same instructions are sent
regardless of transport, and the same `extract_json_object` recovers the required JSON result even
through a leading `<think>...</think>` reasoning trace or a markdown code fence — both real
behaviors from real models, not hypothetical edge cases.
