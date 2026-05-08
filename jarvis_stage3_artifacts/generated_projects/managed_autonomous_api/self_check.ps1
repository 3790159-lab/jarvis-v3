$ErrorActionPreference = "Continue"

Write-Host "=== SELF CHECK ==="

Write-Host "`n[1] Current directory"
Get-Location

Write-Host "`n[2] Python version"
python --version

Write-Host "`n[3] Virtual environment"
if (Test-Path ".\.venv\Scripts\python.exe") {
    Write-Host "Venv OK"
} else {
    Write-Host "Venv missing"
}

Write-Host "`n[4] Required files"
$files = @(
    ".\requirements.txt",
    ".\app\main.py",
    ".\app\runtime.py",
    ".\tests\test_api.py",
    ".\project_control.ps1",
    ".\validate_project.ps1"
)

foreach ($file in $files) {
    if (Test-Path $file) {
        Write-Host "[OK] $file"
    } else {
        Write-Host "[MISS] $file"
    }
}

Write-Host "`n[5] API health on 8010"
try {
    $health = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8010/health" -TimeoutSec 3
    $health | ConvertTo-Json -Depth 10
}
catch {
    Write-Host "API not responding on port 8010"
}

Write-Host "`n[6] Git status"
git status --short
