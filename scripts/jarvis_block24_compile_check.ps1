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

Write-Host "== Block 2.4 compile ==" -ForegroundColor Cyan

$Targets = @(
 "app\services\tool_router.py",
 "app\routers\tool_routing.py",
 "app\routers\multistep.py",
 "app\main.py"
)

foreach ($t in $Targets) {
    Write-Host "Checking $t" -ForegroundColor DarkGray
    & $Python -m py_compile $t
    if ($LASTEXITCODE -ne 0) { throw "Compile error in $t" }
}

$ImportScript = Join-Path $ProjectRoot "jarvis_block24_import_check.py"
$PyLines = @(
 'import importlib',
 'importlib.import_module("app.services.tool_router")',
 'importlib.import_module("app.routers.tool_routing")',
 'importlib.import_module("app.routers.multistep")',
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

Write-Host "Block 2.4 compile/import checks passed." -ForegroundColor Green