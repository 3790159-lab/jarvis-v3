# Jarvis Day 4 Handoff

Created May 23 2026.

CONTEXT: Daniil + Claude continuing Jarvis V3.
Path C:\jarvis. SSH via jarvis-desktop.

DAY 3 PROVEN IN PRODUCTION:
- Bug 45 explicit pod override
- Bug 49 bootstrap probe-before-gate (pods ready in 150s)
- KEEP_POD_RUNNING flag
- Face swap E2E 7 swaps via Telegram

DAY 3 FAILED (Day 4 todos):
- Bug 44 uuid uploads NOT working despite shipped code. Outputs 4 unique + 3 duplicate pairs. Logs showed old format. Hypothesis stale pyc cache.
- Bug 48 podExec API deprecated. Error Unknown type PodExecInput. Files face_swap_engine.py + runpod_comfy_engine.py.
- Animation pipeline SUPPLY_CONSTRAINT immediate fail. Need sniper-like retry loop.

CURRENT STATE:
- All Python killed
- All pods stopped no billing
- Branch phase-3.0-inventory-stop-reliability
- HEAD 0b1a8e0 pushed

TEMPLATE PATCH (Day 3 critical workaround):
Template jpzymkuab8 has custom startCmd that wgets patched bootstrap.sh from GitHub before exec.
Network volume tfo7deuhs7 already has patched bootstrap.sh v003.

FIRST STEPS NEXT CLAUDE:
1. Verify git log shows HEAD 0b1a8e0 on origin/phase-3.0-inventory-stop-reliability
2. Clean pycache: Get-ChildItem -Path C:\jarvis -Filter __pycache__ -Recurse then Remove-Item -Recurse -Force
3. Diagnostic for bug 44: add 2 debug log lines, single test, check if uuid in logs
4. Daniil prefers tag-team for 44 investigation, Claude Code brief for 48 mechanical fix

GOTCHAS:
- PowerShell is not bash
- SSH key C:\Users\Admin\.ssh\id_ed25519 registered in RunPod
- Direct TCP SSH works better than ssh.runpod.io proxy
- Sniper script scripts\runpod_gpu_sniper.py
- Pod billing 1.89 per hour, stop manually

Daniil works as napartnik peer collaborator, RU EN mix OK.
