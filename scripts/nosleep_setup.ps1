# Prevent Windows from sleeping while Jarvis services are running
# Requires: Run as Administrator

Write-Host "=== Jarvis NoSleep Setup ===" -ForegroundColor Cyan

# AC power: disable sleep and hibernate, keep monitor timeout at 30 min
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 30

# DC power (on battery): allow normal sleep to preserve battery
# Only disable sleep while on AC power (server scenario)

Write-Host "Sleep disabled on AC power" -ForegroundColor Green
Write-Host "Monitor timeout: 30 minutes (can still turn off screen)"
Write-Host ""
Write-Host "Current power settings:"
powercfg /query SCHEME_CURRENT SUB_SLEEP | Select-String "Current AC"

Write-Host ""
Write-Host "To restore default sleep settings:"
Write-Host "  powercfg /change standby-timeout-ac 30"
Write-Host "  powercfg /change hibernate-timeout-ac 180"
