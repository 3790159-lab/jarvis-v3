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
    return ($script:BaseUrl.TrimEnd("/") + $Path)
}

function Show-Diagnostics {
    Write-Host "== Diagnostics ==" -ForegroundColor Cyan

    $checks = @(
        @{ Label = "Helper";      Path = $script:HelperPath },
        @{ Label = "RunReal";     Path = $script:RunRealPath },
        @{ Label = "Phase2";      Path = $script:Phase2Path },
        @{ Label = "Smoke";       Path = $script:SmokePath },
        @{ Label = "NormalizePy"; Path = $script:NormalizePy }
    )

    foreach ($item in $checks) {
        if (Test-Path $item.Path) {
            Write-Host ("[OK] {0}: {1}" -f $item.Label, $item.Path) -ForegroundColor Green
        }
        else {
            Write-Warning ("[MISSING] {0}: {1}" -f $item.Label, $item.Path)
        }
    }

    try {
        $health = Invoke-RestMethod -Method Get -Uri (Get-ApiUri "/health") -TimeoutSec 10
        Write-Host "API health reachable." -ForegroundColor Green
    }
    catch {
        Write-Warning ("API health probe failed: {0}" -f $_.Exception.Message)
    }
}

$script:BaseUrl = $BaseUrl
$script:ThisScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:HelperPath = Join-Path $script:ThisScriptDir "jarvis_real_run_helpers.ps1"

Assert-FileExists -Path $script:HelperPath -Label "Helpers script"
. $script:HelperPath

if (Get-Command Resolve-ProjectRoot -ErrorAction SilentlyContinue) {
    $script:ProjectRoot = Resolve-ProjectRoot -FallbackPath $ProjectRootOverride
}
else {
    if ([string]::IsNullOrWhiteSpace($ProjectRootOverride)) {
        $script:ProjectRoot = Split-Path -Parent $script:ThisScriptDir
    }
    else {
        $script:ProjectRoot = $ProjectRootOverride
    }
}

if (-not $script:ProjectRoot) {
    throw "Failed to resolve project root."
}
if (-not (Test-Path $script:ProjectRoot)) {
    throw ("Project root does not exist: {0}" -f $script:ProjectRoot)
}

Set-Location $script:ProjectRoot

$script:ScriptsDir   = Join-Path $script:ProjectRoot "scripts"
$script:RunRealPath  = Join-Path $script:ScriptsDir "jarvis_run_real_sop_package.ps1"
$script:CleanupPath  = Join-Path $script:ScriptsDir "jarvis_cleanup_failed_test_runs.ps1"
$script:RestartPath  = Join-Path $script:ScriptsDir "restart_backend.ps1"
$script:Phase2Path   = Join-Path $script:ScriptsDir "jarvis_sop_phase2_finalize.ps1"
$script:SmokePath    = Join-Path $script:ScriptsDir "jarvis_system_hardening_smoke.ps1"
$script:NormalizePy  = Join-Path $script:ScriptsDir "jarvis_artifact_normalize_utf8.py"
$script:ArtifactsDir = Join-Path $script:ProjectRoot "jarvis_stage3_artifacts"

function Show-Menu {
    Clear-Host
    Write-Host "================ Jarvis Operator Console v4 ================" -ForegroundColor Cyan
    Write-Host ("Project root: {0}" -f $script:ProjectRoot) -ForegroundColor DarkGray
    Write-Host ("Base URL    : {0}" -f $script:BaseUrl) -ForegroundColor DarkGray
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
    Write-Host "D) Diagnostics"
    Write-Host "Q) Quit"
    Write-Host "============================================================" -ForegroundColor Cyan
}

$exitConsole = $false

while (-not $exitConsole) {
    Show-Menu
    $choice = Read-Host "Select"
    $choice = ([string]$choice).Trim().ToUpperInvariant()

    try {
        switch ($choice) {
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
                Assert-FileExists -Path $script:RunRealPath -Label "Run real SOP package script"
                $topic = Read-Host "Enter SOP topic"
                if ([string]::IsNullOrWhiteSpace($topic)) {
                    $topic = "Jarvis Real Task Execution"
                }
                & $script:RunRealPath -BaseUrl $script:BaseUrl -Topic $topic -ProjectRootOverride $script:ProjectRoot
                Pause-Console
            }

            "4" {
                $query = Read-Host "Enter memory search query"
                if ([string]::IsNullOrWhiteSpace($query)) {
                    $query = "sop"
                }
                $encodedQuery = [uri]::EscapeDataString($query)
                $uri = Get-ApiUri ("/api/memory/search?q=" + $encodedQuery + "&limit=10")
                Invoke-JsonRequest -Method "GET" -Uri $uri | ConvertTo-Json -Depth 30
                Pause-Console
            }

            "5" {
                Assert-FileExists -Path $script:CleanupPath -Label "Cleanup script"
                & $script:CleanupPath
                Pause-Console
            }

            "6" {
                Assert-FileExists -Path $script:RestartPath -Label "Restart backend script"
                & $script:RestartPath
                Pause-Console
            }

            "7" {
                if (-not (Test-Path $script:ArtifactsDir)) {
                    New-Item -ItemType Directory -Force -Path $script:ArtifactsDir | Out-Null
                }
                Start-Process explorer.exe $script:ArtifactsDir
            }

            "8" {
                Assert-FileExists -Path $script:RunRealPath -Label "Run real SOP package script"
                & $script:RunRealPath -BaseUrl $script:BaseUrl -Topic "Telegram workflow for handling real Jarvis operator requests" -ProjectRootOverride $script:ProjectRoot

                if (Get-Command Invoke-SopPhase2Finalize -ErrorAction SilentlyContinue) {
                    [void](Invoke-SopPhase2Finalize -ProjectRoot $script:ProjectRoot)
                }
                elseif (Test-Path $script:Phase2Path) {
                    Write-Warning "Helper function is missing. Running Phase 2 finalize script directly."
                    & $script:Phase2Path -ProjectRoot $script:ProjectRoot
                    if ($LASTEXITCODE -ne 0) {
                        Write-Warning ("Direct Phase 2 finalize returned exit code {0}" -f $LASTEXITCODE)
                    }
                }
                else {
                    Write-Warning ("Phase 2 finalize script not found: {0}" -f $script:Phase2Path)
                }

                Pause-Console
            }

            "9" {
                Assert-FileExists -Path $script:SmokePath -Label "Hardening smoke script"
                & $script:SmokePath -ProjectRoot $script:ProjectRoot -BaseUrl $script:BaseUrl
                Pause-Console
            }

            "D" {
                Show-Diagnostics
                Pause-Console
            }

            "Q" {
                $exitConsole = $true
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