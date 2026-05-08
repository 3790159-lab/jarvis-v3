param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\generated_projects\managed_autonomous_api"
$DiagDir = Join-Path $ProjectRoot "artifacts\jarvis_control"
New-Item -ItemType Directory -Force -Path $DiagDir | Out-Null

$reportPath = Join-Path $DiagDir "diagnostics_report.txt"
$stdoutLog = Join-Path $ProjectRoot "artifacts\phase13_runtime_fix\uvicorn_stdout.log"
$stderrLog = Join-Path $ProjectRoot "artifacts\phase13_runtime_fix\uvicorn_stderr.log"

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("=== JARVIS DIAGNOSTICS REPORT ===")
$lines.Add("Timestamp: $(Get-Date -Format s)")
$lines.Add("BaseUrl: $BaseUrl")
$lines.Add("")

try {
    $health = Invoke-RestMethod -Method GET -Uri "$BaseUrl/health" -TimeoutSec 5
    $lines.Add("[HEALTH] OK")
    $lines.Add(($health | ConvertTo-Json -Depth 20))
} catch {
    $lines.Add("[HEALTH] FAILED")
    $lines.Add($_.Exception.Message)
}

try {
    $owner = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction Stop | Select-Object -First 1
    $pidValue = $owner.OwningProcess
    $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    $lines.Add("")
    $lines.Add("[PORT OWNER] 8010")
    $lines.Add("PID: $pidValue")
    if ($proc) {
        $lines.Add("ProcessName: $($proc.ProcessName)")
    }
} catch {
    $lines.Add("")
    $lines.Add("[PORT OWNER] FAILED")
    $lines.Add($_.Exception.Message)
}

$checks = @(
    "/api/execution/health",
    "/api/tools/health",
    "/api/feedback/health",
    "/api/mission-memory-v2/health",
    "/api/hitl/health",
    "/api/autonomous-decision/health"
)

foreach ($path in $checks) {
    $lines.Add("")
    try {
        $resp = Invoke-RestMethod -Method GET -Uri "$BaseUrl$path" -TimeoutSec 5
        $lines.Add("[CHECK] $path -> OK")
        $lines.Add(($resp | ConvertTo-Json -Depth 20))
    } catch {
        $lines.Add("[CHECK] $path -> FAILED")
        $lines.Add($_.Exception.Message)
    }
}

$lines.Add("")
$lines.Add("=== LAST STDERR ===")
if (Test-Path $stderrLog) {
    Get-Content $stderrLog -Tail 100 | ForEach-Object { $lines.Add($_) }
} else {
    $lines.Add("stderr log not found")
}

$lines.Add("")
$lines.Add("=== LAST STDOUT ===")
if (Test-Path $stdoutLog) {
    Get-Content $stdoutLog -Tail 100 | ForEach-Object { $lines.Add($_) }
} else {
    $lines.Add("stdout log not found")
}

Set-Content -Path $reportPath -Value $lines -Encoding UTF8
Write-Host "Diagnostics saved to: $reportPath" -ForegroundColor Green
Get-Content $reportPath
