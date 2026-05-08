param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    $Python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCmd) {
        throw "Python was not found in PATH and .venv\Scripts\python.exe does not exist."
    }
    $Python = $PythonCmd.Source
}

Write-Host "Using Python: $Python" -ForegroundColor Yellow

$Targets = @(
    "app\main.py",
    "app\assistant.py",
    "app\services\provider_resolver.py",
    "app\routers\multistep.py"
)

Write-Host "== Runtime compile check ==" -ForegroundColor Cyan
foreach ($Target in $Targets) {
    if (Test-Path $Target) {
        Write-Host "Checking $Target" -ForegroundColor DarkGray
        & $Python -m py_compile $Target
        if ($LASTEXITCODE -ne 0) {
            throw "py_compile failed for $Target with exit code $LASTEXITCODE"
        }
    }
}

Write-Host "== Import check ==" -ForegroundColor Cyan
$ImportScript = Join-Path $ProjectRoot "jarvis_block63_import_check.py"
$PyLines = @(
    'import importlib' ,
    'import sys' ,
    'print("PYTHONPATH_HEAD=", sys.path[0])' ,
    'importlib.import_module("app.main")' ,
    'importlib.import_module("app.routers.multistep")' ,
    'print("IMPORT_OK")'
)
[System.IO.File]::WriteAllLines($ImportScript, $PyLines, [System.Text.UTF8Encoding]::new($false))

Push-Location $ProjectRoot
try {
    & $Python $ImportScript
    if ($LASTEXITCODE -ne 0) {
        throw "Import check failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
    if (Test-Path $ImportScript) {
        Remove-Item $ImportScript -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "== Route probe ==" -ForegroundColor Cyan
try {
    $openapi = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/openapi.json" -TimeoutSec 3
    $paths = @($openapi.paths.PSObject.Properties.Name)
    if ($paths -contains "/api/missions/multistep/execute") {
        Write-Host "Route probe OK: /api/missions/multistep/execute" -ForegroundColor Green
    } else {
        Write-Host "Route probe warning: multistep route not visible in openapi." -ForegroundColor DarkYellow
    }
} catch {
    Write-Host "Route probe skipped: backend not running or openapi unavailable." -ForegroundColor DarkYellow
}

Write-Host "Compile and import checks passed." -ForegroundColor Green
