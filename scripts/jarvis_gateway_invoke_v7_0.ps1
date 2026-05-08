param(
    [Parameter(Mandatory=$true)][string]$ToolName,
    [string]$ArgsJson = "{}",
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Gateway = Join-Path $ProjectRoot "tools\jarvis_unified_tool_gateway_v7_0.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\unified_tool_gateway_v7_0"

& $PyExe $Gateway `
    --project-root $ProjectRoot `
    --out-dir $OutDir `
    --tool $ToolName `
    --args-json $ArgsJson