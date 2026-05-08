param(
    [Parameter(Mandatory=$true)][string]$Task,
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

$Tool = Join-Path $ProjectRoot "tools\jarvis_tool_selection_brain_v7_6.py"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\tool_selection_brain_v7_6"

Write-Host "=== JARVIS TOOL SELECTION BRAIN V7.6 ===" -ForegroundColor Cyan

$Args = @(
    "--project-root", $ProjectRoot,
    "--out-dir", $OutDir,
    "--task", $Task
)

if ($DryRun) {
    $Args += "--dry-run"
}

& $PyExe $Tool @Args

exit $LASTEXITCODE