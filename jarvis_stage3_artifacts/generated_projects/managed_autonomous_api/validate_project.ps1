param(
    [string]$ProjectPath = ".",
    [int]$Port = 8011
)

$ErrorActionPreference = "Stop"

function Get-PythonExe {
    if (Test-Path ".\.venv\Scripts\python.exe") {
        return ".\.venv\Scripts\python.exe"
    }
    return "python"
}

function Test-PortInUse {
    param([int]$PortToCheck)
    $connections = Get-NetTCPConnection -LocalPort $PortToCheck -ErrorAction SilentlyContinue
    return $null -ne $connections
}

Write-Host "[VALIDATE] Project path: $ProjectPath"

if (!(Test-Path $ProjectPath)) {
    throw "Project path does not exist: $ProjectPath"
}

Set-Location $ProjectPath

$requiredFiles = @(
    ".\requirements.txt",
    ".\app\main.py",
    ".\app\runtime.py",
    ".\tests\test_api.py"
)

foreach ($file in $requiredFiles) {
    if (!(Test-Path $file)) {
        throw "Missing required file: $file"
    }
}

Write-Host "[VALIDATE] Required files OK"

$pythonExe = Get-PythonExe
Write-Host "[VALIDATE] Using Python: $pythonExe"

& $pythonExe -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed"
}

Write-Host "[VALIDATE] Tests OK"

if (Test-PortInUse -PortToCheck $Port) {
    throw "Port $Port is already in use"
}

$job = Start-Process -FilePath $pythonExe `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port" `
    -PassThru `
    -WindowStyle Hidden

Start-Sleep -Seconds 4

try {
    $health = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:$Port/health"
    $ready = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:$Port/ready"
    $diag = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:$Port/diagnostics"
}
finally {
    if ($job -and !$job.HasExited) {
        Stop-Process -Id $job.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
}

if ($health.status -ne "healthy") {
    throw "Health check failed"
}

if (($ready.status -ne "ready") -and ($ready.status -ne "not_ready")) {
    throw "Readiness check failed"
}

if ($diag.status -ne "ok") {
    throw "Diagnostics check failed"
}

Write-Host "[VALIDATE] Health OK"
Write-Host "[VALIDATE] Ready OK"
Write-Host "[VALIDATE] Diagnostics OK"
Write-Host "VALIDATION OK"
