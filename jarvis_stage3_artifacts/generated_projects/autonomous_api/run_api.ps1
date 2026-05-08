$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path .\.venv\Scripts\Activate.ps1) { . .\.venv\Scripts\Activate.ps1 }
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
