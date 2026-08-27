# Registers (or re-registers) the JarvisPanelClientGuardian scheduled task.
# Mirrors register_backend_guardian.ps1: S4U + RunLevel Highest so it runs
# whether the user is logged on or not (session-independent),
# MultipleInstances=IgnoreNew so the task itself never double-starts. Triggers:
# at system startup AND at logon, so the client panel comes back after a reboot
# -- the exact hole that killed it twice (20.08: the panel died with the
# interactive session that started it, no traceback, port 8011 free).
#
# ONE TASK, ONE CLIENT. The SLUG is baked into the task arguments on purpose:
# an unregistered task for a "future" client looks like supervision without
# being any. A second client gets its own task, registered the same way.
#
# THE PORT IS NOT REPEATED HERE. It used to be a fourth copy of 8011 (after
# run_panel_client.DEFAULT_PORT, the guardian's -Port and ops_watchdog's
# PANEL_CLIENT_PORT), and a fourth copy is a fourth chance to end up with
# "started on 8011, judged on 8012" -- the smaller number silencing the larger
# one without a word. The task calls the guardian WITHOUT -Port, so the value
# is declared once, in the guardian's own parameter. Pass -Port to this script
# only to override it deliberately for one registration.
#
# Registration does NOT start the task -- run
# `schtasks /Run /TN JarvisPanelClientGuardian` (or reboot) to launch it.

param(
    [string]$Slug = 'yarina',
    # 0 means "do not pass -Port at all" -- the guardian's own default rules.
    # This is what keeps the port declared in exactly one place per side.
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
# ИМЯ ЗАДАЧИ ЕДЕТ ЗА СЛАГОМ. Пока оно было константой, вторая регистрация
# с другим -Slug ПЕРЕТИРАЛА задачу первого клиента (Register-ScheduledTask
# -Force), и живая панель оставалась без присмотра -- при полностью зелёном
# выводе этого самого скрипта. Комментарий выше обещал 'a second client
# gets its own task', а имя обещание не держало.
#
# Базовое имя закреплено за ИСТОРИЧЕСКИМ слагом и переименованию не
# подлежит: эта задача зарегистрирована в системе и держит живую панель,
# а §9 спеки живое не трогает. Остальные слаги получают суффикс.
#
# Регистр меняется InvariantCulture, а не ToTitleCase: у турецкой локали
# ToUpper('i') даёт 'I' с точкой, и имя задачи разошлось бы с ожиданием
# на машине с другой культурой.
$BaseTaskName   = 'JarvisPanelClientGuardian'
$HistoricalSlug = 'yarina'
if ($Slug -eq $HistoricalSlug) {
    $TaskName = $BaseTaskName
} else {
    $TaskName = $BaseTaskName + $Slug.Substring(0,1).ToUpperInvariant() + $Slug.Substring(1)
}
$Root     = 'C:\jarvis'
$Script   = Join-Path (Join-Path $Root 'scripts') 'panel_client_guardian_detached.ps1'

$psArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Slug {1}' -f $Script, $Slug
if ($Port -gt 0) { $psArgs = '{0} -Port {1}' -f $psArgs, $Port }
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory $Root

# Workgroup (non-domain) machine: qualify the local account with the computer
# name so the SID maps (USERDOMAIN would wrongly be "WORKGROUP" here).
$Account = "$env:COMPUTERNAME\$env:USERNAME"

$Triggers = @(
    New-ScheduledTaskTrigger -AtStartup
    New-ScheduledTaskTrigger -AtLogOn -User $Account
)

# S4U: run whether logged on or not, without storing a password.
$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest

# ExecutionTimeLimit = 0 (TimeSpan::Zero): the guardian is an infinite loop, and
# any finite limit would have the scheduler kill the supervisor itself.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers `
    -Principal $Principal -Settings $Settings -Force | Out-Null

$portNote = if ($Port -gt 0) { "port=$Port (explicit override)" } else { 'port: guardian default' }
Write-Host "[OK] Registered scheduled task '$TaskName' (S4U / Highest, AtStartup + AtLogOn), slug=$Slug, $portNote"
Write-Host "     Not started. Launch with: schtasks /Run /TN $TaskName"
