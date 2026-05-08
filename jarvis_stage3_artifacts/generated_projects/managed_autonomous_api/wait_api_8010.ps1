param(
    [string]$BaseUrl = "http://127.0.0.1:8010",
    [int]$TimeoutSeconds = 35
)

$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\generated_projects\managed_autonomous_api"
$stderrLog = Join-Path $ProjectRoot "artifacts\phase13_runtime_fix\uvicorn_stderr.log"
$stdoutLog = Join-Path $ProjectRoot "artifacts\phase13_runtime_fix\uvicorn_stdout.log"
$pidFile   = Join-Path $ProjectRoot "artifacts\phase13_runtime_fix\uvicorn_pid.txt"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ok = $false

Write-Host ""
Write-Host "== Waiting for API readiness ==" -ForegroundColor Cyan

while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 3
        Write-Host "API is reachable" -ForegroundColor Green
        $health | ConvertTo-Json -Depth 10
        $ok = $true
        break
    } catch {
        if (Test-Path $pidFile) {
            $pidValue = Get-Content $pidFile -ErrorAction SilentlyContinue
            if ($pidValue) {
                $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
                if (-not $proc) {
                    Write-Host "API process is no longer running." -ForegroundColor Red
                    if (Test-Path $stderrLog) {
                        Write-Host "`n=== stderr ===" -ForegroundColor Yellow
                        Get-Content $stderrLog -ErrorAction SilentlyContinue
                    }
                    if (Test-Path $stdoutLog) {
                        Write-Host "`n=== stdout ===" -ForegroundColor Yellow
                        Get-Content $stdoutLog -ErrorAction SilentlyContinue
                    }
                    exit 2
                }
            }
        }
        Start-Sleep -Milliseconds 900
    }
}

if (-not $ok) {
    Write-Host "API did not become ready in time" -ForegroundColor Red
    if (Test-Path $stderrLog) {
        Write-Host "`n=== stderr ===" -ForegroundColor Yellow
        Get-Content $stderrLog -ErrorAction SilentlyContinue
    }
    if (Test-Path $stdoutLog) {
        Write-Host "`n=== stdout ===" -ForegroundColor Yellow
        Get-Content $stdoutLog -ErrorAction SilentlyContinue
    }
    exit 1
}

exit 0
