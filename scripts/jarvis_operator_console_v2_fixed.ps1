



try { chcp 65001 | Out-Null } catch {}
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8'
$PSDefaultParameterValues['Add-Content:Encoding'] = 'utf8'
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

param(
    [string]$BaseUrl = "http://127.0.0.1:8015",
    [string]$ProjectRootOverride = ""
)


function Invoke-SopPhase2Finalize {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $WrapperPath = Join-Path $ProjectRoot "scripts\jarvis_sop_phase2_finalize.ps1"
    if (-not (Test-Path $WrapperPath)) {
        Write-Warning "Phase 2 wrapper not found: $WrapperPath"
        return
    }

    Write-Host ""
    Write-Host "== PHASE 2 FINALIZE ==" -ForegroundColor Cyan

    try {
        & $WrapperPath -ProjectRoot $ProjectRoot
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Phase 2 finalize returned exit code $LASTEXITCODE"
        }
    }
    catch {
        Write-Warning ("Phase 2 finalize failed: " + $_.Exception.Message)
    }
}


$ErrorActionPreference = "Stop"

$ThisScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ThisScriptDir "jarvis_real_run_helpers.ps1")

$ProjectRoot = Resolve-ProjectRoot -FallbackPath $ProjectRootOverride
Set-Location $ProjectRoot

$ScriptsDir = Join-Path $ProjectRoot "scripts"
$RunRealPath = Join-Path $ScriptsDir "jarvis_run_real_sop_package.ps1"
$CleanupPath = Join-Path $ScriptsDir "jarvis_cleanup_failed_test_runs.ps1"
$RestartPath = Join-Path $ScriptsDir "restart_backend.ps1"
$ArtifactsDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts"

function Show-Menu {
    Clear-Host
    Write-Host "================ Jarvis Operator Console v2 ================" -ForegroundColor Cyan
    Write-Host "Project root: $ProjectRoot" -ForegroundColor DarkGray
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
    Write-Host "============================================================" -ForegroundColor Cyan
}

do {
    Show-Menu
    $Choice = Read-Host "Select"

    try {
        switch ($Choice.ToUpper()) {
            "1" {
                Write-Host "== Backend health ==" -ForegroundColor Cyan
                Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/health" | ConvertTo-Json -Depth 20
                Write-Host "== Control health ==" -ForegroundColor Cyan
                Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/control/health" | ConvertTo-Json -Depth 20
                Pause
            }

            "2" {
                Write-Host "== Adapters ==" -ForegroundColor Cyan
                Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/agents/adapters" | ConvertTo-Json -Depth 30
                Write-Host "== Registry ==" -ForegroundColor Cyan
                Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/agents/registry" | ConvertTo-Json -Depth 30
                Pause
            }

            "3" {
                $Topic = Read-Host "Enter SOP topic"
                if ([string]::IsNullOrWhiteSpace($Topic)) {
                    $Topic = "Jarvis Real Task Execution"
                }

                & $RunRealPath -BaseUrl $BaseUrl -Topic $Topic -ProjectRootOverride $ProjectRoot
                Pause
            }

            "4" {
                $Query = Read-Host "Enter memory search query"
                if ([string]::IsNullOrWhiteSpace($Query)) {
                    $Query = "sop"
                }

                Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/memory/search?q=$([uri]::EscapeDataString($Query))&limit=10" | ConvertTo-Json -Depth 30
                Pause
            }

            "5" {
                & $CleanupPath
                Pause
            }

            "6" {
                & $RestartPath
                Pause
            }

            "7" {
                if (-not (Test-Path $ArtifactsDir)) {
                    New-Item -ItemType Directory -Force -Path $ArtifactsDir | Out-Null
                }
                Start-Process explorer.exe $ArtifactsDir
            }

            "8" {
                & $RunRealPath -BaseUrl $BaseUrl -Topic "Telegram workflow for handling real Jarvis operator requests" -ProjectRootOverride $ProjectRoot
                Pause

            Invoke-SopPhase2Finalize -ProjectRoot $ProjectRoot



            }

            "Q" {
                break
            }

            default {
                Write-Host "Unknown choice." -ForegroundColor Yellow
                Pause
            }
        }
    }
    catch {
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        Pause
    }
}
while ($true)