# Registers (or re-registers) the JarvisRestoreDrill scheduled task (DEV-46 sec.4.3).
#
# The drill downloads yesterday's client backup, decrypts it, opens the snapshot
# with the REAL storage layer and compares the numbers against the manifest.
# Weekly, automatically, result into the watchdog -- the owner's answer 3.
#
# WHERE THIS TASK BELONGS. The drill needs the PRIVATE key, and the host does
# not have it by construction (sec.3.3 variant B: the host writes backups and
# cannot read them). So this task lives on the OWNER'S machine. Today the host
# and the owner's machine are the same box; after the cloud split it moves to
# the laptop and the rented VM keeps only the public key. Registering it on the
# rented host later would break the very property variant B was chosen for.
#
# ORDER MATTERS (owner's rule, and it is DEV-47 in miniature):
#   1. register THIS task first;
#   2. only then put JARVIS_BACKUP_PUBLIC_KEY into the host .env.
# The watchdog probe registers itself the moment that variable appears. Put the
# key in first and the lamp goes red with "drill_never" -- a lamp that is red
# while everything is fine trains the owner to stop reading it.
#
# Sunday 05:00 local: one hour after JarvisStateBackup (04:00), so the drill
# always works on the freshest set rather than on yesterday's.
#
# -X utf8 forces UTF-8 for the child process: the drill's summary carries
# Cyrillic and the scheduled-task console defaults to cp1251 here.
#
# ASCII-only on purpose. Repo contract (commit 9f5982cd): any .ps1 with
# non-ASCII bytes MUST carry a UTF-8 BOM, else PS 5.1 reads it as windows-1251
# and dies in the parser. Add Russian text here only together with a BOM.
#
# Registration deliberately does NOT run the drill -- a live R2 read is a
# separate, deliberate step:
#   schtasks /Run /TN JarvisRestoreDrill
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PrivateKey
)
$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisRestoreDrill'
$Python   = 'C:\jarvis\.venv\Scripts\python.exe'
$Script   = 'C:\jarvis\scripts\restore_drill.py'
$RepoRoot = 'C:\jarvis'

if (-not (Test-Path $Python)) { throw "python not found: $Python" }
if (-not (Test-Path $Script)) { throw "drill script not found: $Script" }

# ---- fail closed on the key -----------------------------------------------
# A task that can never work must not be created. A registered task whose key
# path is wrong fails every week into rc 2, writes no verdict, and the probe
# reports "not run" -- true, but the cause would be buried in Task Scheduler
# history instead of being said out loud here, once.
$KeyResolved = $null
try { $KeyResolved = (Resolve-Path -LiteralPath $PrivateKey -ErrorAction Stop).Path }
catch { throw "private key not found: $PrivateKey -- create the pair first: python scripts\backup_keygen.py --private-out <path outside this tree>" }

# The key must not live inside the repo tree: the tree is what gets copied,
# pushed and backed up. Compare RESOLVED paths, not strings -- '..\jarvis\...'
# is inside.
$RepoResolved = (Resolve-Path -LiteralPath $RepoRoot).Path
if ($KeyResolved.StartsWith($RepoResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "private key sits INSIDE the repo tree ($KeyResolved). Move it out: the tree is copied, pushed and backed up."
}

$psArgs = '-X utf8 "{0}" --private-key "{1}"' -f $Script, $KeyResolved
$Action = New-ScheduledTaskAction -Execute $Python -Argument $psArgs -WorkingDirectory $RepoRoot

# Workgroup (non-domain) machine: qualify the local account with the computer name.
$Account = "$env:COMPUTERNAME\$env:USERNAME"

$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 5:00am

$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# StartWhenAvailable: a run missed while the laptop was off is caught up --
# that is the whole point of a weekly rhythm with a 10-day probe threshold.
# One instance at a time; 60 min bounds a hung download without cutting a real
# one (the client set is ~0.5 MB today, so a real run is seconds).
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 60)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

# ---- proof by fact --------------------------------------------------------
# "Register-ScheduledTask returned" proves nothing: the 2026-07-25 guardian
# deploy returned success while the Scheduler silently kept the old definition.
# Read the LIVE task back and throw on anything that does not match the spec.
$reg = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$problems = @()

if ($reg.Principal.LogonType -ne 'S4U')     { $problems += "LogonType=$($reg.Principal.LogonType), expected S4U" }
if ($reg.Principal.RunLevel  -ne 'Highest') { $problems += "RunLevel=$($reg.Principal.RunLevel), expected Highest" }
if (-not $reg.Settings.StartWhenAvailable)  { $problems += "StartWhenAvailable is off" }

$trig = @($reg.Triggers)[0]
if ($trig.CimClass.CimClassName -ne 'MSFT_TaskWeeklyTrigger') {
    $problems += "trigger is $($trig.CimClass.CimClassName), expected MSFT_TaskWeeklyTrigger"
}
if ($trig.StartBoundary -notmatch 'T05:00:00') {
    $problems += "trigger time is $($trig.StartBoundary), expected 05:00 local"
}

$act = @($reg.Actions)[0]
if ($act.Execute -ne $Python)            { $problems += "Execute=$($act.Execute), expected $Python" }
if ($act.Arguments -notlike "*restore_drill.py*") { $problems += "Arguments do not run restore_drill.py: $($act.Arguments)" }
if ($act.Arguments -notlike "*--private-key*")    { $problems += "Arguments carry no --private-key: $($act.Arguments)" }

if ($problems.Count -gt 0) {
    throw ("JarvisRestoreDrill registered but the LIVE definition disagrees:`n  " + ($problems -join "`n  "))
}

Write-Host "JarvisRestoreDrill registered and verified by reading the live task back."
Write-Host "  runs: Sunday 05:00, one hour after JarvisStateBackup"
Write-Host "  key:  $KeyResolved"
Write-Host ""
Write-Host "The drill was NOT run. A live R2 read is a separate, deliberate step:"
Write-Host "  schtasks /Run /TN JarvisRestoreDrill"
Write-Host ""
Write-Host "ONLY AFTER a green run put JARVIS_BACKUP_PUBLIC_KEY into the host .env."
Write-Host "Doing it the other way round lights a red lamp that nobody can fix yet."
