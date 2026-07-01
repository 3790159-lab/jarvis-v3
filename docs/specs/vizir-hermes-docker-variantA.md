# Spec — knee #2 · Variant A: Hermes executes inside Docker, driven from WSL Ubuntu

**Status:** DRAFT, awaiting Daniil's OK (spec→OK→TDD).
**Worktree:** `C:\jarvis_worktrees\vizir-hermes-docker` @ `eb341fd` (from prod `e9288c2`, not merged).
**Supersedes** the tcp/DOCKER_HOST bridge of the mock phase (that was effectively "Variant B").

## Goal
Hermes runs its `terminal` / `code_execution` **inside an ephemeral Docker container**, with
**PROVEN isolation** (never on the Windows host), under Vizir's cost-cap. Close the knee #2
live run that failed isolation in live run #1.

## Why Variant A (chosen 2026-07-01)
Live run #1 root cause (systematic-debug, $0 probes): Hermes ran on the Windows HOST, not in a
container. Triple cause: (1) dockerd not durable (WSL idle → tcp refused); (2) Windows-host-path
bind mounts (skills/cred/cache) invalid on the Linux daemon → `docker run` exit 125, because
`DOCKER_HOST=tcp` to a bare Linux dockerd loses the Win→/mnt/c path translation Docker Desktop
would have done; (3) 🚨 when the docker backend is unavailable Hermes SILENTLY falls back to the
host (no error, status=done).

Variant A puts the **client + daemon + paths + mounts all on Linux** (Hermes runs inside WSL
Ubuntu, docker over the unix socket). This eliminates the whole Win↔Linux class:
- No tcp, no path translation → cause (2) gone (mounts are native Linux paths, valid on the daemon).
- The child process runs **inside WSL for the whole run**, so the VM can't idle-sleep mid-run → cause (1) largely gone; the guard also re-confirms dockerd immediately before spawn.
- Cause (3) is closed by an explicit **isolation-safety guard** (below), independent of A/B/C.

## Architecture
```
Windows host  (bot / Vizir / Coordinator — UNTOUCHED, prod ledger isolated)
  └─ Vizir Hermes StepHandler (parent, Windows python)
       └─ spawn:  wsl -d Ubuntu -u root  python3  /opt/vizir/hermes_child.py
            └─ Hermes AIAgent  (Linux venv, provider=anthropic)   ← LLM $ spent here
                 └─ terminal/code_execution → DockerEnvironment
                      └─ unix:///var/run/docker.sock (native)
                           └─ ephemeral python:3.11-slim container   ← code runs HERE only
```
Parent still enforces the cost-cap: child streams cumulative cost per iteration on stdout →
`report_cost(delta)` (RAISES `StepBudgetExceeded` at the cap) → parent kills the child. Proven
mechanism, unchanged; only the spawn transport (Windows-python → `wsl … python3`) changes.

## Changes (all in worktree, TDD, additive — knee #1 untouched)
1. **Install Hermes in Ubuntu** — Linux venv, pin commit `1b376855` (same as Windows), isolated
   from `C:\jarvis`. `hermes doctor` clean. provider=anthropic. `$0` install (LLM spend only at live run).
2. **Adapter** (`handlers_hermes.py` / `hermes_child.py`):
   - `_hermes_paths()` gains a **WSL/Linux mode** returning the `wsl.exe` argv + Linux child path.
   - `_default_run` / `_stream_subprocess` spawn via `wsl -d Ubuntu -u root python3 <linux child>`.
   - **env_overlay for A:** `TERMINAL_ENV=docker`, `TERMINAL_DOCKER_IMAGE=python:3.11-slim`,
     `TERMINAL_CONTAINER_MEMORY=1024`, `persist_across_processes=false`. **Dropped vs mock phase:**
     no `DOCKER_HOST` (unix socket default), no `HERMES_DOCKER_BINARY` (docker on Linux PATH).
   - `ANTHROPIC_API_KEY` must reach the WSL child (WSL does not inherit Windows env) — passed
     explicitly via the stdin cfg / `WSLENV`, validated in Phase 0.
3. **Isolation-safety guard** (the teeth — isolation analog of the cost-cap teeth):
   - **Pre-flight:** ACTUALLY run a throwaway container (`docker run --rm python:3.11-slim …`)
     and assert (a) rc=0, (b) container hostname ≠ host hostname, (c) a marker file created
     inside does NOT appear on the host. Not just `docker version`.
   - If pre-flight fails → **BLOCK the step (raise), NEVER spawn Hermes** → Hermes can never reach
     the silent host-fallback. (Closes cause (3) regardless of bridge choice.)
   - **Post-run:** assert no host artifact leaked (no `C:\workspace\*`, expected output only in the
     container/collected artifact) before acceptance passes.
4. **Durable dockerd:** confirmed active immediately before spawn by the guard; the running child
   keeps WSL busy. (Keepalive/`docker rm -f`-on-breach hardening = existing techdebt, later.)

## Money teeth (contract unchanged — docs/specs/vizir-arc.md)
cost-cap via `report_cost` (reserve-before-spend), parent kills child on breach (proven live,
$0.30 crash-test). Killing the child does not instantly kill the in-container process — covered by
container mem/pids cap + Hermes per-command timeout + orphan-reaper; hard `docker rm -f` on breach
is documented techdebt.

## Phase 0 — $0 de-risking spikes (BEFORE any paid run)
- **S1** Install Hermes in Ubuntu; `hermes doctor` clean. Resolve python 3.11 vs Ubuntu's 3.14
  (pip currently MISSING; node absent) — pin 3.11 if deps require it.
- **S2** `wsl.exe` stdio round-trip clean: raw UTF-8, no BOM/CRLF corruption, with a **fake child**
  (the PowerShell-pipe BOM seen in recon must NOT appear via `create_subprocess_exec`).
- **S3** env→config mapping verified: `TERMINAL_ENV=docker` actually selects `DockerEnvironment`;
  `persist=false`; image/memory honored.
- **S4** cgroup limits probe passes on this WSL2 (`--memory`/`--pids-limit` effective) — else
  document degraded mode (important on the 8 GB host).
- **S5** throwaway-container isolation probe works: container hostname differs, host file NOT
  created, `/mnt/c` absent or non-writable from the container.
- **S6** locate the silent host-fallback site (terminal_tool selecting Local on Docker init
  failure); if feasible, make it hard-fail instead of falling to Local (defence-in-depth on top of the guard).

## TDD plan (after Phase 0, spy-teeth first)
- Guard: docker-unavailable → **BLOCK**, prove it NEVER spawns Hermes / never touches host (mutate → red).
- env_overlay reaches the WSL child (live subprocess, $0 fake).
- cost-cap still kills the child in WSL-spawn mode (mutate report_cost → overspend caught → red).
- knee #1 (docker_exec=False) regression: unchanged call shape, all existing hermes/vizir green.

## Live run (under cap, with Daniil's explicit OK — real Anthropic cents)
primes-in-container, hard cap $0.30, prod ledger isolated (charge_logger=None). Then PROVE isolation:
`docker inspect` mounts have no host paths / container hostname / no host file / `C:\jarvis` git clean.
On green → merge worktree into `phase-4.0-unified-jarvis`. THEN terminal/loop/services.
