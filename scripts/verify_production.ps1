# verify_production.ps1 - Production verification for Block I + F + L
$ErrorActionPreference = "Continue"
$ok = 0
$warn = 0
$total = 18

Write-Host ""
Write-Host "=== Jarvis Production Verification v3 (Block I+F+L) ===" -ForegroundColor Cyan

# 1. Backend health
Write-Host ""
Write-Host "[1/18] Backend health..." -ForegroundColor Yellow
try {
    $wc = [System.Net.WebClient]::new()
    $result = $wc.DownloadString("http://127.0.0.1:8010/health")
    if ($result -match "healthy") {
        Write-Host "  OK  Backend: online" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "  WARN  Backend: unexpected response: $result" -ForegroundColor Red
        $warn++
    }
} catch {
    Write-Host "  WARN  Backend: offline or not started" -ForegroundColor Red
    $warn++
}

# 2. Bot heartbeat file
Write-Host ""
Write-Host "[2/18] Bot heartbeat..." -ForegroundColor Yellow
$hbFile = "state\bot_heartbeat.txt"
if (Test-Path $hbFile) {
    $ts = 0
    $rawTs = Get-Content $hbFile -ErrorAction SilentlyContinue
    if ([int]::TryParse($rawTs, [ref]$ts)) {
        $now = [int]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
        $age = $now - $ts
        if ($age -lt 90) {
            Write-Host "  OK  Bot heartbeat: fresh ($age s ago)" -ForegroundColor Green
            $ok++
        } else {
            Write-Host "  WARN  Bot heartbeat: stale ($age s ago - bot may be down)" -ForegroundColor Red
            $warn++
        }
    } else {
        Write-Host "  WARN  Bot heartbeat: unreadable content" -ForegroundColor Red
        $warn++
    }
} else {
    Write-Host "  WARN  Bot heartbeat file not found (bot not running)" -ForegroundColor Red
    $warn++
}

# 3. Night Autonomy - exactly 5 tasks
Write-Host ""
Write-Host "[3/18] Night Autonomy (expect exactly 5 tasks)..." -ForegroundColor Yellow
$tasksFile = "state\scheduled_tasks.json"
if (Test-Path $tasksFile) {
    $tasks = Get-Content $tasksFile | ConvertFrom-Json
    $nightTasks = @($tasks | Where-Object { $_.action -like "night_*" })
    if ($nightTasks.Count -eq 5) {
        Write-Host "  OK  Night Autonomy: exactly 5 phases (no duplicates)" -ForegroundColor Green
        $ok++
    } elseif ($nightTasks.Count -gt 5) {
        Write-Host "  FAIL  Night Autonomy: $($nightTasks.Count) tasks -- DUPLICATES present!" -ForegroundColor Red
        $warn++
    } else {
        Write-Host "  WARN  Night Autonomy: $($nightTasks.Count)/5 phases scheduled" -ForegroundColor Yellow
        $warn++
    }
} else {
    Write-Host "  WARN  scheduled_tasks.json not found" -ForegroundColor Red
    $warn++
}

# 4. Watchdog functions
Write-Host ""
Write-Host "[4/18] Watchdog code..." -ForegroundColor Yellow
$wdContent = Get-Content "app\services\system_watchdog.py" -Raw -ErrorAction SilentlyContinue
if ($wdContent -and $wdContent -match "def check_bot_alive" -and $wdContent -match "def restart_bot_if_dead") {
    Write-Host "  OK  check_bot_alive + restart_bot_if_dead present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Watchdog functions missing" -ForegroundColor Red
    $warn++
}

# 5. Heartbeat thread
Write-Host ""
Write-Host "[5/18] Heartbeat thread code..." -ForegroundColor Yellow
$tgContent = Get-Content "tools\jarvis_smart_telegram_control.py" -Raw -ErrorAction SilentlyContinue
if ($tgContent -and $tgContent -match "_heartbeat_thread" -and $tgContent -match "_HEARTBEAT_FILE") {
    Write-Host "  OK  _HEARTBEAT_FILE + _heartbeat_thread defined" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Heartbeat code missing" -ForegroundColor Red
    $warn++
}

