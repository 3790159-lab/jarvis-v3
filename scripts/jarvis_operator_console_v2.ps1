param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$ProjectRootOverride = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try { chcp 65001 | Out-Null } catch {}
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$PSDefaultParameterValues["Out-File:Encoding"] = "utf8"
$PSDefaultParameterValues["Set-Content:Encoding"] = "utf8"
$PSDefaultParameterValues["Add-Content:Encoding"] = "utf8"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Pause-Console {
    Read-Host "Нажмите ВВОД для продолжения" | Out-Null
}

function Assert-FileExists {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path $Path)) {
        throw ("{0} not found: {1}" -f $Label, $Path)
    }
}

function Get-ApiUri {
    param([Parameter(Mandatory = $true)][string]$Path)
    return ($BaseUrl.TrimEnd("/") + $Path)
}

$ThisScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$HelpersPath   = Join-Path $ThisScriptDir "jarvis_real_run_helpers.ps1"

Assert-FileExists -Path $HelpersPath -Label "Helpers script"
. $HelpersPath

$ProjectRoot = Resolve-ProjectRoot -FallbackPath $ProjectRootOverride
if (-not $ProjectRoot) {
    throw "Failed to resolve project root."
}
if (-not (Test-Path $ProjectRoot)) {
    throw ("Project root does not exist: {0}" -f $ProjectRoot)
}

Set-Location $ProjectRoot

$ScriptsDir         = Join-Path $ProjectRoot "scripts"
$RunRealPath        = Join-Path $ScriptsDir "jarvis_run_real_sop_package.ps1"
$CleanupPath        = Join-Path $ScriptsDir "jarvis_cleanup_failed_test_runs.ps1"
$RestartPath        = Join-Path $ScriptsDir "restart_backend.ps1"
$Phase2FinalizePath = Join-Path $ScriptsDir "jarvis_sop_phase2_finalize.ps1"
$SmokePath          = Join-Path $ScriptsDir "jarvis_system_hardening_smoke.ps1"
$ArtifactsDir       = Join-Path $ProjectRoot "jarvis_stage3_artifacts"

function Show-Menu {
    Clear-Host
    Write-Host "================ Jarvis Operator Console v2 ================" -ForegroundColor Cyan
    Write-Host ("Project root: {0}" -f $ProjectRoot) -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "1) Health"
    Write-Host "2) Agents status"
    Write-Host "3) Run real SOP package"
    Write-Host "4) Search memory"
    Write-Host "5) Cleanup broken test memories"
    Write-Host "6) Restart backend"
    Write-Host "7) Open artifacts folder"
    Write-Host "8) Quick real run: Telegram workflow SOP + Direct Phase 2"
    Write-Host "9) Hardening smoke test"
    Write-Host "Q) Quit"
    Write-Host "============================================================" -ForegroundColor Cyan
}

$ExitConsole = $false

while (-not $ExitConsole) {
    Show-Menu
    $Choice = ([string](Read-Host "Select")).Trim().ToUpperInvariant()

    try {
        switch ($Choice) {
            "1" {
                Write-Host "== Backend health ==" -ForegroundColor Cyan
                Invoke-JsonRequest -Method "GET" -Uri (Get-ApiUri "/health") | ConvertTo-Json -Depth 20

                Write-Host "== Control health ==" -ForegroundColor Cyan
                try {
                    Invoke-JsonRequest -Method "GET" -Uri (Get-ApiUri "/api/control/health") | ConvertTo-Json -Depth 20
                }
                catch {
                    Write-Warning ("Control health endpoint failed: {0}" -f $_.Exception.Message)
                }

                Pause-Console
            }

            "2" {
                Write-Host "== Adapters ==" -ForegroundColor Cyan
                try {
                    Invoke-JsonRequest -Method "GET" -Uri (Get-ApiUri "/api/agents/adapters") | ConvertTo-Json -Depth 30
                }
                catch {
                    Write-Warning ("Adapters endpoint failed: {0}" -f $_.Exception.Message)
                }

                Write-Host "== Registry ==" -ForegroundColor Cyan
                try {
                    Invoke-JsonRequest -Method "GET" -Uri (Get-ApiUri "/api/agents/registry") | ConvertTo-Json -Depth 30
                }
                catch {
                    Write-Warning ("Registry endpoint failed: {0}" -f $_.Exception.Message)
                }

                Pause-Console
            }

            "3" {
                Assert-FileExists -Path $RunRealPath -Label "Run real SOP package script"

                $Topic = Read-Host "Enter SOP topic"
                if ([string]::IsNullOrWhiteSpace($Topic)) {
                    $Topic = "Jarvis Real Task Execution"
                }

                & $RunRealPath -BaseUrl $BaseUrl -Topic $Topic -ProjectRootOverride $ProjectRoot
                Pause-Console
            }

            "4" {
                $Query = Read-Host "Enter memory search query"
                if ([string]::IsNullOrWhiteSpace($Query)) {
                    $Query = "sop"
                }

                $EncodedQuery = [uri]::EscapeDataString($Query)
                $MemoryUri = (Get-ApiUri "/api/memory/search?q=$EncodedQuery&limit=10")
                Invoke-JsonRequest -Method "GET" -Uri $MemoryUri | ConvertTo-Json -Depth 30
                Pause-Console
            }

            "5" {
                Assert-FileExists -Path $CleanupPath -Label "Cleanup script"
                & $CleanupPath
                Pause-Console
            }

            "6" {
                Assert-FileExists -Path $RestartPath -Label "Restart backend script"
                & $RestartPath
                Pause-Console
            }

            "7" {
                if (-not (Test-Path $ArtifactsDir)) {
                    New-Item -ItemType Directory -Force -Path $ArtifactsDir | Out-Null
                }
                Start-Process explorer.exe $ArtifactsDir
            }

            "8" {
                Assert-FileExists -Path $RunRealPath -Label "Run real SOP package script"

                & $RunRealPath -BaseUrl $BaseUrl -Topic "Telegram workflow for handling real Jarvis operator requests" -ProjectRootOverride $ProjectRoot

                if (Get-Command Invoke-SopPhase2Finalize -ErrorAction SilentlyContinue) {
                    [void](Invoke-SopPhase2Finalize -ProjectRoot $ProjectRoot)
                }
                elseif (Test-Path $Phase2FinalizePath) {
                    Write-Warning "Helper function is missing. Running Phase 2 finalize script directly."
                    & $Phase2FinalizePath -ProjectRoot $ProjectRoot
                    if ($LASTEXITCODE -ne 0) {
                        Write-Warning ("Direct Phase 2 finalize returned exit code {0}" -f $LASTEXITCODE)
                    }
                }
                else {
                    Write-Warning ("Phase 2 finalize script not found: {0}" -f $Phase2FinalizePath)
                }

                Pause-Console
            }

            "9" {
                Assert-FileExists -Path $SmokePath -Label "Hardening smoke script"
                & $SmokePath -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl
                Pause-Console
            }

            "Q" {
                $ExitConsole = $true
            }

            default {
                Write-Host "Unknown choice." -ForegroundColor Yellow
                Pause-Console
            }
        }
    }
    catch {
        Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
        Pause-Console
    }
}
