param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (!(Test-Path $PyExe)) {
    throw "Python venv executable not found: $PyExe"
}

$env:PYTHONPATH = $ProjectRoot

$TempDir = [System.IO.Path]::GetTempPath()
if ([string]::IsNullOrWhiteSpace($TempDir)) {
    throw "System temp path is empty"
}

$TempPy = Join-Path $TempDir "jarvis_n8n_smoke.py"

$PyCode = @"
from app.services.n8n_client import N8nClient
import json
import sys

try:
    client = N8nClient()
    workflows = client.list_workflows()
    webhook = client.trigger_webhook(
        {
            "action": "health_check",
            "source": "jarvis_smoke",
            "message": "smoke test",
        }
    )

    payload = {
        "status": "ok",
        "workflows_probe": workflows,
        "webhook_probe": webhook,
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
except Exception as exc:
    print(json.dumps({
        "status": "error",
        "error": str(exc)
    }, ensure_ascii=False, indent=2))
    sys.exit(1)
"@

[System.IO.File]::WriteAllText(
    $TempPy,
    $PyCode,
    [System.Text.UTF8Encoding]::new($false)
)

& $PyExe $TempPy
if ($LASTEXITCODE -ne 0) {
    throw "jarvis_n8n_smoke failed"
}

Write-Host ""
Write-Host "jarvis_n8n_smoke passed." -ForegroundColor Green