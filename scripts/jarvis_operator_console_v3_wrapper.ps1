param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$LegacyConsolePath = "",
    [string]$Phase2WrapperPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $LegacyConsolePath) {
    $LegacyConsolePath = Join-Path $ProjectRoot "scripts\jarvis_operator_console_v2.ps1"
}
if (-not $Phase2WrapperPath) {
    $Phase2WrapperPath = Join-Path $ProjectRoot "scripts\jarvis_sop_phase2_finalize.ps1"
}

function Invoke-LegacyMenuOption {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Option
    )

    if (-not (Test-Path $LegacyConsolePath)) {
        throw "Legacy console not found: $LegacyConsolePath"
    }

    $InputFile = Join-Path $env:TEMP ("jarvis_console_input_" + [guid]::NewGuid().ToString("N") + ".txt")

    try {
        @(
            $Option,
            "",
            "Q"
        ) | Set-Content -Path $InputFile -Encoding utf8

        $cmd = 'powershell -NoProfile -ExecutionPolicy Bypass -File "{0}" < "{1}"' -f $LegacyConsolePath, $InputFile
        cmd.exe /c $cmd
    }
    finally {
        Remove-Item $InputFile -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-SopPhase2Finalize {
    if (-not (Test-Path $Phase2WrapperPath)) {
        Write-Warning "Phase 2 wrapper not found: $Phase2WrapperPath"
        return
    }

    Write-Host ""
    Write-Host "== PHASE 2 FINALIZE ==" -ForegroundColor Cyan

    & $Phase2WrapperPath -ProjectRoot $ProjectRoot
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Phase 2 finalize returned exit code $LASTEXITCODE"
    }
}

function Show-Menu {
    Clear-Host
    Write-Host "================ Jarvis Operator Console v3 Wrapper ================" -ForegroundColor Cyan
    Write-Host "Project root: $ProjectRoot" -ForegroundColor Gray
    Write-Host ""
    Write-Host "1) Health"
    Write-Host "2) Agents status"
    Write-Host "3) Run real SOP package"
    Write-Host "4) Search memory"
    Write-Host "5) Cleanup broken test memories"
    Write-Host "6) Restart backend"
    Write-Host "7) Open artifacts folder"
    Write-Host "8) Quick real run: Telegram workflow SOP + Direct Phase 2"
    Write-Host "Q) Quit"
    Write-Host "===================================================================" -ForegroundColor Cyan
}

while ($true) {
    Show-Menu
    $choice = Read-Host "Select"

    switch ($choice) {
        "1" { Invoke-LegacyMenuOption -Option "1"; Read-Host "Нажмите ВВОД для продолжения" | Out-Null }
        "2" { Invoke-LegacyMenuOption -Option "2"; Read-Host "Нажмите ВВОД для продолжения" | Out-Null }
        "3" { Invoke-LegacyMenuOption -Option "3"; Read-Host "Нажмите ВВОД для продолжения" | Out-Null }
        "4" { Invoke-LegacyMenuOption -Option "4"; Read-Host "Нажмите ВВОД для продолжения" | Out-Null }
        "5" { Invoke-LegacyMenuOption -Option "5"; Read-Host "Нажмите ВВОД для продолжения" | Out-Null }
        "6" { Invoke-LegacyMenuOption -Option "6"; Read-Host "Нажмите ВВОД для продолжения" | Out-Null }
        "7" { Invoke-LegacyMenuOption -Option "7"; Read-Host "Нажмите ВВОД для продолжения" | Out-Null }
        "8" { Invoke-LegacyMenuOption -Option "8"; Invoke-SopPhase2Finalize; Read-Host "Нажмите ВВОД для продолжения" | Out-Null }
        { $_ -match '^(Q|q)$' } { break }
        default { Write-Warning "Неизвестный пункт меню: $choice"; Start-Sleep -Seconds 1 }
    }
}