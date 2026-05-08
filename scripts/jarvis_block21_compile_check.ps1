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

$Targets = @(
  "app\main.py",
  "app\routers\multistep.py",
  "app\routers\resume_recovery.py",
  "app\routers\tools_runtime.py",
  "app\services\mission_resume_store.py",
  "app\services\tool_registry.py",
  "app\services\tool_executor_runtime.py"
)

Write-Host "Using Python: $Python" -ForegroundColor Yellow
Write-Host "== Block 2.1 compile check ==" -ForegroundColor Cyan
foreach ($Target in $Targets) {
  if (Test-Path $Target) {
    Write-Host "Checking $Target" -ForegroundColor DarkGray
    & $Python -m py_compile $Target
    if ($LASTEXITCODE -ne 0) { throw "py_compile failed for $Target" }
  }
}

$ImportScript = Join-Path $ProjectRoot "jarvis_block21_import_check.py"
$PyLines = @(
  'import importlib' ,
  'importlib.import_module("app.main")' ,
  'importlib.import_module("app.routers.multistep")' ,
  'importlib.import_module("app.routers.resume_recovery")' ,
  'importlib.import_module("app.routers.tools_runtime")' ,
  'importlib.import_module("app.services.tool_registry")' ,
  'importlib.import_module("app.services.tool_executor_runtime")' ,
  'print("IMPORT_OK")'
)
[System.IO.File]::WriteAllLines($ImportScript, $PyLines, [System.Text.UTF8Encoding]::new($false))
Push-Location $ProjectRoot
try {
  & $Python $ImportScript
  if ($LASTEXITCODE -ne 0) { throw "Import check failed." }
} finally {
  Pop-Location
  if (Test-Path $ImportScript) { Remove-Item $ImportScript -Force -ErrorAction SilentlyContinue }
}

Write-Host "Block 2.1 compile/import checks passed." -ForegroundColor Green
