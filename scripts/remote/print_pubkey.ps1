# scripts/remote/print_pubkey.ps1
# Print the laptop's SSH public key for copy-paste into the desktop's
# authorized_keys (and administrators_authorized_keys, if desktop user
# is an admin).
#
# Usage on the laptop:
#   C:\jarvis\scripts\remote\print_pubkey.ps1
#   C:\jarvis\scripts\remote\print_pubkey.ps1 -Key jarvis_desktop

param(
    [string] $Key = "jarvis_desktop"
)

$ErrorActionPreference = "Stop"

$privatePath = Join-Path $env:USERPROFILE ".ssh\$Key"
$publicPath  = "$privatePath.pub"

if (-not (Test-Path $publicPath)) {
    Write-Host "Public key not found: $publicPath" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Generate one with:" -ForegroundColor Cyan
    Write-Host "  ssh-keygen -t ed25519 -f `$env:USERPROFILE\.ssh\$Key -C `"`$env:USERNAME`@`$env:COMPUTERNAME`""
    exit 1
}

Write-Host "Public key: $publicPath" -ForegroundColor Cyan
Write-Host ""
Write-Host "----- BEGIN COPY -----"
Get-Content $publicPath
Write-Host "----- END COPY -----"
Write-Host ""
Write-Host "On desktop, append the line above to:" -ForegroundColor Cyan
Write-Host "  C:\Users\Admin\.ssh\authorized_keys"
Write-Host "If desktop user is an administrator, ALSO append to:" -ForegroundColor Cyan
Write-Host "  C:\ProgramData\ssh\administrators_authorized_keys"
Write-Host "  (and lock the ACL — see REMOTE_ACCESS.md Section F)"
