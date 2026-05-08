$port = 8010
$conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue

if (-not $conn) {
    Write-Host "No listening process on port $port" -ForegroundColor Yellow
    exit 0
}

$pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pidValue in $pids) {
    try {
        Write-Host "Stopping PID $pidValue on port $port" -ForegroundColor Yellow
        Stop-Process -Id $pidValue -Force -ErrorAction Stop
        Write-Host "Stopped PID $pidValue" -ForegroundColor Green
    } catch {
        Write-Host "Failed to stop PID $pidValue : $($_.Exception.Message)" -ForegroundColor Red
    }
}
