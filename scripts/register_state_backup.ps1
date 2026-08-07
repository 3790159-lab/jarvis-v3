# Registers (or re-registers) the JarvisStateBackup scheduled task (DEV-16, B2).
# Mirrors register_ig_token_refresh.ps1: S4U + RunLevel Highest so it runs
# whether the user is logged on or not (session-independent), StartWhenAvailable
# so a run missed while the PC was off is caught up.
#
# Daily at 04:00 local (= 01:00 UTC on this UTC+3 box). Deliberately far from
# the UTC midnight boundary: backup keys are BACKUP_PREFIX/<UTC date>/..., so a
# run near 00:00 UTC would scatter consecutive nights across two date folders
# and confuse rotation. (Was 03:30 before the 2026-08-08 live deploy.)
#
# -X utf8 forces UTF-8 for the child process: the run's summary carries emoji
# and the scheduled-task console defaults to cp1251 here.
#
# ASCII-only on purpose. The repo contract (commit 9f5982cd) is: any .ps1 with
# non-ASCII bytes MUST carry a UTF-8 BOM, else PS 5.1 reads it as windows-1251
# and dies in the parser. Add Russian text here only together with a BOM.
#
# Registration deliberately does NOT run the backup -- a live R2 run is a
# separate, deliberate step (owner's boundary in B2):
#   schtasks /Run /TN JarvisStateBackup
$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisStateBackup'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\state_backup.py'

if (-not (Test-Path $Python)) { throw "python not found: $Python" }
if (-not (Test-Path $Script)) { throw "backup script not found: $Script" }

$psArgs = '-X utf8 "{0}"' -f $Script
$Action = New-ScheduledTaskAction -Execute $Python -Argument $psArgs -WorkingDirectory 'C:\jarvis'

# Workgroup (non-domain) machine: qualify the local account with the computer name.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

$Trigger = New-ScheduledTaskTrigger -Daily -At 4:00am

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# Run a missed schedule as soon as possible; retry a few times on a transient
# R2 hiccup; a single run must not overlap another. 101 files took ~62s in the
# live acceptance run -- 30 min bounds a hung upload without cutting a real one.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

# ---- proof by fact -------------------------------------------------------
# "Register-ScheduledTask returned" proves nothing: the 2026-07-25 guardian
# deploy returned success while the Scheduler silently kept the old definition.
# Read the LIVE task back and throw on anything that does not match the spec.
$reg = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$problems = @()

if ($reg.Principal.LogonType -ne 'S4U')     { $problems += "LogonType=$($reg.Principal.LogonType), expected S4U" }
if ($reg.Principal.RunLevel  -ne 'Highest') { $problems += "RunLevel=$($reg.Principal.RunLevel), expected Highest" }
if (-not $reg.Principal.UserId)             { $problems += 'principal has no UserId' }

if ($reg.Actions[0].Execute   -ne $Python) { $problems += "Execute=$($reg.Actions[0].Execute), expected $Python" }
if ($reg.Actions[0].Arguments -ne $psArgs) { $problems += "Arguments=$($reg.Actions[0].Arguments), expected $psArgs" }
if ($reg.Actions[0].WorkingDirectory -ne 'C:\jarvis') { $problems += "WorkingDirectory=$($reg.Actions[0].WorkingDirectory)" }

if (-not $reg.Settings.StartWhenAvailable) { $problems += 'StartWhenAvailable is false -- a run missed while off would vanish' }
if ($reg.Settings.MultipleInstances -ne 'IgnoreNew') { $problems += "MultipleInstances=$($reg.Settings.MultipleInstances), expected IgnoreNew" }

# A task with no trigger looks registered and never runs.
if ($reg.Triggers.Count -lt 1) {
    $problems += 'no triggers on the live task -- it would never run'
} else {
    $trg = $reg.Triggers[0]
    if ($trg.CimClass.CimClassName -ne 'MSFT_TaskDailyTrigger') {
        $problems += "trigger type $($trg.CimClass.CimClassName), expected MSFT_TaskDailyTrigger"
    }
    $start = [datetime]$trg.StartBoundary
    if ($start.Hour -ne 4 -or $start.Minute -ne 0) {
        $problems += "trigger at $($start.ToString('HH:mm')), expected 04:00 local"
    }
}

if ($problems.Count -gt 0) {
    throw ("'$TaskName' was registered but the LIVE definition does not match the spec:`n  - " +
           ($problems -join "`n  - "))
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName
$start = [datetime]$reg.Triggers[0].StartBoundary
Write-Host "[OK] Registered and verified by read-back: '$TaskName'"
Write-Host "     state    : $($reg.State)"
Write-Host "     principal: $($reg.Principal.UserId) / $($reg.Principal.LogonType) / $($reg.Principal.RunLevel)"
Write-Host "     action   : $($reg.Actions[0].Execute) $($reg.Actions[0].Arguments)"
Write-Host "     trigger  : daily $($start.ToString('yyyy-MM-dd HH:mm K'))"
Write-Host "     next run : $($info.NextRunTime)"
