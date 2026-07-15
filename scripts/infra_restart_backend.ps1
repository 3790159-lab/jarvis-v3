# One-shot backend restart action for /infra_restart's [backend] button
# (DEV-12). No elevation needed -- the backend is a plain user-owned
# python.exe (uvicorn), not a Windows Service. Frees :8010 then relaunches it,
# mirroring backend_guardian_detached.ps1's Start-Backend step-for-step, kept
# standalone here (not dot-sourced) so the guardian script itself stays
# untouched. app.services.infra_control polls /health itself afterward.

param([int]$Port = 8010)
$ErrorActionPreference = 'Continue'
$Root = 'C:\jarvis'
$py   = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$entry  = Join-Path $Root 'scripts\run_backend_detached.py'
$logDir = Join-Path $Root 'state\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$bOut = Join-Path $logDir 'backend_boot.stdout.log'
$bErr = Join-Path $logDir 'backend_boot.stderr.log'

& (Join-Path $Root 'scripts\stop_port_owner.ps1') -Port $Port | Out-Null
Start-Sleep -Seconds 2
Start-Process -FilePath $py -ArgumentList @($entry) -WorkingDirectory $Root `
    -WindowStyle Hidden -RedirectStandardOutput $bOut -RedirectStandardError $bErr | Out-Null
