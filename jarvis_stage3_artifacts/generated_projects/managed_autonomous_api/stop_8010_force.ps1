$port = 8010

Write-Host "Checking listeners on port $port..." -ForegroundColor Cyan

$conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "No TCP connections found on port $port" -ForegroundColor Yellow
    exit 0
}

$pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pidValue in $pids) {
    if ($pidValue -and $pidValue -ne 0) {
        try {
            $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "Stopping PID $pidValue ($($proc.ProcessName))" -ForegroundColor Yellow
            } else {
                Write-Host "Stopping PID $pidValue" -ForegroundColor Yellow
            }
            Stop-Process -Id $pidValue -Force -ErrorAction Stop
            Write-Host "Stopped PID $pidValue" -ForegroundColor Green
        } catch {
            Write-Host "Failed to stop PID $pidValue : $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

Start-Sleep -Seconds 2

$still = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
if ($still) {
    Write-Host "Port $port is still busy after stop attempt" -ForegroundColor Red
    $still | Select-Object LocalAddress,LocalPort,State,OwningProcess | Format-Table -AutoSize
    exit 1
}

Write-Host "Port $port is now free" -ForegroundColor Green
