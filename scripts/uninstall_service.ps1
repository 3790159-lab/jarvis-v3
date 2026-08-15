# Jarvis Windows Service Uninstaller
# Requires: Run as Administrator

$ErrorActionPreference = "Continue"
$NssmExe = "$PSScriptRoot\..\nssm\nssm.exe"
$Services = @("JarvisWatchdog", "JarvisBot", "JarvisBackend")

Write-Host "=== Jarvis Service Uninstaller ===" -ForegroundColor Yellow

foreach ($svc in $Services) {
    $existing = Get-Service $svc -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Stopping $svc..."
        Stop-Service $svc -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2

        if (Test-Path $NssmExe) {
            & $NssmExe remove $svc confirm
        } else {
            sc.exe delete $svc
        }
        Write-Host "$svc removed" -ForegroundColor Green
    } else {
        Write-Host "$svc not found — skipping"
    }
}

Write-Host "`nAll Jarvis services removed." -ForegroundColor Green
