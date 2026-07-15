# Second independent remote-access channel (DEV-15)

## Incident

2026-07-15: an ingress edit on `api.jarvis-d.com` plus a `Restart-Service`
on `cloudflared` took the tunnel down. SSH and RDP both route through that
same tunnel (see `docs/REMOTE_ACCESS.md`), so **both died at once** — the
machine was unreachable from anywhere. Only `/infra_restart` via the
Telegram bot (a process running locally on the machine already) could
recover it.

The single point of failure isn't cloudflared being unreliable — it's that
there was only **one** path in. This doc sets up a second path that shares
no process, config file, account, or vendor with cloudflared, so an outage
or operator mistake on one never takes down both.

## Options considered

| | Works after reboot (as a service) | No inbound port needed | Free | Independent of cloudflared |
| --- | --- | --- | --- | --- |
| **Tailscale** | Yes — `Tailscale` Windows service, auto-start | Yes — WireGuard + DERP relay fallback, no port-forward ever | Yes — free for personal tailnets (up to 100 devices) | Yes — separate binary, service, account, vendor |
| RustDesk | Yes — "install as service" mode | Mostly — public relay servers handle NAT traversal, but reliability/latency depend on a third-party relay unless you self-host one (extra infra to maintain) | Yes (self-hosted relay is free but is its own maintenance burden; public relay is free but shared/rate-limited) | Yes |
| AnyDesk | Yes | Yes | Free tier is **personal use only** — unattended/business access needs a paid license, and this machine runs a bot for Daniil's business use, so the free tier's ToS is a poor fit | Yes |

**Decision: Tailscale.** It's the only option that's fully free for this
use case, needs zero router/firewall changes, and (being a mesh VPN rather
than an HTTP proxy) has nothing in common with the cloudflared tunnel it's
meant to back up. RustDesk's public relay is a reasonable fallback if
Tailscale itself ever has an outage, but isn't picked as the primary
second channel because its NAT-traversal reliability is weaker without
self-hosting a relay.

## What this gets you

Once installed and logged in, the machine gets a stable `100.x.y.z`
Tailscale IP reachable from any other device on the same tailnet (e.g.
Daniil's laptop, once it also runs `tailscale up` on the same account) —
regardless of cloudflared's state. From there, plain SSH (`sshd`, already
set up per `docs/REMOTE_ACCESS.md` Section A) or RDP works exactly as it
would over a LAN.

## Setup (Daniil, interactive — CC does not run this)

This needs a real winget install, an admin PowerShell for the service
autostart, and a one-time browser login (`tailscale up`) — the same shape
as the existing `cloudflared tunnel login` step in
`docs/REMOTE_ACCESS.md` Section C. None of that is something a headless
coding agent should be doing unattended to the machine it might itself be
running on.

1. **Install + enable the service** (admin PowerShell):

   ```powershell
   scripts\remote\setup_tailscale_channel.ps1
   ```

   This installs Tailscale via `winget` if missing, and sets the
   `Tailscale` service to `Automatic` + starts it.

2. **Log in** (opens a browser once):

   ```powershell
   tailscale up
   ```

   Sign in with the account you want this tailnet under (a personal
   Google/Microsoft/GitHub account is enough for the free tier).

3. **Verify**:

   ```powershell
   scripts\remote\setup_tailscale_channel.ps1 -Status
   # or, for the full picture alongside cloudflared/sshd:
   scripts\remote\check_remote_status.ps1
   ```

   Expect `Healthy=True` and a `tailscale status` line showing this
   machine's `100.x.y.z` address.

4. **On the laptop**: install Tailscale, `tailscale up` with the *same*
   account, then `ssh Admin@<the desktop's 100.x.y.z address>` (or RDP to
   it) works independently of the Cloudflare tunnel.

## Live-test checklist (acceptance criteria, ≤5 min, Daniil)

This is the actual DEV-15 acceptance test. It intentionally repeats the
failure mode from the incident, so it must be run by Daniil with a second
device in hand (the laptop) — not automated, since automating "kill the
machine's primary access path" from an unattended agent is itself the
kind of risk this task exists to reduce.

1. From the laptop, confirm Tailscale reaches the desktop:
   `ssh Admin@<tailscale-ip> "hostname"` → should print `PC-LOE`.
2. On the desktop, stop cloudflared: `Stop-Service cloudflared`.
3. From the laptop, confirm the *cloudflared* path is now down (e.g. the
   `ssh jarvis-desktop` alias from `docs/REMOTE_ACCESS.md` times out or is
   refused) **and** the Tailscale path from step 1 still works.
4. Restore cloudflared: `Start-Service cloudflared` on the desktop, then
   `scripts\remote\check_remote_status.ps1` to confirm both channels are
   green again.

Expected result: step 3 proves the two channels don't share a failure
mode — losing cloudflared no longer means losing the machine.

## CLAUDE.md rule

Per DEV-15 acceptance point 3, `CLAUDE.md` now has a standing rule: before
any operation that touches cloudflared, the tunnel config, DNS/ingress, or
other machine-network settings, first confirm the second channel
(Tailscale) is healthy via `scripts\remote\check_remote_status.ps1`. If
it isn't, fix that first or escalate — don't touch the primary tunnel
while there's no verified fallback in.
