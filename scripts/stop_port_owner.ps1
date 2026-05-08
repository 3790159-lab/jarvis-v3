param(
    [int]$Port = 8010
)

$ErrorActionPreference = "Stop"

$connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if (-not $connections) {
    Write-Host "[INFO] No process is listening on this port."
    exit 0
}

$procIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $procIds) {
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId"
        Write-Host ("[INFO] Stopping PID {0} | {1}" -f $procId, $proc.CommandLine)
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host ("[OK] Stopped PID {0}" -f $procId)
    } catch {
        Write-Host ("[WARN] Failed to stop PID {0}: {1}" -f $procId, $_.Exception.Message)
    }
}

