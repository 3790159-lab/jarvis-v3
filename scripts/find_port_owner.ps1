param(
    [int]$Port = 8010
)

$ErrorActionPreference = "Stop"

Write-Host ("[INFO] Checking port {0}" -f $Port)

$connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if (-not $connections) {
    Write-Host "[INFO] No process is listening on this port."
    exit 0
}

$connections | Select-Object LocalAddress, LocalPort, State, OwningProcess | Format-Table -AutoSize

$procIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $procIds) {
    try {
        Get-CimInstance Win32_Process -Filter "ProcessId = $procId" |
            Select-Object ProcessId, Name, CommandLine |
            Format-List
    } catch {
        Write-Host ("[WARN] Failed to inspect PID {0}: {1}" -f $procId, $_.Exception.Message)
    }
}

