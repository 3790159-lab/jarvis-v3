param(
    [string]$Name = "auto_api"
)

$ErrorActionPreference = "Stop"

Write-Host "STEP 1: Creating project..."
python .\tools\jarvis_real_tools.py scaffold-fastapi --name $Name

$projectPath = ".\jarvis_stage3_artifacts\generated_projects\$Name"
Set-Location $projectPath

Write-Host "STEP 2: Creating venv..."
python -m venv .venv

Write-Host "STEP 3: Activating venv..."
.\.venv\Scripts\Activate.ps1

Write-Host "STEP 4: Installing dependencies..."
pip install -r requirements.txt

Write-Host "STEP 5: Running tests..."
pytest -q

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ TEST FAILED"
    exit 1
}

Write-Host "✅ PROJECT READY AND WORKING"
