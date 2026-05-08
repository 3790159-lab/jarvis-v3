# Restart all Jarvis services gracefully
# Requires: Run as Administrator

param(
    [string]$Service = "all"  # "all", "backend", "bot", "watchdog"
)

$ErrorActionPreference = "Continue"

function Restart-JarvisService {
    param([string]$Name)
    $svc = Get-Service $Name -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "Restarting $Name..."
        Restart-Service $Name -Force
        Start-Sleep -Seconds 2
        $svc = Get-Service $Name
        Write-Host "  $Name: $($svc.Status)" -ForegroundColor $(if ($svc.Status -eq "Running") { "Green" } else { "Red" })
    } else {
        Write-Host "$Name not installed — skipping" -ForegroundColor Yellow
    }
}

switch ($Service.ToLower()) {
    "backend" { Restart-JarvisService "JarvisBackend" }
    "bot"     { Restart-JarvisService "JarvisBot" }
    "watchdog" { Restart-JarvisService "JarvisWatchdog" }
    default {
        Restart-JarvisService "JarvisWatchdog"
        Restart-JarvisService "JarvisBot"
        Start-Sleep -Seconds 3
        Restart-JarvisService "JarvisBackend"
        Start-Sleep -Seconds 8
        Start-Service JarvisBot -ErrorAction SilentlyContinue
        Start-Service JarvisWatchdog -ErrorAction SilentlyContinue
    }
}

Write-Host "`n=== Status ===" -ForegroundColor Cyan
Get-Service Jarvis* -ErrorAction SilentlyContinue | Format-Table Name, Status
