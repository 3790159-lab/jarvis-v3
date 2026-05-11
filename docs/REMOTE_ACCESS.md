# Remote access: SSH + Cloudflare Tunnel

Goal: SSH from the laptop into the desktop (`PC-LOE`, user `Admin`) over a
Cloudflare Tunnel — no port forwarding, no public IP exposure. VSCode
Remote-SSH works on top of this.

## State of the desktop as of writing

- OpenSSH Server: **not installed** — must be added (Section A).
- `cloudflared`: **already installed** at
  `C:\Program Files (x86)\cloudflared\cloudflared.exe`. The download in
  Section B is only needed if your install is missing.
- No tunnel logged in yet, no `~/.cloudflared/` directory.
- No SSH firewall rule.
- Desktop user: `Admin`. Desktop name: `PC-LOE`.

Substitute `<DESKTOP_USER>` = `Admin` and `<DESKTOP_HOST>` = `PC-LOE`
when copying snippets.

> Sections marked **[admin]** need an elevated PowerShell ("Run as
> administrator"). Sections marked **[browser]** open a browser window.

## Estimated total time

| Section | Time | Where |
| --- | --- | --- |
| A. Install OpenSSH Server | 5 min | desktop, admin |
| B. cloudflared binary | 2 min | desktop |
| C. cloudflared login | 3 min | desktop, browser |
| D. Create tunnel + DNS | 5 min | desktop |
| E. Register cloudflared service | 3 min | desktop, admin |
| F. SSH key | 2 min | laptop |
| G. Laptop SSH config | 2 min | laptop |
| H. VSCode Remote-SSH | 5 min | laptop |
| I. Verify | 1 min | laptop |
| **Total** | **~30 min** | |

---

## Section A — Install OpenSSH Server **[admin]**

Open PowerShell **as administrator** on the desktop. Then run:

```powershell
# Install OpenSSH Server feature
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# Enable + start the service
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic

# Allow inbound port 22 (LOCAL only — the tunnel handles external)
New-NetFirewallRule -DisplayName "OpenSSH Server (sshd)" `
    -Enabled True -Direction Inbound -Protocol TCP -Action Allow `
    -LocalPort 22 -Profile Any

# Sanity check
Get-Service sshd | Format-List Name, Status, StartType
```

Expected output: `Status: Running`, `StartType: Automatic`.

You can confirm anywhere with the project helper:

```powershell
C:\jarvis\scripts\remote\check_remote_status.ps1
```

---

## Section B — cloudflared binary

Your desktop already has cloudflared at
`C:\Program Files (x86)\cloudflared\cloudflared.exe`. To confirm:

```powershell
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" --version
```

If the file is missing, fetch the latest with the helper script:

```powershell
C:\jarvis\scripts\remote\install_cloudflared.ps1
```

It downloads the binary to `C:\jarvis\scripts\remote\cloudflared.exe` —
no admin needed for the download.

Throughout the rest of this doc, the placeholder `<CFD>` means either
the system path or the project copy, whichever you have. Set it for the
current session if you prefer:

```powershell
$CFD = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
# or, if installed via the helper:
# $CFD = "C:\jarvis\scripts\remote\cloudflared.exe"
```

---

## Section C — Cloudflare authentication **[browser]**

```powershell
& $CFD tunnel login
```

A browser tab opens. Sign into your Cloudflare account and select the
zone (domain) you want to use. The cert lands in
`$env:USERPROFILE\.cloudflared\cert.pem`.

### No Cloudflare account / domain?

You have two paths from here:

- **Path A (recommended): named tunnel + your domain** (Section D).
  Permanent URL like `ssh.example.com`, survives restarts, free.
- **Path B (fallback): Quick Tunnel.** Run
  `cloudflared tunnel --url ssh://localhost:22` and Cloudflare hands
  you a random `*.trycloudflare.com` URL that changes every restart.
  Use for one-off testing only — DNS routing in Section D requires a
  real zone.

The rest of this guide assumes **Path A**.

---

## Section D — Create the named tunnel

```powershell
& $CFD tunnel create jarvis-desktop
```

This prints a tunnel UUID and writes a credentials file at
`$env:USERPROFILE\.cloudflared\<UUID>.json`. Save the UUID — you need it
for the config below.

Create `$env:USERPROFILE\.cloudflared\config.yml`:

```yaml
tunnel: <UUID-from-create-step>
credentials-file: C:\Users\Admin\.cloudflared\<UUID>.json

ingress:
  - hostname: ssh.<your-domain>.com
    service: ssh://localhost:22
  - hostname: rdp.<your-domain>.com
    service: rdp://localhost:3389
  - hostname: api.<your-domain>.com
    service: http://localhost:8010
  - service: http_status:404
```

The `api.*` entry exposes the Jarvis backend (port 8010) — drop the
line if you don't want that. The `rdp.*` entry is optional; SSH alone
is enough for VSCode Remote-SSH.

Register DNS records that point those hostnames at the tunnel:

```powershell
& $CFD tunnel route dns jarvis-desktop ssh.<your-domain>.com
& $CFD tunnel route dns jarvis-desktop rdp.<your-domain>.com
& $CFD tunnel route dns jarvis-desktop api.<your-domain>.com
```

Test it without a service first:

```powershell
& $CFD tunnel run jarvis-desktop
```

Open a second PowerShell window on the desktop and curl one of the
hostnames — if you get a 502, the tunnel is reachable but no local
listener answered (expected for the SSH route until Section A is
complete). Ctrl+C the tunnel run when satisfied.

---

## Section E — Install cloudflared as a Windows service **[admin]**

In an **admin** PowerShell on the desktop:

```powershell
$CFD = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
& $CFD service install
Start-Service cloudflared
Set-Service cloudflared -StartupType Automatic
Get-Service cloudflared | Format-List Name, Status, StartType
```

Once registered, the tunnel survives reboots and runs without an
interactive console. Confirm with the helper:

```powershell
C:\jarvis\scripts\remote\check_remote_status.ps1
```

---

## Section F — Generate an SSH key on the laptop

On the **laptop**, regular (non-admin) PowerShell:

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\jarvis_desktop -C "daniil-laptop"
```

Press Enter twice for no passphrase, or set one. Then print the public
key with the helper:

```powershell
C:\jarvis\scripts\remote\print_pubkey.ps1
```

Copy the printed `ssh-ed25519 ... daniil-laptop` line.

On the **desktop**, append it to `authorized_keys`:

```powershell
$auth = "$env:USERPROFILE\.ssh\authorized_keys"
if (-not (Test-Path "$env:USERPROFILE\.ssh")) {
    New-Item -ItemType Directory -Path "$env:USERPROFILE\.ssh" -Force | Out-Null
}
Add-Content -Path $auth -Value "<paste the ssh-ed25519 line here>"
```

### If your desktop user is an administrator

Windows SSH treats admins specially. Also append the key to:

```powershell
$adminAuth = "C:\ProgramData\ssh\administrators_authorized_keys"
Add-Content -Path $adminAuth -Value "<same line>"

# Lock the ACL — only Administrators + SYSTEM may read
icacls $adminAuth /inheritance:r
icacls $adminAuth /grant "Administrators:F" "SYSTEM:F"
```

`Admin` (your desktop user) **is** an admin, so do this step.

---

## Section G — SSH client config on the laptop

Add to `$env:USERPROFILE\.ssh\config` (create the file if missing):

```sshconfig
Host jarvis-desktop
    HostName ssh.<your-domain>.com
    User Admin
    IdentityFile ~/.ssh/jarvis_desktop
    ProxyCommand "C:\Program Files (x86)\cloudflared\cloudflared.exe" access ssh --hostname %h
```

If cloudflared lives in a different place on the laptop, adjust the
`ProxyCommand` path. The `cloudflared access ssh` wrapper handles the
HTTPS-tunnel-to-stdio plumbing so plain SSH "just works".

Test from the laptop:

```powershell
ssh jarvis-desktop "whoami; hostname"
```

You should see `pc-loe\admin` and `PC-LOE`.

---

## Section H — VSCode Remote-SSH on the laptop

1. Install VSCode (or VS Code Insiders) on the laptop.
2. Install the **Remote - SSH** extension (publisher: Microsoft).
3. Optional: install **Remote - SSH: Editing Configuration Files** so
   VSCode can edit your `~/.ssh/config`.
4. `Ctrl+Shift+P` → `Remote-SSH: Connect to Host…` → pick
   `jarvis-desktop`.
5. After the remote VS Code Server installs (one-time, ~30 s), open
   `File → Open Folder…` → `C:\jarvis`.

The terminal in VSCode then runs on the desktop, the editor talks to
remote files, and Python interpreter selection lets you pick
`C:\jarvis\.venv\Scripts\python.exe`.

---

## Section I — End-to-end verification

From the laptop:

```powershell
ssh jarvis-desktop "Get-Location; whoami; hostname"
```

Expected:

```
Path
----
C:\Users\Admin
pc-loe\admin
PC-LOE
```

If you get there, you're done. If not, see the troubleshooting block.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ssh: connect to host ssh.example.com port 22: Connection refused` | Tunnel offline, or sshd not running on desktop | Run helper `check_remote_status.ps1` on desktop |
| `Permission denied (publickey)` | Public key not in `authorized_keys` *or* `administrators_authorized_keys` | Re-check Section F; for admin users **both** files |
| `cloudflared` says "no such tunnel" | Wrong UUID in `config.yml`, or different Cloudflare account logged in | `cloudflared tunnel list` to confirm UUID |
| VSCode hangs on "Setting up SSH host" | Server install failed for the remote VS Code Server — usually missing OpenSSH SFTP | Reinstall OpenSSH Client+Server on desktop |
| Tunnel works from outside but not from same LAN | DNS leak — Cloudflare returns a routable but loopbacked address | Use the FQDN, not LAN IP |
| `cloudflared service install` says access denied | Not running as admin | Re-open PowerShell as admin |

## Quick-reference: what runs where

```
laptop                                                  desktop (PC-LOE)
------                                                  ---------------
ssh jarvis-desktop                                      sshd:22  (Section A)
       │                                                  ▲
       ▼                                                  │
cloudflared access ssh                                  cloudflared service
       │           (Section G)                            │ (Sections D, E)
       └─── HTTPS ─── Cloudflare edge ─── HTTPS ──────────┘
```

Section A makes the right edge listen on `:22`. Section D + E make the
left edge stream into that listener through Cloudflare.
