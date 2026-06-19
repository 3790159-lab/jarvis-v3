# Post-reboot / anytime health check for the Jarvis persistence layer.
# Read-only: starts/kills nothing. Run after a reboot to confirm everything
# came back up on its own.  Usage:  powershell -File scripts\verify_jarvis_health.ps1

$ErrorActionPreference = 'Continue'
$Root = 'C:\jarvis'
$ok = $true
function Say($cond,$label,$detail){ $m = if($cond){'  OK '}else{$script:ok=$false;'FAIL'}; Write-Host ("[{0}] {1}{2}" -f $m,$label,$(if($detail){" - $detail"}else{''})) }

Write-Host "=== Jarvis health ==="
$os = Get-CimInstance Win32_OperatingSystem
$up = (Get-Date) - $os.LastBootUpTime
Write-Host ("Last boot: {0}  (uptime {1:dd}d {1:hh}h {1:mm}m)" -f $os.LastBootUpTime,$up)

# --- scheduled tasks ---
foreach($n in 'JarvisBackendGuardian','JarvisBotGuardian'){
  $t = Get-ScheduledTask -TaskName $n -EA SilentlyContinue
  Say ($t -and $t.State -eq 'Running') "$n task Running" $(if($t){$t.State}else{'missing'})
}
$sn = Get-ScheduledTask -TaskName JarvisSniperDetached -EA SilentlyContinue
Say ($sn -and $sn.State -ne 'Running') "Sniper frozen (not Running)" $(if($sn){$sn.State}else{'absent'})

# --- backend ---
try { $h = Invoke-RestMethod -Uri 'http://127.0.0.1:8010/health' -TimeoutSec 4; Say ($h.status -eq 'healthy') 'Backend /health' $h.status }
catch { Say $false 'Backend /health' 'unreachable' }

# --- bot: process + fresh heartbeat ---
$botProc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -EA SilentlyContinue | Where-Object { $_.CommandLine -match 'jarvis_smart_telegram_control' }
$hbAge = $null
$hbPath = Join-Path $Root 'state\bot_heartbeat.txt'
if(Test-Path $hbPath){ try { $last=[int64]((Get-Content $hbPath|Select-Object -First 1).Trim()); $hbAge=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-$last } catch {} }
Say ([bool]$botProc) 'Bot process running' $(if($botProc){"PID $(@($botProc)[0].ProcessId)"}else{'none'})
Say ($hbAge -ne $null -and $hbAge -le 90) 'Bot heartbeat fresh' $(if($hbAge -ne $null){"${hbAge}s"}else{'no heartbeat'})

# --- ownership: guardians parented to services.exe/svchost, not a user shell ---
foreach($pat in @('backend_guardian_detached','bot_guardian_detached')){
  $g = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -EA SilentlyContinue | Where-Object { $_.CommandLine -match ('-File.*'+$pat) }
  $par = if($g){ (Get-CimInstance Win32_Process -Filter ("ProcessId="+@($g)[0].ParentProcessId) -EA SilentlyContinue).Name }
  Say ($par -match 'svchost|services') "$pat session-independent" "parent=$par"
}

# --- remote access ---
foreach($s in 'sshd','cloudflared'){ $svc=Get-Service -Name $s -EA SilentlyContinue; Say ($svc -and $svc.Status -eq 'Running') "$s running" $(if($svc){$svc.Status}else{'absent'}) }

Write-Host ""
if($ok){ Write-Host 'RESULT: ALL GREEN' -ForegroundColor Green } else { Write-Host 'RESULT: SOME CHECKS FAILED (see above)' -ForegroundColor Yellow }
