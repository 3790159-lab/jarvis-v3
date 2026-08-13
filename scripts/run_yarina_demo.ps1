# Демо третьего клиента (Ярина) на СУЩЕСТВУЮЩЕЙ сессии аккаунта.
# Логин не нужен и НЕ выполняется. Образец — run_volska_semidemo.ps1.
#
# ЧТО ЭТО ДЕЛАЕТ. Ставит флаг state\chatter_demo_yarina.flag и перезапускает
# JarvisChatterGuardian. Гардиан видит флаг (блок DEMO OVERRIDE в
# chatter_guardian_detached.ps1), подставляет состав `yarina`, СВОЮ базу
# .secrets\yarina.db, свои логи — и сам поднимает/воскрешает раннер.
#
# ЧЕГО ЭТО НЕ ДЕЛАЕТ. Не трогает chatter/clients/active.yaml и ни одного файла
# клиента volska: состав задаётся ТОЛЬКО переменной окружения в гардиане.
#
# ЦЕНА. Аккаунт один, а персона не роутится по контакту — значит пока флаг
# стоит, ОЛЬГА НЕ РАБОТАЕТ. У неё есть живые лиды в runtime-allowlist, так что
# демо ставится осознанно и снимается сразу после: -Revert.
param(
    [switch]$Revert    # снять демо: вернуть Ольгу под гардианом
)

$ErrorActionPreference = 'Stop'
$Root = 'C:\jarvis'
Set-Location $Root

function Stop-Chatter {
    Write-Host '-> останавливаю гардиан и любой живой раннер'
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

$flag   = Join-Path $Root 'state\chatter_demo_yarina.flag'
$client = Join-Path $Root 'chatter\clients\yarina'

if ($Revert) {
    Stop-Chatter
    # Флаг ПРОЧЬ до включения гардиана — иначе он поднимет Ярину снова.
    if (Test-Path $flag) { Remove-Item $flag -Force }
    Write-Host '-> включаю JarvisChatterGuardian назад (Ольга)'
    Enable-ScheduledTask -TaskName 'JarvisChatterGuardian' | Out-Null
    Start-ScheduledTask  -TaskName 'JarvisChatterGuardian'
    Write-Host 'ГОТОВО: демо снято, Ольга поднимается под гардианом.'
    exit 0
}

# Проверка клиента ДО постановки флага. Флаг, поднятый раньше онбординга,
# уронил бы Ольгу ради персоны, которой нет: раннер упал бы на загрузке
# конфига, а гардиан крутил бы рестарты по кругу.
if (-not (Test-Path $client)) {
    Write-Host "ОТКАЗ: нет каталога клиента $client"
    Write-Host 'Сначала собери клиента (knowledge/settings/playbook/persona), потом демо.'
    exit 1
}

Stop-Chatter

New-Item -ItemType File -Path $flag -Force | Out-Null

Write-Host '-> включаю JarvisChatterGuardian в демо-режиме (персона yarina, сессия demo, база yarina.db)'
Enable-ScheduledTask -TaskName 'JarvisChatterGuardian' | Out-Null
Start-ScheduledTask  -TaskName 'JarvisChatterGuardian'
Write-Host ''
Write-Host 'ГОТОВО. Гардиан поднимет раннер yarina (~2 мин).'
Write-Host 'ОЛЬГА НА ЭТО ВРЕМЯ НЕ РАБОТАЕТ - аккаунт один.'
Write-Host 'Кого впустить в демо: /allow <id> в пульте (правка файлов не нужна).'
Write-Host 'Снять демо и вернуть Ольгу: .\scripts\run_yarina_demo.ps1 -Revert'
