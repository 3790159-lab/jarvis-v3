param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$LegacyConsolePath = "",
    [string]$Phase2WrapperPath = "",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $LegacyConsolePath) {
    $LegacyConsolePath = Join-Path $ProjectRoot "scripts\jarvis_operator_console_v2.ps1"
}
if (-not $Phase2WrapperPath) {
    $Phase2WrapperPath = Join-Path $ProjectRoot "scripts\jarvis_sop_phase2_finalize.ps1"
}

function Pause-Console {
    Read-Host "Нажмите ВВОД для продолжения" | Out-Null
}

function Test-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (Test-Path $Path) {
        Write-Host ("[OK] {0}: {1}" -f $Label, $Path) -ForegroundColor Green
        return $true
    }

    Write-Warning ("[MISSING] {0}: {1}" -f $Label, $Path)
    return $false
}

function Test-PowerShellSyntax {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path $Path)) {
        Write-Warning ("Cannot parse missing file: {0}" -f $Path)
        return $false
    }

    $tokens = $null
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)

    if ($errors -and $errors.Count -gt 0) {
        Write-Warning ("Parser errors in: {0}" -f $Path)
        foreach ($err in $errors) {
            Write-Warning ("  Line {0}, Col {1}: {2}" -f $err.Extent.StartLineNumber, $err.Extent.StartColumnNumber, $err.Message)
        }
        return $false
    }

    Write-Host ("[PARSE OK] {0}" -f $Path) -ForegroundColor Green
    return $true
}

function Show-Diagnostics {
    Clear-Host
    Write-Host "================ Jarvis Diagnostics ================" -ForegroundColor Cyan
    Write-Host ("Project root: {0}" -f $ProjectRoot) -ForegroundColor Gray
    Write-Host ""

    $allOk = $true

    $checks = @(
        @{ Label = "Project root";      Path = $ProjectRoot },
        @{ Label = "Legacy console v2"; Path = $LegacyConsolePath },
        @{ Label = "Phase 2 finalize";  Path = $Phase2WrapperPath },
        @{ Label = "Launcher BAT";      Path = (Join-Path $ProjectRoot "start_jarvis_console_v3_clean.bat") },
        @{ Label = "App main";          Path = (Join-Path $ProjectRoot "app\main.py") },
        @{ Label = "Python venv";       Path = (Join-Path $ProjectRoot ".venv\Scripts\python.exe") }
    )

    foreach ($item in $checks) {
        $ok = Test-RequiredFile -Path $item.Path -Label $item.Label
        if (-not $ok) { $allOk = $false }
    }

    Write-Host ""

    $parseTargets = @(
        $LegacyConsolePath,
        $Phase2WrapperPath,
        (Join-Path $ProjectRoot "scripts\jarvis_operator_console_v3_clean.ps1")
    ) | Select-Object -Unique

    foreach ($path in $parseTargets) {
        $ok = Test-PowerShellSyntax -Path $path
        if (-not $ok) { $allOk = $false }
    }

    Write-Host ""
    if ($allOk) {
        Write-Host "Diagnostics result: READY" -ForegroundColor Green
    }
    else {
        Write-Warning "Diagnostics result: ATTENTION NEEDED"
    }
}

