from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
BACKUP_DIR = PROJECT_ROOT / ("backup_port_repair_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

def write_find_port_owner() -> None:
    path = SCRIPTS_DIR / "find_port_owner.ps1"
    backup(path)
    content = r'''param(
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

$pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pid in $pids) {
    try {
        Get-CimInstance Win32_Process -Filter "ProcessId = $pid" |
            Select-Object ProcessId, Name, CommandLine |
            Format-List
    } catch {
        Write-Host ("[WARN] Failed to inspect PID {0}: {1}" -f $pid, $_.Exception.Message)
    }
}
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_stop_port_owner() -> None:
    path = SCRIPTS_DIR / "stop_port_owner.ps1"
    backup(path)
    content = r'''param(
    [int]$Port = 8010
)

$ErrorActionPreference = "Stop"

$connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if (-not $connections) {
    Write-Host "[INFO] No process is listening on this port."
    exit 0
}

$pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pid in $pids) {
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pid"
        Write-Host ("[INFO] Stopping PID {0} | {1}" -f $pid, $proc.CommandLine)
        Stop-Process -Id $pid -Force -ErrorAction Stop
        Write-Host ("[OK] Stopped PID {0}" -f $pid)
    } catch {
        Write-Host ("[WARN] Failed to stop PID {0}: {1}" -f $pid, $_.Exception.Message)
    }
}
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_restart_backend_on_8010() -> None:
    path = SCRIPTS_DIR / "restart_backend_8010_safe.ps1"
    backup(path)
    content = r'''param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

chcp 65001 | Out-Null
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $ProjectRoot

& (Join-Path $ProjectRoot "scripts\stop_port_owner.ps1") -Port 8010
Start-Sleep -Seconds 2

$test = Get-NetTCPConnection -LocalPort 8010 -ErrorAction SilentlyContinue
if ($test) {
    throw "Port 8010 is still busy after stop attempt."
}

& (Join-Path $ProjectRoot "scripts\start_backend_safe.ps1") -Port 8010
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_health_check() -> None:
    path = SCRIPTS_DIR / "check_backend_health.ps1"
    backup(path)
    content = r'''param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

$ErrorActionPreference = "Stop"

try {
    $resp = Invoke-RestMethod -Method GET -Uri ($BaseUrl + "/health") -TimeoutSec 10
    Write-Host "[OK] Health response:"
    $resp | ConvertTo-Json -Depth 10
} catch {
    Write-Host ("[WARN] Health check failed: {0}" -f $_.Exception.Message)
    exit 1
}
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def main() -> None:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    write_find_port_owner()
    write_stop_port_owner()
    write_restart_backend_on_8010()
    write_health_check()

    print(f"[DONE] backups: {BACKUP_DIR}")
    print("[DONE] created:")
    print("       scripts/find_port_owner.ps1")
    print("       scripts/stop_port_owner.ps1")
    print("       scripts/restart_backend_8010_safe.ps1")
    print("       scripts/check_backend_health.ps1")

if __name__ == "__main__":
    main()
