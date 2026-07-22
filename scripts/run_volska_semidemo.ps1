# Напів-демо: ганяє персону `volska` (Ольга) на ІСНУЮЧІЙ сесії акаунта Ані.
# Логін не потрібен і НЕ виконується.
#
# НЕ чіпає прод-склад: chatter/clients/active.yaml лишається [demo, demo2].
# Склад задається лише через CHATTER_PERSONAS нижче. Відкат — див. -Revert.
#
# ЧОМУ ПІНИ СЕСІЇ ОБОВ'ЯЗКОВІ (найлегше забути):
#   derive_session_path/derive_db_path (telethon_run.py:79-99) будують шлях
#   з ПЕРШОГО слага: CHATTER_PERSONAS=volska сам по собі повів би раннер на
#   неіснуючий .secrets\volska.session → Telethon попросив би код підтвердження.
#   Пін TELETHON_SESSION/CHATTER_DB тримає його на живих demo-файлах.
#
# ГАРДІАН ТЕПЕР ВОЛОДІЄ І ОЛЬГОЮ (2026-07-22):
#   Раннер volska більше НЕ запускається вручну звідси. Скрипт ставить прапор
#   state\chatter_semidemo_volska.flag і вмикає JarvisChatterGuardian — той
#   бачить прапор (блок SEMIDEMO OVERRIDE у chatter_guardian_detached.ps1),
#   підставляє пін-и volska і сам піднімає/воскрешає раннер. Причина зміни:
#   ручний Start-Process двічі тихо вмирав разом із сесією, що його породила
#   (дрил 2026-07-22 13:55 — раннер зник, «Встигнете до субботи?» втрачено).
#   Аня і Ольга досі взаємовиключні — акаунт один: прапор Є = Ольга, НЕМАЄ = Аня.
param(
    [switch]$Revert    # повернути прод: Аня (demo,demo2) під гардіаном
)

$ErrorActionPreference = 'Stop'
$Root = 'C:\jarvis'
Set-Location $Root

function Stop-Chatter {
    Write-Host '-> зупиняю гардіан і будь-який живий раннер'
    try { Disable-ScheduledTask -TaskName 'JarvisChatterGuardian' -ErrorAction Stop | Out-Null } catch { Write-Warning "disable: $_" }
    try { Stop-ScheduledTask    -TaskName 'JarvisChatterGuardian' -ErrorAction Stop | Out-Null } catch { Write-Warning "stop: $_" }
    Get-CimInstance Win32_Process -Filter "Name like '%powershell%'" |
        Where-Object { $_.CommandLine -like '*chatter_guardian*' } |
        ForEach-Object { Write-Host "   kill guardian $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
        Where-Object { $_.CommandLine -like '*chatter.telethon_run*' } |
        ForEach-Object { Write-Host "   kill runner $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

$flag = Join-Path $Root 'state\chatter_semidemo_volska.flag'

if ($Revert) {
    Stop-Chatter
    # Прапор ГЕТЬ ДО ввімкнення гардіана — інакше він підніме Ольгу знову.
    if (Test-Path $flag) { Remove-Item $flag -Force }
    Write-Host '-> вмикаю JarvisChatterGuardian назад (склад = active.yaml = demo,demo2)'
    Enable-ScheduledTask -TaskName 'JarvisChatterGuardian' | Out-Null
    Start-ScheduledTask  -TaskName 'JarvisChatterGuardian'
    Write-Host 'ГОТОВО: прод повернуто, Аня піднімається під гардіаном.'
    exit 0
}

Stop-Chatter

# Прапор semidemo ПЕРЕД стартом гардіана: той читає його на СВОЄМУ старті
# (обидві гілки цього скрипта завжди перезапускають гардіан, тож стан свіжий).
if (-not (Test-Path $flag)) { New-Item -ItemType File -Path $flag | Out-Null }

Write-Host '-> вмикаю JarvisChatterGuardian у semidemo-режимі (персона volska, сесія demo)'
Enable-ScheduledTask -TaskName 'JarvisChatterGuardian' | Out-Null
Start-ScheduledTask  -TaskName 'JarvisChatterGuardian'
Write-Host ''
Write-Host 'ГОТОВО. Гардіан підніме раннер volska (дебаунс 3x30с + старт, ~2хв).'
Write-Host 'Гейт ЗАКРИТИЙ: відповідає лише allowlist (237616472) — тобі.'
Write-Host 'Після тестового прогону відкрити на живий трафік: /funnel_gate on confirm'
Write-Host 'Повернути прод (Аня): .\scripts\run_volska_semidemo.ps1 -Revert'
