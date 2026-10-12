# Remote access: reachability modes and device pairing

ACC listens on this computer only by default. Remote access is opt-in, has several modes, and never
trusts the network: every remote request needs a paired device. Nothing here changes the default.

## Choose a mode

Start ACC with `--reach <mode>` (`python -m acc.server --project <folder> --reach <mode>`).

| Mode | ACC listens on | Reached through | Use it for |
|---|---|---|---|
| `local` (default) | `127.0.0.1` | this computer only | normal use; no remote access |
| `tailscale-serve` | `127.0.0.1` | a private Tailscale HTTPS route (`https://<device>.ts.net:8443`-`8446`) | your own phone or laptop on your tailnet; ACC itself stays on loopback |
| `private` | one explicit private address, `--host 192.168.1.20` (a `100.x` Tailscale address also qualifies) | `http://<that address>:<port>` | a home or office network |
| `docker` | not available yet | | refused until the owner's entry from the host is built and tested (see Docker below) |
| `custom` | `--host`, plus `--allow-host` for every accepted host | your own reverse proxy or tunnel | anything else; a public address also needs `--public-ack` |

Options: `--host`, `--allow-host <host[:port]>` (repeatable, exactly as the browser sends it),
`--public-ack` (custom only), `--behind-https` (a TLS front door serves the page, so the device cookie is
marked Secure), `--reach-check`.

Sign-in is separate from reachability: the mode decides where ACC listens, pairing decides who may use it.

**The owner's door.** When ACC listens on a non-loopback address (`private`, `custom`), it also opens a second,
loopback-only listener for the owner at this computer, on a port it chooses and prints in the launch link. The
network-facing listener never answers as the owner, even for a request that claims a loopback Host, and the owner
listener never answers remote hosts. Both share the same approvals and pairings. `tailscale-serve` keeps ACC on
loopback, so one listener serves both and the Host header tells them apart.

## Check before you start

`python -m acc.server --project <folder> --reach <mode> [options] --reach-check` prints every setting and
whether ACC would start, then exits (status 2 if it would refuse). It creates no files. For `tailscale-serve` it
also reads the Serve configuration, read-only, and fails if no port among 8443-8446 is free or the port is shared
publicly, exactly as startup would.

ACC also refuses to start, with the reason, for an unsafe combination:

- a remote mode with a control token shorter than 32 characters, or without device pairing;
- a non-loopback bind in `local` mode, or a non-loopback bind in `tailscale-serve` mode;
- `private` with a wildcard, public or hostname address;
- a wildcard bind with no accepted hosts;
- `docker` (not available yet);
- `custom` bound to loopback that also accepts a loopback name, since the owner and a device would look the same;
- `custom` on a public address without `--public-ack`;
- `tailscale-serve` when Tailscale is not running, has no HTTPS device name, has no free port among
  8443-8446, or reports the port as publicly shared (Funnel).

## Pair a device

1. At the computer, open ACC and expand **Remote access**. Choose **Create pairing link**. The link works
   once and expires after 5 minutes. An eight-digit code is shown too, for typing instead.
