# TEMP: регистрация boot-probe приёмки P1/P2 §8.1 (задача 4 слоя процесса).
# Таск JarvisSecretsBootProbe_TEMP: AtStartup / S4U / Highest — тот же принципал
# и триггер, что у боевых гардианов, поэтому прогон доказывает ровно то, что
# нужно приёмке: AtStartup-процесс под членом Administrators расшифровал
# machine-scope+entropy блобы (entropy за ACL) без участия владельца.
#
# Probe работает ТОЛЬКО с одноразовыми фикстурами в $Root\state\secrets_probe —
# живые секреты не трогаются. После зелёного ребут-теста таск удалить:
#   .\register_secrets_boot_probe_TEMP.ps1 -Unregister
# (прецедент: DpapiBootProbe_TEMP, удалён после тестов №1-2, спека §13).

param(
    [string]$Root = 'C:\jarvis_worktrees\sprint0-secrets-p1p2',
    [string]$Py   = 'C:\jarvis\.venv\Scripts\python.exe',
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
$TaskName = 'JarvisSecretsBootProbe_TEMP'

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[OK] Unregistered '$TaskName' (probe-каталог state\secrets_probe можно удалить руками)"
    return
}

$ProbeScript = Join-Path $Root 'scripts\secrets_boot_probe.py'
if (-not (Test-Path $ProbeScript)) { throw "нет $ProbeScript" }

# Фикстуры создаются здесь же, из живой сессии (setup требует интерактива
# ровно ноль, но делать это заранее = меньше движущихся частей на буте).
& $Py $ProbeScript --setup
if ($LASTEXITCODE -ne 0) { throw "setup фикстур probe упал ($LASTEXITCODE)" }

$Action = New-ScheduledTaskAction -Execute $Py `
    -Argument ('"{0}"' -f $ProbeScript) -WorkingDirectory $Root

# Workgroup-машина: локальный аккаунт квалифицируется именем компьютера.
$Account = "$env:COMPUTERNAME\$env:USERNAME"
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId $Account `
    -LogonType S4U -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries     = $false

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host "[OK] Registered '$TaskName' (S4U / Highest, AtStartup, TEMP)"
Write-Host "     Смок из сессии:  schtasks /Run /TN $TaskName"
Write-Host "     Вердикт:         $Root\state\secrets_probe\probe_result.log"
Write-Host "     После зелёного ребут-теста: -Unregister"