# 6. Anti-hallucination guard
Write-Host ""
Write-Host "[6/18] Anti-hallucination guard..." -ForegroundColor Yellow
$qaContent = Get-Content "app\services\quick_answer.py" -Raw -ErrorAction SilentlyContinue
if ($qaContent -and ($qaContent -match "ANTI-HALLUCINATION" -or $qaContent -match "NEVER invent")) {
    Write-Host "  OK  Anti-hallucination rules in system prompt" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Anti-hallucination guard missing in quick_answer.py" -ForegroundColor Red
    $warn++
}

# 7. Action commands
Write-Host ""
Write-Host "[7/18] Action commands..." -ForegroundColor Yellow
if ($tgContent -and $tgContent -match "def cmd_restart_backend" -and $tgContent -match "def cmd_restart_bot") {
    Write-Host "  OK  cmd_restart_backend + cmd_restart_bot defined" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Action command functions missing" -ForegroundColor Red
    $warn++
}

# 8. Smart Router intents
Write-Host ""
Write-Host "[8/18] Smart Router intents..." -ForegroundColor Yellow
if ($tgContent -and $tgContent -match "self_status" -and $tgContent -match "progress_report") {
    Write-Host "  OK  self_status + progress_report intents present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  New router intents missing" -ForegroundColor Red
    $warn++
}

# 9. PID lockfile
Write-Host ""
Write-Host "[9/18] PID lockfile (single-instance)..." -ForegroundColor Yellow
if ($tgContent -and $tgContent -match "_PID_FILE") {
    Write-Host "  OK  PID lockfile guard present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  PID lockfile not found in bot" -ForegroundColor Red
    $warn++
}

# 10. Russian dishes dict
Write-Host ""
Write-Host "[10/18] Russian dishes translation dict..." -ForegroundColor Yellow
$rmContent = Get-Content "app\services\restaurant_mode.py" -Raw -ErrorAction SilentlyContinue
if ($rmContent -and $rmContent -match "RUSSIAN_DISHES_EN" -and $rmContent -match "borscht") {
    Write-Host "  OK  RUSSIAN_DISHES_EN dict with borscht translation present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Russian dishes translation dict missing" -ForegroundColor Red
    $warn++
}

# 11. Block L: block_l_common.py
Write-Host ""
Write-Host "[11/18] Block L: block_l_common.py..." -ForegroundColor Yellow
if (Test-Path "app\services\block_l_common.py") {
    $blContent = Get-Content "app\services\block_l_common.py" -Raw
    if ($blContent -match "claude_api_call" -and $blContent -match "save_json_safe") {
        Write-Host "  OK  block_l_common.py with claude_api_call + save_json_safe" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "  WARN  block_l_common.py missing key functions" -ForegroundColor Yellow
        $warn++
    }
} else {
    Write-Host "  FAIL  block_l_common.py not found" -ForegroundColor Red
    $warn++
}

# 12. Block L: figma_brief_generator.py
Write-Host ""
Write-Host "[12/18] Block L: figma_brief_generator.py..." -ForegroundColor Yellow
if (Test-Path "app\services\figma_brief_generator.py") {
    Write-Host "  OK  figma_brief_generator.py present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  figma_brief_generator.py not found" -ForegroundColor Red
    $warn++
}

# 13. Block L: app_spec_generator.py + bolt_diy_health.py
Write-Host ""
Write-Host "[13/18] Block L: bolt.diy services..." -ForegroundColor Yellow
if ((Test-Path "app\services\app_spec_generator.py") -and (Test-Path "app\services\bolt_diy_health.py")) {
    Write-Host "  OK  app_spec_generator.py + bolt_diy_health.py present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  bolt.diy services missing" -ForegroundColor Red
    $warn++
}

# 14. Block L: smart_prompts.py
Write-Host ""
Write-Host "[14/18] Block L: smart_prompts.py..." -ForegroundColor Yellow
if (Test-Path "app\services\smart_prompts.py") {
    $spContent = Get-Content "app\services\smart_prompts.py" -Raw
    if ($spContent -match "smart_enhance" -and $spContent -match "detect_category") {
        Write-Host "  OK  smart_prompts.py with smart_enhance + detect_category" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "  WARN  smart_prompts.py missing key functions" -ForegroundColor Yellow
        $warn++
    }
} else {
    Write-Host "  FAIL  smart_prompts.py not found" -ForegroundColor Red
    $warn++
}

