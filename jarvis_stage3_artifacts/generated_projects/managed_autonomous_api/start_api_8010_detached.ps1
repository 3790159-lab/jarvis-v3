param(
    [string]$BindHost = "127.0.0.1",
    [int]$BindPort = 8010
)

$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\generated_projects\managed_autonomous_api"
Set-Location $ProjectRoot

$pythonExe = if (Test-Path ".\.venv\Scripts\python.exe") {
    (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
    "python"
}

$stdoutLog = Join-Path $ProjectRoot "artifacts\phase13_runtime_fix\uvicorn_stdout.log"
$stderrLog = Join-Path $ProjectRoot "artifacts\phase13_runtime_fix\uvicorn_stderr.log"
$pidFile   = Join-Path $ProjectRoot "artifacts\phase13_runtime_fix\uvicorn_pid.txt"

if (Test-Path $stdoutLog) { Remove-Item $stdoutLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $stderrLog) { Remove-Item $stderrLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $pidFile)   { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }

$args = @(
    "-m", "uvicorn",
    "app.main:app",
    "--host", $BindHost,
    "--port", "$BindPort",
    "--log-level", "debug"
)

$proc = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList $args `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Start-Sleep -Seconds 3

$alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
if ($alive) {
    Set-Content -Path $pidFile -Value "$($proc.Id)" -Encoding ASCII
    Write-Host "Started detached API process PID=$($proc.Id)" -ForegroundColor Green
    Write-Host "stdout: $stdoutLog" -ForegroundColor Cyan
    Write-Host "stderr: $stderrLog" -ForegroundColor Cyan
    Write-Host "pid:    $pidFile" -ForegroundColor Cyan
    exit 0
}

Write-Host "Detached API process exited immediately." -ForegroundColor Red

if (Test-Path $stderrLog) {
    Write-Host "`n=== stderr ===" -ForegroundColor Yellow
    Get-Content $stderrLog -ErrorAction SilentlyContinue
}
if (Test-Path $stdoutLog) {
    Write-Host "`n=== stdout ===" -ForegroundColor Yellow
    Get-Content $stdoutLog -ErrorAction SilentlyContinue
}

exit 1
