param(
    [int]$Port = 8010,
    [int]$IntervalSeconds = 15
)

function Check-Backend {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

function Start-Backend {
    Write-Host "[INFO] Starting backend..."
    
    if (Test-Path ".\.venv\Scripts\python.exe") {
        Start-Process -FilePath ".\.venv\Scripts\python.exe" `
            -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port $Port"
    } else {
        Start-Process -FilePath "python" `
            -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port $Port"
    }

    Start-Sleep 4
}

$lastState = ""

while ($true) {
    $alive = Check-Backend

    if (-not $alive) {
        if ($lastState -ne "dead") {
            Write-Host "[WARN] Backend not responding. Restarting..."
            $lastState = "dead"
        }
        Start-Backend
    } else {
        if ($lastState -ne "alive") {
            Write-Host "[OK] Backend alive"
            $lastState = "alive"
        }
    }

    Start-Sleep $IntervalSeconds
}
