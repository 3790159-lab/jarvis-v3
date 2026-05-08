param(
    [int]$PanelPort = 8026
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    $conn = Get-NetTCPConnection -LocalPort $PanelPort -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($conn -and $conn.OwningProcess) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 600
        Write-Host ("Stopped panel on port {0}, pid {1}" -f $PanelPort, $conn.OwningProcess) -ForegroundColor Green
    }
    else {
        Write-Host ("No panel listener on port {0}" -f $PanelPort) -ForegroundColor Yellow
    }
}
catch {
    Write-Host ("No panel listener on port {0}" -f $PanelPort) -ForegroundColor Yellow
}