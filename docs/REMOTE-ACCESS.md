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
| `docker` | `0.0.0.0` inside a container | the address the container publishes, set with `--allow-host` | running ACC in a container |
| `custom` | `--host`, plus `--allow-host` for every accepted host | your own reverse proxy or tunnel | anything else; a public address also needs `--public-ack` |

Options: `--host`, `--allow-host <host[:port]>` (repeatable, exactly as the browser sends it),
`--public-ack` (custom only), `--behind-https` (a TLS front door serves the page, so the device cookie is
marked Secure), `--reach-check`.

Sign-in is separate from reachability: the mode decides where ACC listens, pairing decides who may use it.

## Check before you start

`python -m acc.server --project <folder> --reach <mode> [options] --reach-check` prints every setting and
whether ACC would start, then exits (status 2 if it would refuse). It creates no files.

ACC also refuses to start, with the reason, for an unsafe combination:

- a remote mode with a control token shorter than 32 characters, or without device pairing;
- a non-loopback bind in `local` mode, or a non-loopback bind in `tailscale-serve` mode;
- `private` with a wildcard, public or hostname address;
- a wildcard bind with no accepted hosts;
- `docker` outside a container (set `ACC_IN_DOCKER=1` if detection fails), or without `--allow-host`;
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
| The owner, at this computer | `token` in the state folder, sent from `127.0.0.1` or `localhost` | never |
| The remote-driven orchestrator (the MCP bridge) | `agent-token` in the state folder, local hosts only | yes |
| A paired device | the device cookie, remote hosts only | yes |

ACC creates `agent-token` at first start, a different secret from `token`. `acc/setup.py` now points new bridge
configurations at it. An existing bridge configuration keeps using `token` (ungated) until you change its
`--token-file` to `agent-token`.

A gated caller does not get to run these operations. Each becomes an approval request instead:

| Operation | Why it is gated |
|---|---|
| Create a task with a local command (`argv`) | it executes any command as you |
| Export the archive (`archive/export`) | it writes files wherever the caller points |
| Publish a task (`publish`) | it pushes a branch and opens a pull request |
| Change GitHub settings (`github/configure`) | it decides where reviewed work is pushed |
| Recover an interrupted task (`recover`) | it asserts that you inspected the process tree |
| Accept shared memory (`memory/{id}/review` with `active`) | every agent then treats it as established |
| Rewrite a vault note's status (`knowledge/transition`), or rebut one (`knowledge/rebuttal`) | it overwrites an existing note and can mark it disproven |
| Add a vault note already marked `verified` or `active` (`knowledge/note`) | it claims a verification nobody did |

How it works: the call returns `202` with `approval_required` and an approval id, and nothing runs. The request
appears under **Needs your decision** in ACC at this computer (the `attention` list in `/api/state`), saying who
asked and exactly what it will do. **Approve** runs that stored request once, as you; **Deny** drops it.
The caller follows it with `GET /api/approvals/{id}` (the bridge has `acc_approval_status`). A request expires
after 10 minutes, at most 20 can wait, an identical retry returns the same request, and every step is written to
the Activity feed. Pending requests are kept in memory, so a restart drops them and the caller asks again.

A gated caller cannot raise its own permissions: approving, denying, pairing and the device list are for the owner
only, the agent token is refused on remote hosts, and the owner token is refused there too.

Not gated, on purpose: starting and running configured AI workers, creating tasks without a command, conversation,
project mode, orchestrator selection, rejecting memory, and ordinary knowledge check-ins, which already require evidence. Worker runs are the product and are
bounded by worktrees, snapshots and review; the gates cover what steps outside that boundary. Two limits to know:
a task's `permissions` field is still a label that nothing enforces, and an approved command runs with your full
user rights, so read the summary before you approve.

**The gates are a guardrail, not a sandbox.** They stop a remote-driven agent that uses ACC's API or the bridge.
An agent that can also read your files can read `token` in the state folder and act as you. Keep the
orchestrator's file access away from the state folder, or run it as a different operating-system user. Keeping
the owner token off disk is a follow-up.

## What protects it

- The control token is accepted only for requests whose Host is `127.0.0.1` or `localhost`, from a loopback
  client, and is never given to a remote device. A remote request with the token is refused.
- A remote request is accepted only if its Host is one the mode allows, and needs the device cookie. Pairing
  management (create, approve, deny, disconnect, the device list) is for the owner at this computer only; a
  paired device cannot pair others or approve itself.
- Cross-site and foreign-origin requests are refused; a remote Origin must match its own Host.
- `tailscale-serve` keeps ACC on loopback. It never resets or replaces anyone else's Tailscale Serve setup,
  refuses a publicly shared port, and checks the route is exactly its own before turning remote access on.
  On clean shutdown it removes its route.

## Docker

ACC does not ship an image yet. A container needs these settings:

```
python -m acc.server --project /work --state-dir /state --port 8765 \
  --reach docker --allow-host localhost:8765
```

and the container's port published to the address you will browse (for example `-p 127.0.0.1:8765:8765`).
Browse to the address in `--allow-host`, then pair as above. Keep `/state` on a volume.

## What is and is not verified

Tested here: every mode's accept and refuse rules, the pairing flow, the HTTP rules above, the permission gates, and the browser
pairing and approval screens in headless Chromium. The Tailscale route is tested against a fake `tailscale` command that
mimics the output ACC reads; it has **not** been run against real Tailscale. Real Tailscale, a real phone,
and Docker still need checking on the owner's computer. There is no QR code yet; open the link or type the
code. The design follows ACC-Workspace's mobile pairing and Tailscale route (both MIT).
