param([string]$ProjectRoot = (Get-Location).Path)
. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisContext -ProjectRoot $ProjectRoot
Push-Location $ctx.InfraRoot
try {
    docker compose down
} finally {
    Pop-Location
}