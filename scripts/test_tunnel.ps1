# Test Cloudflare Tunnel connectivity for Jarvis
# Run after setup_cloudflare_tunnel.ps1 to verify everything works
#
# Usage:
#   .\scripts\test_tunnel.ps1 -Domain jarvis.yourdomain.com

param(
    [string]$Domain = "",
    [string]$LocalBackend = "http://127.0.0.1:8010"
)

$ErrorActionPreference = "Continue"
$OK = 0
$FAIL = 0

function Test-Endpoint {
    param([string]$Label, [string]$Url, [string]$ExpectContains = "")
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        if ($ExpectContains -and $resp.Content -notmatch $ExpectContains) {
            Write-Host "  WARN $Label — body missing '$ExpectContains'" -ForegroundColor Yellow
            $script:FAIL++
        } else {
            Write-Host "  OK   $Label ($($resp.StatusCode))" -ForegroundColor Green
            $script:OK++
        }
    } catch {
        Write-Host "  FAIL $Label — $_" -ForegroundColor Red
        $script:FAIL++
    }
}

Write-Host "=== Jarvis Tunnel Connectivity Test ===" -ForegroundColor Cyan
Write-Host ""

# 1. Local backend
Write-Host "[1] Local backend health" -ForegroundColor Yellow
Test-Endpoint "Local /health" "$LocalBackend/health" "healthy"

# 2. Cloudflared service
Write-Host ""
Write-Host "[2] Cloudflared service" -ForegroundColor Yellow
$svc = Get-Service "cloudflared" -ErrorAction SilentlyContinue
if (-not $svc) { $svc = Get-Service "Jarvis-cloudflared" -ErrorAction SilentlyContinue }
if ($svc) {
    if ($svc.Status -eq "Running") {
        Write-Host "  OK   Service $($svc.Name) is Running" -ForegroundColor Green
        $OK++
    } else {
        Write-Host "  FAIL Service $($svc.Name) is $($svc.Status)" -ForegroundColor Red
        $FAIL++
    }
} else {
    Write-Host "  WARN cloudflared service not found (may be running manually)" -ForegroundColor Yellow
}

# 3. Public domain
if ($Domain) {
    Write-Host ""
    Write-Host "[3] Public domain: https://$Domain" -ForegroundColor Yellow
    Test-Endpoint "Public /health" "https://$Domain/health" "healthy"
    Test-Endpoint "Public /telegram/webhook/status" "https://$Domain/telegram/webhook/status" "ok"
    Test-Endpoint "Public /api/jarvis/image/health" "https://$Domain/api/jarvis/image/health" "ok"
} else {
    Write-Host ""
    Write-Host "[3] Skipped — pass -Domain jarvis.yourdomain.com to test public endpoint" -ForegroundColor Gray
}

# 4. Webhook queue
Write-Host ""
Write-Host "[4] Webhook queue file" -ForegroundColor Yellow
$queuePath = Join-Path (Resolve-Path "$PSScriptRoot\..").Path "state\webhook_queue.jsonl"
if (Test-Path $queuePath) {
    $lines = (Get-Content $queuePath | Measure-Object -Line).Lines
    Write-Host "  OK   webhook_queue.jsonl exists ($lines lines)" -ForegroundColor Green
    $OK++
} else {
    Write-Host "  INFO webhook_queue.jsonl not yet created (normal on first run)" -ForegroundColor Gray
}

# Summary
Write-Host ""
Write-Host "=== Results: $OK OK, $FAIL FAIL ===" -ForegroundColor $(if ($FAIL -eq 0) { "Green" } else { "Red" })

if ($FAIL -gt 0) {
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "  - Start backend: uvicorn app.main:app --port 8010"
    Write-Host "  - Check tunnel:  .\scripts\setup_cloudflare_tunnel.ps1 -Status"
    Write-Host "  - Re-run setup:  .\scripts\setup_cloudflare_tunnel.ps1 -Domain $Domain"
}
