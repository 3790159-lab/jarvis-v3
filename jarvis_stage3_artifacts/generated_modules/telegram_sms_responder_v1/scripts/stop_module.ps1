param([int]$Port = 8110)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

try {
    $Conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    $Ids = @($Conns | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($ProcId in $Ids) {
        if ($ProcId -and $ProcId -ne $PID) {
            try { & taskkill.exe /PID $ProcId /T /F | Out-Null } catch { }
            try { Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue } catch { }
        }
    }
} catch { }

Write-Host ""
Write-Host "=== TELEGRAM MODULE API STOP REQUESTED ===" -ForegroundColor Green