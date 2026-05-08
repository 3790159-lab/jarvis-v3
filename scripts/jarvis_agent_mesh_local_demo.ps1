param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Stop"
$Py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Set-Location $ProjectRoot
& $Py "scripts\jarvis_agent_mesh_demo.py"