$ErrorActionPreference = "Stop"

Write-Host "Creating virtual environment..."
python -m venv .venv

Write-Host "Activating virtual environment..."
& .\.venv\Scripts\Activate.ps1

Write-Host "Installing dependencies..."
pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created from .env.example"
}

Write-Host "Done. Start the API with:"
Write-Host "  scripts\start_api.bat"
Write-Host "And the worker with:"
Write-Host "  scripts\start_worker.bat"
