param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8010
)

powershell -ExecutionPolicy Bypass -File ".\stop_8010_force.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not free port $Port" -ForegroundColor Red
    exit 1
}

Start-Sleep -Seconds 1

if (Test-Path ".\.venv\Scripts\python.exe") {
    & .\.venv\Scripts\python.exe -m uvicorn app.main:app --host $Host --port $Port
} else {
    python -m uvicorn app.main:app --host $Host --port $Port
}
