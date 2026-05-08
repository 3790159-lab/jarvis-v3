param(
    [ValidateSet("status","selfcheck","routes","diagnostics","smoke","start","stop","restart")]
    [string]$Action = "status",
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

Set-Location "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\generated_projects\managed_autonomous_api"

switch ($Action) {
    "status" {
        Write-Host ""
        Write-Host "== Jarvis Status ==" -ForegroundColor Cyan
        try {
            Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 5 | ConvertTo-Json -Depth 20
        } catch {
            Write-Host "API unreachable at $BaseUrl" -ForegroundColor Red
            exit 1
        }
    }

    "selfcheck" {
        Write-Host ""
        Write-Host "== Jarvis Runtime Selfcheck ==" -ForegroundColor Cyan
        if (Test-Path ".\.venv\Scripts\python.exe") {
            .\.venv\Scripts\python.exe .\jarvis_runtime_selfcheck.py
        } else {
            python .\jarvis_runtime_selfcheck.py
        }
    }

    "routes" {
        powershell -ExecutionPolicy Bypass -File ".\jarvis_routes_audit.ps1" -BaseUrl $BaseUrl
        exit $LASTEXITCODE
    }

    "diagnostics" {
        powershell -ExecutionPolicy Bypass -File ".\jarvis_diagnostics.ps1" -BaseUrl $BaseUrl
        exit $LASTEXITCODE
    }

    "smoke" {
        powershell -ExecutionPolicy Bypass -File ".\jarvis_smoke_all.ps1" -BaseUrl $BaseUrl
        exit $LASTEXITCODE
    }

    "stop" {
        powershell -ExecutionPolicy Bypass -File ".\stop_8010_force.ps1"
        exit $LASTEXITCODE
    }

    "start" {
        powershell -ExecutionPolicy Bypass -File ".\start_api_8010_detached.ps1" -BindHost "127.0.0.1" -BindPort 8010
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        powershell -ExecutionPolicy Bypass -File ".\wait_api_8010.ps1" -BaseUrl $BaseUrl -TimeoutSeconds 35
        exit $LASTEXITCODE
    }

    "restart" {
        powershell -ExecutionPolicy Bypass -File ".\stop_8010_force.ps1"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        powershell -ExecutionPolicy Bypass -File ".\start_api_8010_detached.ps1" -BindHost "127.0.0.1" -BindPort 8010
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        powershell -ExecutionPolicy Bypass -File ".\wait_api_8010.ps1" -BaseUrl $BaseUrl -TimeoutSeconds 35
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        powershell -ExecutionPolicy Bypass -File ".\jarvis_routes_audit.ps1" -BaseUrl $BaseUrl
        exit $LASTEXITCODE
    }
}
