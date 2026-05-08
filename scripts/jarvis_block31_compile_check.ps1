param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    $Python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCmd) { throw "Python not found." }
    $Python = $PythonCmd.Source
}

Write-Host "== Block 3 foundation compile ==" -ForegroundColor Cyan

$Targets = @(
    "app\services\risk_policies.py",
    "app\services\approval_store.py",
    "app\services\task_marketplace.py",
    "app\services\agent_adapters.py",
    "app\services\semantic_memory.py",
    "app\routers\agent_control_plane.py",
    "app\main.py"
)

foreach ($t in $Targets) {
    Write-Host "Checking $t" -ForegroundColor DarkGray
    & $Python -m py_compile $t
    if ($LASTEXITCODE -ne 0) {
        throw "Compile error in $t"
    }
}

$ImportScript = Join-Path $ProjectRoot "jarvis_block31_import_check.py"
$PyLines = @(
    'import importlib',
    'importlib.import_module("app.services.risk_policies")',
    'importlib.import_module("app.services.approval_store")',
    'importlib.import_module("app.services.task_marketplace")',
    'importlib.import_module("app.services.agent_adapters")',
    'importlib.import_module("app.services.semantic_memory")',
    'importlib.import_module("app.routers.agent_control_plane")',
    'print("IMPORT_OK")'
)
[System.IO.File]::WriteAllLines($ImportScript, $PyLines, [System.Text.UTF8Encoding]::new($false))

Push-Location $ProjectRoot
try {
    & $Python $ImportScript
    if ($LASTEXITCODE -ne 0) {
        throw "Import check failed."
    }
} finally {
    Pop-Location
    if (Test-Path $ImportScript) {
        Remove-Item $ImportScript -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Block 3 foundation compile/import checks passed." -ForegroundColor Green