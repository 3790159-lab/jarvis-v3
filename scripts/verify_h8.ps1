# verify_h8.ps1 - Production verification for Block H8 stabilization
$ErrorActionPreference = "Continue"
$ok = 0
$warn = 0

Write-Host ""
Write-Host "=== Block H8 Production Verification ===" -ForegroundColor Cyan

# 1. Backend health
Write-Host ""
Write-Host "[1] Backend health..." -ForegroundColor Yellow
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8010/health" -TimeoutSec 5 -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        Write-Host "  OK  Backend: online (HTTP 200)" -ForegroundColor Green
        $ok++
    }
} catch {
    Write-Host "  WARN  Backend: offline or not started" -ForegroundColor Red
    $warn++
}

# 2. Bot heartbeat file
Write-Host ""
Write-Host "[2] Bot heartbeat..." -ForegroundColor Yellow
$hbFile = "state\bot_heartbeat.txt"
if (Test-Path $hbFile) {
    $ts = [int](Get-Content $hbFile)
    $now = [int]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
    $age = $now - $ts
    if ($age -lt 90) {
        Write-Host "  OK  Bot heartbeat: fresh ($age s ago)" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "  WARN  Bot heartbeat: stale ($age s ago)" -ForegroundColor Red
        $warn++
    }
} else {
    Write-Host "  WARN  Bot heartbeat file not found (bot not running)" -ForegroundColor Red
    $warn++
}

# 3. Night Autonomy schedule
Write-Host ""
Write-Host "[3] Night Autonomy schedule..." -ForegroundColor Yellow
$tasksFile = "state\scheduled_tasks.json"
if (Test-Path $tasksFile) {
    $tasks = Get-Content $tasksFile | ConvertFrom-Json
    $nightTasks = $tasks | Where-Object { $_.id -like "night_*" }
    if ($nightTasks -and $nightTasks.Count -ge 5) {
        Write-Host "  OK  Night Autonomy: $($nightTasks.Count) phases scheduled" -ForegroundColor Green
        $ok++
    } elseif ($nightTasks -and $nightTasks.Count -gt 0) {
        Write-Host "  WARN  Night Autonomy: only $($nightTasks.Count)/5 phases" -ForegroundColor Yellow
        $warn++
    } else {
        Write-Host "  WARN  Night Autonomy: no night_* tasks found" -ForegroundColor Red
        $warn++
    }
} else {
    Write-Host "  WARN  scheduled_tasks.json not found" -ForegroundColor Red
    $warn++
}

# 4. Watchdog functions
Write-Host ""
Write-Host "[4] Watchdog code..." -ForegroundColor Yellow
$wdContent = Get-Content "app\services\system_watchdog.py" -Raw
if ($wdContent -match "def check_bot_alive" -and $wdContent -match "def restart_bot_if_dead") {
    Write-Host "  OK  check_bot_alive + restart_bot_if_dead present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Watchdog functions missing" -ForegroundColor Red
    $warn++
}

# 5. Heartbeat thread
Write-Host ""
Write-Host "[5] Heartbeat thread code..." -ForegroundColor Yellow
$tgContent = Get-Content "tools\jarvis_smart_telegram_control.py" -Raw
if ($tgContent -match "_heartbeat_thread" -and $tgContent -match "_HEARTBEAT_FILE") {
    Write-Host "  OK  _HEARTBEAT_FILE + _heartbeat_thread defined" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Heartbeat code missing" -ForegroundColor Red
    $warn++
}

# 6. Anti-hallucination
Write-Host ""
Write-Host "[6] Anti-hallucination guard..." -ForegroundColor Yellow
$qaContent = Get-Content "app\services\quick_answer.py" -Raw
if ($qaContent -match "ANTI-HALLUCINATION" -or $qaContent -match "NEVER invent") {
    Write-Host "  OK  Anti-hallucination rules in system prompt" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Anti-hallucination guard missing" -ForegroundColor Red
    $warn++
}

# 7. Action commands
Write-Host ""
Write-Host "[7] Action commands..." -ForegroundColor Yellow
if ($tgContent -match "def cmd_restart_backend" -and $tgContent -match "def cmd_restart_bot") {
    Write-Host "  OK  cmd_restart_backend + cmd_restart_bot defined" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Action command functions missing" -ForegroundColor Red
    $warn++
}

# 8. Smart Router intents
Write-Host ""
Write-Host "[8] Smart Router intents..." -ForegroundColor Yellow
if ($tgContent -match "self_status" -and $tgContent -match "progress_report") {
    Write-Host "  OK  self_status + progress_report intents present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  New router intents missing" -ForegroundColor Red
    $warn++
}

# Summary
Write-Host ""
Write-Host "=== RESULT ===" -ForegroundColor Cyan
Write-Host "OK: $ok/8" -ForegroundColor Green
if ($warn -gt 0) {
    Write-Host "WARN/FAIL: $warn" -ForegroundColor Red
    Write-Host "Runtime checks need backend+bot running" -ForegroundColor Yellow
} else {
    Write-Host "All checks passed!" -ForegroundColor Green
}
