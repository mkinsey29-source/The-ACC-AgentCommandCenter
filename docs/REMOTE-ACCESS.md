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
folder (digests only, owner-readable). A paired device has full control of ACC, like the local page, until
finer permission gates (SC-9) exist; Disconnect it if the phone is lost.

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

Tested here: every mode's accept and refuse rules, the pairing flow, the HTTP rules above, and the browser
pairing screen in headless Chromium. The Tailscale route is tested against a fake `tailscale` command that
mimics the output ACC reads; it has **not** been run against real Tailscale. Real Tailscale, a real phone,
and Docker still need checking on the owner's computer. There is no QR code yet; open the link or type the
code. The design follows ACC-Workspace's mobile pairing and Tailscale route (both MIT).
