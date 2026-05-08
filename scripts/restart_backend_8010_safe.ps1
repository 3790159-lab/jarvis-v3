param()

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
