param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [int]$Limit = 5
)

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $ProjectRoot

$PyExe = ".\.venv\Scripts\python.exe"
$env:PYTHONPATH = $ProjectRoot

Write-Host "=== JARVIS SAFE GATEWAY PLAN EXECUTOR V1.3 ===" -ForegroundColor Cyan

$RunPy = @"
from app.services.claude_ecosystem_execution_bridge import run_safe_gateway_plan
import json

result = run_safe_gateway_plan(
    project_root=r'''$ProjectRoot''',
    limit=$Limit,
)

print(json.dumps(result, ensure_ascii=False, indent=2))
"@

$RunPath = "jarvis_stage3_artifacts\claude_ecosystem\run_safe_gateway_v1_3.py"
[System.IO.File]::WriteAllText((Join-Path (Resolve-Path ".").Path $RunPath), $RunPy, [System.Text.UTF8Encoding]::new($false))

& $PyExe $RunPath

if ($LASTEXITCODE -ne 0) {
    throw "Safe gateway executor failed"
}

Write-Host "=== SAFE GATEWAY DONE ===" -ForegroundColor Green