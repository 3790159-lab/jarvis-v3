# Daily backup: copies state/ to state_backups/YYYY-MM-DD/
# Can be run manually or via Windows Task Scheduler at 3:00 AM

param(
    [int]$KeepDays = 7  # How many days of backups to keep
)

$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
$StateDir = "$ProjectDir\state"
$BackupsDir = "$ProjectDir\state_backups"
$Today = Get-Date -Format "yyyy-MM-dd"
$BackupPath = "$BackupsDir\$Today"

Write-Host "=== Jarvis Daily Backup ===" -ForegroundColor Cyan
Write-Host "Source: $StateDir"
Write-Host "Destination: $BackupPath"

# Create backups dir if needed
if (-not (Test-Path $BackupsDir)) {
    New-Item -ItemType Directory -Path $BackupsDir -Force | Out-Null
}

# Skip if today's backup already exists
if (Test-Path $BackupPath) {
    Write-Host "Today's backup already exists: $BackupPath" -ForegroundColor Yellow
    exit 0
}

# Copy state/ to today's backup
if (-not (Test-Path $StateDir)) {
    Write-Host "state/ dir not found: $StateDir" -ForegroundColor Red
    exit 1
}

try {
    Copy-Item -Path $StateDir -Destination $BackupPath -Recurse -Force
    $Size = (Get-ChildItem $BackupPath -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
    Write-Host "Backup created: $BackupPath ($([math]::Round($Size, 1))MB)" -ForegroundColor Green
} catch {
    Write-Error "Backup failed: $_"
    exit 1
}

# Cleanup old backups
$Cutoff = (Get-Date).AddDays(-$KeepDays)
$Deleted = 0
Get-ChildItem -Path $BackupsDir -Directory | ForEach-Object {
    try {
        $BackupDate = [datetime]::ParseExact($_.Name, "yyyy-MM-dd", $null)
        if ($BackupDate -lt $Cutoff) {
            Remove-Item $_.FullName -Recurse -Force
            Write-Host "Deleted old backup: $($_.Name)"
            $Deleted++
        }
    } catch {
        # Ignore non-date directories
    }
}

if ($Deleted -gt 0) {
    Write-Host "Cleaned up $Deleted old backup(s)" -ForegroundColor Yellow
}

Write-Host "Backup complete." -ForegroundColor Green

# ── Register as Task Scheduler (optional) ────────────────────────────────────
# Run: .\scripts\daily_backup.ps1 -RegisterTask
if ($args -contains "-RegisterTask") {
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NonInteractive -File `"$PSScriptRoot\daily_backup.ps1`""
    $Trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
    Register-ScheduledTask `
        -TaskName "JarvisDailyBackup" `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -RunLevel Highest `
        -Force
    Write-Host "Task 'JarvisDailyBackup' registered (runs at 3:00 AM)" -ForegroundColor Green
}
