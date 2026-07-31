<#
.SYNOPSIS
  Dead-man пінг у healthchecks.io — зовнішній сторож ферми.

.DESCRIPTION
  ЛОГІКА DEAD-MAN, а не «сповістити про помилку»: скрипт пінгує ТІЛЬКИ КОЛИ ВСЕ
  ЖИВЕ. Якщо машина померла, заснула, втратила мережу або сам скрипт зламався —
  пінга не буде, і healthchecks.io здійме тривогу САМ, ззовні. Саме тому сторож
  зовнішній: він ловить те, чого внутрішній моніторинг за визначенням не бачить
  (docs/jarvis-panel/PHASE0_READONLY.md §0).

  Перевіряє: свіжість heartbeat-файлів і наявність процесів ферми.
  Здорово   -> GET <URL>            (+ пише state/healthchecks_last.txt)
  Хворо     -> GET <URL>/fail       (тіло = що саме не так)
  Немає URL -> нічого не робить, код 0 (сторож просто не налаштований)

.NOTES
  Read-only: нічого не перезапускає і не вбиває. Це сигналізація, не гардіан.
#>
[CmdletBinding()]
param(
  [string]$Url = $env:HEALTHCHECKS_URL,
  [string]$Root = $(if ($env:JARVIS_ROOT) { $env:JARVIS_ROOT } else { 'C:\jarvis' }),
  [int]$FreshSeconds = 180,
  [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Url)) {
  Write-Output 'HEALTHCHECKS_URL не заданий — сторож не налаштований, вихід 0.'
  exit 0
}

$problems = @()

# 1) heartbeat-файли. ВІК беремо з LastWriteTime, а не з розміру: на NTFS розмір
#    живого лога бреше (0 байт при відкритому write-хендлі).
$beats = @{
  'chatter раннер'   = 'chatter_heartbeat.txt'
  'головний бот'     = 'bot_heartbeat.txt'
  'chatter гардіан'  = 'chatter_guardian_heartbeat.txt'
  'ops_watchdog'     = 'ops_watchdog_heartbeat.txt'
}
foreach ($name in $beats.Keys) {
  $p = Join-Path $Root "state\$($beats[$name])"
  if (-not (Test-Path $p)) { $problems += "$name : heartbeat відсутній"; continue }
  $age = ((Get-Date) - (Get-Item $p).LastWriteTime).TotalSeconds
  if ($age -gt $FreshSeconds) {
    $problems += ("{0} : heartbeat {1:N0} с тому" -f $name, $age)
  }
}

# 2) процеси. Фільтр по Name='python.exe' ОБОВ'ЯЗКОВИЙ: перевірка лише за
#    CommandLine матчить сам процес перевірки (рядок потрапляє в його командний
#    рядок) і дає фальшиве «живий» на мертвому раннері.
$selfIds = @($PID)
$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
         Where-Object { $_.Name -eq 'python.exe' -and $_.ProcessId -notin $selfIds }
$expect = @{
  'chatter раннер' = 'chatter.telethon_run'
  'backend'        = 'run_backend_detached'
  'головний бот'   = 'jarvis_smart_telegram_control'
}
foreach ($name in $expect.Keys) {
  $hit = $procs | Where-Object { $_.CommandLine -like "*$($expect[$name])*" }
  if (-not $hit) { $problems += "$name : процес не знайдено" }
}

$healthy = ($problems.Count -eq 0)
$body = if ($healthy) { 'ok' } else { ($problems -join '; ') }

if ($WhatIfOnly) {
  Write-Output ("healthy={0}`n{1}" -f $healthy, $body)
  exit 0
}

$target = if ($healthy) { $Url } else { ($Url.TrimEnd('/') + '/fail') }
try {
  Invoke-RestMethod -Uri $target -Method Post -Body $body -TimeoutSec 20 | Out-Null
  if ($healthy) {
    # Позначку читає панель Джарвіса, щоб показати вік останнього пінга.
    # ⚠️ НЕ `Get-Date -UFormat %s`: у PowerShell 5.1 воно рахує епоху від
    # ЛОКАЛЬНОГО часу, тому в Києві (UTC+3) давало мітку на 10800 с у
    # МАЙБУТНЄ, і будь-який споживач діставав ВІД'ЄМНИЙ вік. Спіймано живцем
    # 2026-07-31: записало 06:03:52 при реальних 03:03:51.
    # ToUnixTimeSeconds() рахує від UTC за визначенням і зсуву не має.
    $stamp = Join-Path $Root 'state\healthchecks_last.txt'
    Set-Content -Path $stamp -Value ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -Encoding utf8
  }
  Write-Output ("пінг {0}: {1}" -f $(if ($healthy) { 'ok' } else { 'FAIL' }), $body)
} catch {
  # Мережа лягла — це САМЕ той випадок, заради якого сторож зовнішній:
  # пінга не буде, і healthchecks.io здійме тривогу сам.
  Write-Output "пінг не пройшов: $($_.Exception.Message)"
  exit 1
}
