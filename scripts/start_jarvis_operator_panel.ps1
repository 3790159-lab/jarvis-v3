param(
    [string]$ProjectRoot = "",
    [string]$BackendBaseUrl = "http://127.0.0.1:8015",
    [string]$Host = "127.0.0.1",
    [int]$Port = 8026,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$ServerPath = Join-Path $ProjectRoot "scripts\jarvis_operator_panel_server.py"
$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

if (-not (Test-Path $ServerPath)) {
    throw "Panel server not found: $ServerPath"
}

if ($OpenBrowser) {
    Start-Sleep -Milliseconds 800
    Start-Process ("http://{0}:{1}/" -f $Host, $Port)
}

& $PyExe $ServerPath --project-root $ProjectRoot --backend-base-url $BackendBaseUrl --host $Host --port $Port