function Invoke-LegacyConsoleWindow {
    if (-not (Test-Path $LegacyConsolePath)) {
        throw "Legacy console not found: $LegacyConsolePath"
    }

    $proc = Start-Process -FilePath "powershell.exe" `
        -WorkingDirectory $ProjectRoot `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $LegacyConsolePath
        ) `
        -PassThru

    if (-not $proc) {
        throw "Failed to start legacy console process."
    }

    Write-Host ("Legacy console started. PID={0}" -f $proc.Id) -ForegroundColor Green
}

function Invoke-LegacyQuickRun8 {
    if (-not (Test-Path $LegacyConsolePath)) {
        throw "Legacy console not found: $LegacyConsolePath"
    }

    $inputFile = Join-Path $env:TEMP ("jarvis_console_input_" + [guid]::NewGuid().ToString("N") + ".txt")

    try {
        @(
            "8",
            "",
            "Q"
        ) | Set-Content -Path $inputFile -Encoding utf8

        $command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" < "{1}"' -f $LegacyConsolePath, $inputFile

        Write-Host "Running legacy quick workflow..." -ForegroundColor Cyan

        $proc = Start-Process -FilePath "cmd.exe" `
            -ArgumentList @("/d", "/c", $command) `
            -WorkingDirectory $ProjectRoot `
            -Wait `
            -PassThru

        if ($proc.ExitCode -ne 0) {
            throw "Legacy quick run exited with code $($proc.ExitCode)."
        }

        Write-Host "Legacy quick workflow completed." -ForegroundColor Green
    }
    finally {
        Remove-Item $inputFile -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Phase2Finalize {
    if (-not (Test-Path $Phase2WrapperPath)) {
        Write-Warning "Phase 2 wrapper not found: $Phase2WrapperPath"
        return $false
    }

    Write-Host ""
    Write-Host "== PHASE 2 FINALIZE ==" -ForegroundColor Cyan

    $proc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $Phase2WrapperPath,
            "-ProjectRoot", $ProjectRoot
        ) `
        -WorkingDirectory $ProjectRoot `
        -Wait `
        -PassThru

    if ($proc.ExitCode -ne 0) {
        Write-Warning ("Phase 2 finalize returned exit code {0}" -f $proc.ExitCode)
        return $false
    }

    Write-Host "Phase 2 finalize completed." -ForegroundColor Green
    return $true
}

function Invoke-ApiHealthProbe {
    Clear-Host
    Write-Host "================ Jarvis API Health ================" -ForegroundColor Cyan
    Write-Host ("Base URL: {0}" -f $BaseUrl) -ForegroundColor Gray

    foreach ($endpoint in @("/health", "/api/ai/health", "/api/autonomy/health", "/api/spreadsheets/health")) {
        $uri = $BaseUrl.TrimEnd("/") + $endpoint
        Write-Host ""
        Write-Host ("GET {0}" -f $uri) -ForegroundColor Cyan

        try {
            $response = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 15
            Write-Host ($response | ConvertTo-Json -Depth 10) -ForegroundColor Green
        }
        catch {
            Write-Warning $_.Exception.Message
        }
    }
}

function Show-Menu {
    Clear-Host
    Write-Host "================ Jarvis Operator Console v3 Clean ================" -ForegroundColor Cyan
    Write-Host ("Project root: {0}" -f $ProjectRoot) -ForegroundColor Gray
    Write-Host ""
    Write-Host "1) Open legacy console v2"
    Write-Host "8) Quick real run: Telegram workflow SOP + Direct Phase 2"
    Write-Host "P) Run Phase 2 finalize on latest SOP package"
    Write-Host "D) Diagnostics: files + parser checks"
    Write-Host "H) Health probe: local Jarvis API"
    Write-Host "Q) Quit"
    Write-Host "=================================================================" -ForegroundColor Cyan
}

while ($true) {
    Show-Menu
    $rawChoice = Read-Host "Select"
    $choice = ([string]$rawChoice).Trim().ToUpperInvariant()

    try {
        switch ($choice) {
            "1" {
                Invoke-LegacyConsoleWindow
                Pause-Console
            }
            "8" {
                Invoke-LegacyQuickRun8
                [void](Invoke-Phase2Finalize)
                Pause-Console
            }
            "P" {
                [void](Invoke-Phase2Finalize)
                Pause-Console
            }
            "D" {
                Show-Diagnostics
                Pause-Console
            }
            "H" {
                Invoke-ApiHealthProbe
                Pause-Console
            }
            "Q" {
                break
            }
            default {
                Write-Warning ("Неизвестный пункт меню: {0}" -f $choice)
                Start-Sleep -Seconds 1
            }
        }
    }
    catch {
        Write-Warning $_.Exception.Message
        Pause-Console
    }
}
