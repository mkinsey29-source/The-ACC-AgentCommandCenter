# ACC Build 2: one runtime, two providers

This is a personal/local development bridge for the standalone Android APK.
Claude Code and Codex run as their **unmodified official binaries**. Sign in
through those programs; ACC does not read their token files or offer a Claude
subscription login of its own. Both adapters use the same authenticated HTTP
contract, chat UI, cancellation and recovery handling.

Build 2 adds text conversations. It does not yet start task-board agents, write
project files, run a terminal shell from the APK, or attach Sentry notes. Only the
displayed conversation is sent to the selected provider. Conversations and
pairing are separate per runtime/project/provider. Model and billing changes
create a new local connection identity. **New conversation clears the displayed
thread**, so save anything you need in Sentry first.

## Android local setup (experimental, needs device acceptance)

Install Termux using its [official installation instructions](https://github.com/termux/termux-app#installation).
Open Termux and run:

```sh
pkg update
pkg install python git proot-distro
proot-distro install debian
proot-distro login debian
```

PRoot supplies a Linux userland, not Docker or kernel isolation. Do not enable
ACC's deliberately refused Docker mode. The commands above follow current
[PRoot-Distro instructions](https://github.com/termux/proot-distro/tree/5a427a1f924550c176ab34197e9fcf5d010fdbd9).
An older Termux package may use a different image installer; check its own help.
The provider vendors do not list Android/PRoot as a supported native platform.
Successful Linux tests do **not** establish tablet compatibility. In particular,
Codex sandbox support may fail under PRoot. ACC will report the failure; do not
work around it by enabling full access or bypassing the sandbox.

In the Debian shell:

```sh
apt update
apt install python3 git nodejs npm ca-certificates
useradd --create-home --shell /bin/bash acc
su - acc
mkdir -p "$HOME/.local"
npm config set prefix "$HOME/.local"
export PATH="$HOME/.local/bin:$PATH"
npm install -g @anthropic-ai/claude-code@2.1.304 @openai/codex@0.163.0
claude auth login
codex login
```

Use a full Node 22+ installation if the distro's package is older. Claude's npm
package may report an engine warning on older Node versions; the supported
requirement remains Node 22+. Installation uses the vendors' own published
packages; it does not patch their binaries. Do not use `sudo npm install -g`.

Each login opens or prints the provider's own browser flow. Follow its terminal
instructions. For Codex, `codex login --device-auth` is an alternative when
supported/enabled for your account. Do not paste login codes, provider tokens or
passwords into ACC chat, Drive, GitHub, or this conversation.

Obtain this source branch (the release notes identify its exact commit):

```sh
git clone --branch feature/android-build-2-providers-20261019 --single-branch https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter.git acc-mobile
cd acc-mobile/mobile/runtime
python3 acc_mobile_runtime.py --show-pairing
```

The terminal displays the listener URL and a newly generated **ACC pairing
secret**. Keep it private. In ACC, open **Connections**, use
`http://127.0.0.1:8765`, enter that pairing secret, and tap **Pair and check both**.
Then choose Claude or ChatGPT / Codex under **Chat** and send a message.

For later starts, open Termux, enter the same Debian environment/user, restore
`PATH="$HOME/.local/bin:$PATH"`, and run the last Python command again. Keep the
terminal open. Android may terminate it; battery exclusions or keeping a terminal
visible are mitigations, not survival guarantees. Persistent notification and
managed runtime installation remain future APK work.

If local provider execution fails under PRoot, record the exact non-secret error.
The optional computer-host path below is an alternative for testing, not a
decision to make ACC remote-only.

## Optional computer host

Use Python 3.10+, the official Claude Code 2.1.304+ and Codex 0.163.0+ binaries,
and an ordinary private user account on a supported Linux/macOS host. The adapter
flags were checked against those exact CLI versions. Windows is not accepted by
this test run; process-group cancellation there has less containment than POSIX.
Install and sign in using vendor documentation, then run the Python file above.

Loopback HTTP is only for an APK/runtime on the **same device**. To connect a
tablet to a different computer, use a concrete private IP and a TLS certificate
trusted by Android, for example one obtained through your existing Tailscale
setup. This bridge does not change Tailscale Serve or install a tunnel:

```sh
python3 acc_mobile_runtime.py --bind YOUR_PRIVATE_IPV4 --cert /private/cert.pem --key /private/key.pem --show-pairing
```

Pair using the matching `https://` hostname from that certificate. HTTP LAN
addresses, URL credentials, paths, query strings and redirects are refused by the
APK. The runtime refuses browser-origin requests and offers no CORS exemption.
There is no unauthenticated health or chat endpoint.

## Billing and connection evidence

The runtime checks `claude auth status --json` / `codex login status`. It returns
only sign-in state and a coarse billing classification, never their raw output,
email, account IDs or provider credentials. The child environment excludes API
key variables and unrelated service secrets; saved credentials remain owned by
each official CLI. Authentication reported as subscription is **not** evidence
that inference, a chosen model or quota will work.

API or unknown billing is blocked unless you explicitly enable the separate
**Allow separately billed API or unconfirmed billing** checkbox and save the
connection. This is billing consent, not a spending cap. No automatic provider
fallback is implemented. The adapter uses no API keys supplied through ACC.

OpenAI's current app-server documentation limits its own auth surface to local
or open-source applications and says commercial/hosted services require the
appropriate Sign in with ChatGPT route. The official open-source SIWC flow has
dynamic registration; paid/remote apps are directed to the partner interest form.
This local test implementation is not approval to sell ACC with subscription
access. Anthropic allows customers to run unmodified Claude Code under its stated
commercial conditions, with each end user authenticating themselves, and forbids
third-party collection/intermediation of Claude.ai credentials. Confirm the
commercial arrangement before a paid release.

Sources checked October 19, 2026:

- [OpenAI authentication](https://learn.chatgpt.com/docs/auth)
- [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex app-server authentication boundary](https://learn.chatgpt.com/docs/app-server#authentication-endpoints)
- [Open-source SIWC / paid-app distinction](https://developers.openai.com/siwc/token-sharing-open-source)
- [Claude product/authentication conditions](https://code.claude.com/docs/en/legal-and-compliance)
- [Claude CLI options](https://code.claude.com/docs/en/cli-reference)
- [Claude native npm installation](https://code.claude.com/docs/en/setup#install-with-npm)

## Recovery and limits

- One request runs at a time across both adapters. Requests have caller-generated
  IDs; replays with identical content reuse the job, different content is refused.
- The APK saves pending intent before sending. Reopening a pending conversation
  polls that ID; it does not send the prompt again. **Check response** is read-only.
- Runtime jobs are memory-only, bounded to 100, and expire after 30 minutes when
  cleaned on a new submission. A runtime restart loses them. A 404 is an unknown
  outcome, not success or automatic permission to rerun. **Keep as interrupted**
  only releases the local waiting state; it does not stop the provider.
- **Stop request** asks the runtime to cancel. Disconnection is not cancellation
  confirmation. The terminal remains the place to check an uncertain outcome.
- Provider calls time out after 120 seconds. Output is capped; failed, malformed,
  incomplete and empty results are rejected. Raw CLI failure diagnostics are not
  returned over HTTP or logged by the bridge.
- Claude disables built-in and MCP tools, runs safe/restricted mode and doesn't
  persist a CLI session. Codex uses read-only sandboxing with shell execution,
  web search, images and child agents disabled and user config/rules ignored.
  This is not a general process sandbox or an isolation guarantee against the
  same OS user. CLI-managed policy settings still apply.
- Cancellation kills the POSIX process group and waits for the child. Detached
  descendants are not proven contained. Windows, real Termux, real Tailscale,
  real account/paid inference, and process-death recovery require separate checks.
- Android pairing uses Expo SecureStore; web preview pairing is memory-only.
  Conversation history is app-private AsyncStorage, not encrypted chat storage.
  It is not copied into the shared Sentry folder automatically. Uninstall clears
  app-private conversations and pairing.

## Developer checks

```sh
unset TYPESAFE_API_KEY
python3 -m unittest discover -s tests -p test_mobile_runtime.py
cd mobile
unset TYPESAFE_API_KEY
node scripts/test-providers.cjs
npm run typecheck
npm run lint
```

Fixture processes verify transport and adapter behavior. They are explicitly not
evidence of a live provider response or physical Android compatibility.
