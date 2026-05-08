param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectPath
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

function Remove-PythonCaches {
    param([string]$RootPath)
    Get-ChildItem -LiteralPath $RootPath -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @("__pycache__", ".pytest_cache") } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    Get-ChildItem -LiteralPath $RootPath -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
}

$fullProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$venvPython = Join-Path $fullProjectPath ".venv\Scripts\python.exe"
$testFile = Join-Path $fullProjectPath "tests\test_health.py"

if (!(Test-Path -LiteralPath $venvPython)) { throw "Venv python not found: $venvPython" }
if (!(Test-Path -LiteralPath $testFile)) { throw "Test file not found: $testFile" }

Write-Step "Project: $fullProjectPath"
Set-Location -LiteralPath $fullProjectPath

Write-Step "Cleaning caches"
Remove-PythonCaches -RootPath $fullProjectPath

Write-Step "Running isolated tests"
$testOutput = & $venvPython -m pytest tests 2>&1 | Out-String

if ($LASTEXITCODE -eq 0) {
    Write-Step "Tests already passing"
    exit 0
}

Write-Step "Tests failed, analyzing output"

if ($testOutput -match "ModuleNotFoundError: No module named 'app'") {
    Write-Step "Detected missing app import path. Applying fix."

    $fix = @(
        "import sys",
        "from pathlib import Path",
        "",
        "ROOT = Path(__file__).resolve().parents[1]",
        "if str(ROOT) not in sys.path:",
        "    sys.path.insert(0, str(ROOT))",
        "",
        "from fastapi.testclient import TestClient",
        "from app.main import app",
        "",
        "client = TestClient(app)",
        "",
        "def test_health():",
        "    response = client.get('/health')",
        "    assert response.status_code == 200",
        "    body = response.json()",
        "    assert body['status'] == 'healthy'",
        "    assert 'service' in body"
    ) -join "`r`n"

    Set-Content -LiteralPath $testFile -Value $fix -Encoding UTF8

    Write-Step "Retrying isolated tests"
    $testOutput = & $venvPython -m pytest tests 2>&1 | Out-String

    if ($LASTEXITCODE -eq 0) {
        Write-Step "RECOVERY SUCCESSFUL"
        exit 0
    }
}

Write-Host "RECOVERY FAILED"
Write-Host $testOutput
exit 1
