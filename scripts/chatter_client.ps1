# Старт/стоп/статус ОДНОГО клиента chatter.
#
# Правит ЖЕЛАЕМОЕ состояние (chatter/clients/registry.yaml), а процессами не
# управляет вовсе — их приводит к желаемому супервизор JarvisChatterGuardian за
# один цикл (~30с). Это не формальность: вторая ручка, дёргающая процессы в
# обход реестра, немедленно рассинхронизировала бы желаемое с наблюдаемым, и
# «почему клиент снова поднялся» стало бы неотвечаемым вопросом.
#
# Будущий дашборд пишет в ТО ЖЕ поле enabled и читает то же наблюдаемое
# состояние (state/chatter_clients.json) — поэтому он ложится сверху без
# переделки супервизора.
#
#   .\scripts\chatter_client.ps1 -Slug volska -Action start
#   .\scripts\chatter_client.ps1 -Slug volska -Action stop
#   .\scripts\chatter_client.ps1 -Slug volska -Action status
#   .\scripts\chatter_client.ps1 -Action list
param(
    [string]$Slug,
    [Parameter(Mandatory)][ValidateSet('start', 'stop', 'status', 'list')][string]$Action,
    [string]$Root = 'C:\jarvis'
)

$ErrorActionPreference = 'Stop'

$registry  = Join-Path $Root 'chatter\clients\registry.yaml'
$stateFile = Join-Path $Root 'state\chatter_clients.json'

function Fail([string]$msg) {
    # DEV-18: молчаливый успех на опечатке в slug'е = владелец уверен, что
    # остановил клиента, а тот работает.
    Write-Host "[chatter_client] ОШИБКА: $msg"
    exit 1
}

if ($Action -in @('start', 'stop', 'status') -and -not $Slug) {
    Fail "действие '$Action' требует -Slug"
}
if (-not (Test-Path $registry)) { Fail "реестр не найден: $registry" }

function Read-ObservedState {
    if (-not (Test-Path $stateFile)) { return $null }
    try { return (Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { Fail "state/chatter_clients.json не разбирается: $($_.Exception.Message)" }
}

function Set-Enabled([string]$TargetSlug, [bool]$Value) {
    # Точечная правка ТЕКСТОМ, а не через YAML-раунд-трип: реестр — рабочий
    # документ с инструкциями в комментариях, и перезапись парсером снесла бы
    # их. Тот же приём, что у /funnel_gate в settings.yaml.
    $lines = @(Get-Content -LiteralPath $registry -Encoding UTF8)
    $inBlock = $false
    $blockIndent = -1
    $done = $false

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match '^(\s+)([A-Za-z0-9_\-\.]+)\s*:\s*(#.*)?$') {
            $indent = $Matches[1].Length
            $name = $Matches[2]
            if ($inBlock -and $indent -le $blockIndent) { break }  # начался следующий клиент
            if (-not $inBlock -and $name -eq $TargetSlug) {
                $inBlock = $true
                $blockIndent = $indent
                continue
            }
        }
        if ($inBlock -and $line -match '^(\s*enabled\s*:\s*)(true|false)(\s*.*)$') {
            $lines[$i] = $Matches[1] + $(if ($Value) { 'true' } else { 'false' }) + $Matches[3]
            $done = $true
            break
        }
    }

    if (-not $inBlock) { Fail "клиент '$TargetSlug' не найден в реестре $registry" }
    if (-not $done)    { Fail "у клиента '$TargetSlug' нет строки 'enabled:' — правлю только существующее поле" }

    # UTF-8 без BOM: реестр читает Python (chatter.registry_cli), BOM ему помеха.
    [System.IO.File]::WriteAllText(
        $registry, ($lines -join "`r`n") + "`r`n",
        (New-Object System.Text.UTF8Encoding $false))
}

switch ($Action) {
    'start' {
        Set-Enabled -TargetSlug $Slug -Value $true
        Write-Host "[chatter_client] $Slug -> enabled: true"
        Write-Host "Супервизор поднимет его в течение ~30с. Проверить: -Action status"
    }
    'stop' {
        Set-Enabled -TargetSlug $Slug -Value $false
        Write-Host "[chatter_client] $Slug -> enabled: false"
        Write-Host "Супервизор остановит его в течение ~30с. Процессы отсюда НЕ убиваются намеренно."
    }
    'status' {
        $state = Read-ObservedState
        if (-not $state) { Fail "наблюдаемого состояния ещё нет ($stateFile) — супервизор не отработал ни одного цикла" }
        $entry = $state.clients.$Slug
        if (-not $entry) { Fail "клиента '$Slug' нет в наблюдаемом состоянии" }
        Write-Host ("{0}: state={1} desired={2} pid={3} fails={4}" -f `
            $Slug, $entry.state, $entry.desired, $entry.pid, $entry.consecutive_fail)
        if ($entry.last_error) { Write-Host ("  last_error: {0}" -f $entry.last_error) }
    }
    'list' {
        $state = Read-ObservedState
        if (-not $state) { Fail "наблюдаемого состояния ещё нет ($stateFile) — супервизор не отработал ни одного цикла" }
        if ($state.fatal) { Write-Host "РЕЕСТР СЛОМАН: $($state.fatal)" }
        foreach ($p in $state.clients.PSObject.Properties) {
            $e = $p.Value
            Write-Host ("{0,-12} state={1,-8} desired={2,-8} pid={3}" -f $p.Name, $e.state, $e.desired, $e.pid)
            if ($e.last_error) { Write-Host ("             last_error: {0}" -f $e.last_error) }
        }
    }
}
exit 0