# 15. Block L: landing_brief_session.py + landing_generator_v2.py
Write-Host ""
Write-Host "[15/18] Block L: Landing Brief 2.0..." -ForegroundColor Yellow
if ((Test-Path "app\services\landing_brief_session.py") -and (Test-Path "app\services\landing_generator_v2.py")) {
    Write-Host "  OK  landing_brief_session.py + landing_generator_v2.py present" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  FAIL  Landing Brief 2.0 services missing" -ForegroundColor Red
    $warn++
}

# 16. Block L docs present
Write-Host ""
Write-Host "[16/18] Block L docs..." -ForegroundColor Yellow
$docsOk = (Test-Path "docs\block_l\FIGMA_MCP_GUIDE.md") -and
          (Test-Path "docs\block_l\BOLT_DIY_GUIDE.md") -and
          (Test-Path "docs\block_l\LANDING_BRIEF_GUIDE.md")
if ($docsOk) {
    Write-Host "  OK  All Block L docs present (3 guides)" -ForegroundColor Green
    $ok++
} else {
    Write-Host "  WARN  Some Block L docs missing" -ForegroundColor Yellow
    $warn++
}

# 17. bolt.diy health (optional)
Write-Host ""
Write-Host "[17/18] bolt.diy health (optional)..." -ForegroundColor Yellow
try {
    $wc2 = [System.Net.WebClient]::new()
    $wc2.Headers.Add("User-Agent", "Mozilla/5.0")
    $boltResult = $wc2.DownloadString("http://localhost:5173/")
    if ($boltResult.Length -gt 100) {
        Write-Host "  OK  bolt.diy running at localhost:5173" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "  WARN  bolt.diy: unexpected response" -ForegroundColor Yellow
        $warn++
    }
} catch {
    Write-Host "  WARN  bolt.diy not running (optional -- start with: pnpm run dev)" -ForegroundColor Yellow
    $warn++
}

# 18. All tests passing
Write-Host ""
Write-Host "[18/18] Test suite..." -ForegroundColor Yellow
$testOutput = python -m pytest tests/ -q --tb=no 2>&1
$lastLine = ($testOutput | Select-String "passed|failed|error" | Select-Object -Last 1).ToString()
if ($lastLine -match "(\d+) passed") {
    $passedCount = [int]$Matches[1]
    if ($lastLine -match "failed") {
        $failedMatch = [regex]::Match($lastLine, "(\d+) failed")
        $failedCount = if ($failedMatch.Success) { [int]$failedMatch.Groups[1].Value } else { "?" }
        Write-Host "  WARN  Tests: $passedCount passed, $failedCount failed (check pre-existing failures)" -ForegroundColor Yellow
        $warn++
    } else {
        Write-Host "  OK  Tests: $passedCount passed" -ForegroundColor Green
        $ok++
    }
} else {
    Write-Host "  WARN  Could not determine test results: $lastLine" -ForegroundColor Yellow
    $warn++
}

# Summary
Write-Host ""
Write-Host "=== RESULT ===" -ForegroundColor Cyan
Write-Host "OK: $ok / $total" -ForegroundColor Green
if ($warn -gt 0) {
    Write-Host "WARN/FAIL: $warn" -ForegroundColor Red
    if ($ok -ge 15) {
        Write-Host "Status: PRODUCTION READY (minor issues)" -ForegroundColor Yellow
    } elseif ($ok -ge 12) {
        Write-Host "Status: MOSTLY READY" -ForegroundColor Yellow
    } else {
        Write-Host "Status: NEEDS ATTENTION" -ForegroundColor Red
    }
} else {
    Write-Host "All $total checks passed!" -ForegroundColor Green
    Write-Host "Status: FULLY VERIFIED" -ForegroundColor Green
}
Write-Host ""
Write-Host "Runtime checks (1, 2, 17) need backend+bot+bolt.diy running to pass." -ForegroundColor Gray