2. On the phone or laptop, open the link (or open ACC's remote address and enter the code). Name the device
   and choose **Request access**. The device shows a six-digit match code.
3. At the computer, check that the match code is the same, then **Approve**. The device connects. Approving
   hands it a credential once, as an HttpOnly, SameSite=Strict cookie that scripts cannot read.
4. **Disconnect** removes a device at once, including its open event stream.

Limits: at most 8 paired devices; a device is valid for 180 days; 60 pairing attempts a minute across the
node; a pending request expires after 5 minutes. Pairing state is the file `pairing.json` in ACC's state
folder (digests only, owner-readable). A paired device can do what the local page can, except the gated
operations below; Disconnect it if the phone is lost.

## Permission gates

Three kinds of caller reach the API, and only one is trusted without question.

| Caller | Credential | Gated? |
|---|---|---|
| The owner, at this computer | the owner token: memory-only, new at every start, held by the owner's browser tab; sent from `127.0.0.1` or `localhost` | never |
| The remote-driven orchestrator (the MCP bridge) | `agent-token` in the state folder, local hosts only | yes |
| A paired device | the device cookie, remote hosts only | yes |

ACC creates `agent-token` at first start (a private file, different from the owner token). `acc/setup.py` points new
bridge configurations at it. An older bridge or tool configuration that points at the former `token` file stops
working, because that file is removed at start and the owner token is no longer saved anywhere: change its
`--token-file` to `agent-token`.

A gated caller does not get to run these operations. Each becomes an approval request instead:

| Operation | Why it is gated |
|---|---|
| Create a task with a local command (`argv`), or start an existing one (`tasks/{id}/start` when its worker is the local command) | it executes any command as you |
| Export the archive (`archive/export`) | it writes files wherever the caller points |
| Publish a task (`publish`) | it pushes a branch and opens a pull request |
| Change GitHub settings (`github/configure`) | it decides where reviewed work is pushed |
| Recover an interrupted task (`recover`) | it asserts that you inspected the process tree |
| Accept shared memory (`memory/{id}/review` with `active`) | every agent then treats it as established |
| Rewrite a vault note's status (`knowledge/transition`), or rebut one (`knowledge/rebuttal`) | it overwrites an existing note and can mark it disproven |
| Add a vault note already marked `verified` or `active` (`knowledge/note`), or a check-in whose own status is `verified`/`active` or that carries `completed_knowledge` marked `verified` (`knowledge/checkin`) | it claims a verification nobody did |

How it works: the call returns `202` with `approval_required` and an approval id, and nothing runs. The request
appears under **Needs your decision** in ACC at this computer (the `attention` list in `/api/state`), saying who
asked and what it will do, with the complete command or push target shown below the (shortened) summary. **Approve** runs that stored request once, as you; **Deny** drops it.
The caller follows it with `GET /api/approvals/{id}` (the bridge has `acc_approval_status`). A request expires
after 10 minutes, at most 20 can wait, an identical retry returns the same request, and every step is written to
the Activity feed. Pending requests are kept in memory, so a restart drops them and the caller asks again.

A gated caller cannot raise its own permissions: approving, denying, pairing and the device list are for the owner
only, the agent token is refused on remote hosts, and the owner token is refused there too.

Not gated, on purpose: starting and running configured AI workers, creating tasks without a command, conversation,
project mode, orchestrator selection, rejecting memory, and ordinary knowledge check-ins (a check-in that does not claim `verified`/`active` status, whose completed knowledge is `supported`). Evidence on a check-in is only text the caller supplied, so it never replaces approval for a verified claim. Worker runs are the product and are
bounded by worktrees, snapshots and review; the gates cover what steps outside that boundary. Two limits to know:
a task's `permissions` field is still a label that nothing enforces, and an approved command runs with your full
user rights, so read the summary before you approve.

## Keeping the owner token private

The gates only mean something if the orchestrator cannot act as the owner, so ACC never hands it the owner token:

- The owner token is generated at every start and lives only in the server's memory and the owner's browser tab. It
  is not in the state folder, a URL, a process argument, or the terminal output.
- The browser receives it through a one-use launch code (valid 10 minutes), exchanged only on this computer: a paired
  device or the agent token on a remote host is refused, and the refusal does not use up the code. The page keeps the
  token in the tab's memory only, never in browser storage, so reloading the tab needs a new link.
  A code that leaks is useless after the
  first use, and a stolen first use shows up as your own link failing. **New window** in ACC mints another link.
- The old `token` file from earlier versions is deleted at start.

What this stops: an agent that reads files in the state folder, or the command line and output of ACC, finds no
owner token. It finds only `agent-token`, whose risky actions wait for your approval.

What it does not stop: an agent running as **your** operating-system account can still read your browser's profile
or a screenshot of your screen, or read ACC's memory, and so can act as you. For a real boundary, run the
orchestrator under a different account or in a container:

1. Run ACC as you, with the state folder private (`chmod 700`, the default).
2. Put the orchestrator's credential where only its account can read it:
   `python -m acc.server ... --agent-token-file /home/orchestrator/acc/agent-token` (owned by that account, mode 0600
   or group-readable by it), and point the bridge's `--token-file` at the same file.
3. Run the orchestrator, and the vendor remote session that drives it, as that account. It cannot read your state
   folder, your browser profile or ACC's memory.

On Windows the same shape needs a separate Windows account or a container; the vendor desktop apps normally run as
you, so there the launch-code protection is the realistic limit.

## What protects it

- The control token is accepted only for requests whose Host is `127.0.0.1` or `localhost`, from a loopback
  client, and is never given to a remote device. A remote request with the token is refused.
- A remote request is accepted only if its Host is one the mode allows, and needs the device cookie. Pairing
  management (create, approve, deny, disconnect, the device list) is for the owner at this computer only; a
  paired device cannot pair others or approve itself.
- Cross-site and foreign-origin requests are refused; a remote Origin must match its own Host.
- `tailscale-serve` keeps ACC on loopback. It never runs Tailscale's reset, picks only a port that is free in the
  Serve configuration it has just read (read twice, right before it changes anything), refuses a publicly shared
  port, and afterwards checks that the route is exactly its own and that no other Serve setting changed. Tailscale
  offers no atomic compare-and-set, so a different tool changing the same port in the instant between ACC's last
  read and its command cannot be excluded; ACC reports it if it notices and does not claim more. On clean shutdown
  it removes its route.

## Docker

`--reach docker` is refused for now. A container's owner needs a loopback-only port published to the host and a way
to tell that apart from the published device port; that entry has not been built or tested, and starting a mode
where the owner cannot sign in safely is worse than not starting. Run ACC on the host, or use `tailscale-serve` or
`private`. Docker will return once its owner entry is built and tested on a real container.

## What is and is not verified

Tested here: every mode's accept and refuse rules, the pairing flow, the HTTP rules above, the permission gates, and the browser
pairing and approval screens in headless Chromium. The Tailscale route is tested against a fake `tailscale` command that
mimics the output ACC reads; it has **not** been run against real Tailscale. Real Tailscale, a real phone,
and Docker still need checking on the owner's computer. There is no QR code yet; open the link or type the
code. The design follows ACC-Workspace's mobile pairing and Tailscale route (both MIT).
