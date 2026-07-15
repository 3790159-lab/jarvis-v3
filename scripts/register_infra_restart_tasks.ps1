# Registers the JarvisInfraRestartCloudflared one-shot scheduled task used by
# /infra_restart's [cloudflared] button (DEV-12). RunLevel Highest so
# `schtasks /Run /TN JarvisInfraRestartCloudflared` from the bot process gets
# Task Scheduler's OWN elevation for the Start-Service/Stop-Process it needs,
# regardless of whether the bot process itself happens to be elevated --
# mirrors register_bot_guardian.ps1's S4U/Highest pattern, but with NO
# triggers registered (on-demand /Run only, never runs on its own).
#
# Registration does NOT run the action. Run this ONCE, elevated, after
# merging DEV-12 (this is a real, host-wide Task Scheduler change -- run it
# yourself, not from an automated worktree session):
#   powershell -File scripts\register_infra_restart_tasks.ps1

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisInfraRestartCloudflared'
$Script   = 'C:\jarvis\scripts\infra_restart_cloudflared_action.ps1'

$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer
# name so the SID maps (mirrors register_bot_guardian.ps1 / register_backend_guardian.ps1).
$Account = "$env:COMPUTERNAME\$env:USERNAME"

$Principal = New-ScheduledTaskPrincipal -UserId $Account -LogonType S4U -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $TaskName -Action $Action `
    -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, on-demand /Run only, no triggers)"
