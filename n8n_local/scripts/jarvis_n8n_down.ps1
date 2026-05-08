param(
    [string]$ProjectRoot = (Get-Location).Path,
    [switch]$RemoveData
)
. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisN8nContext -ProjectRoot $ProjectRoot

Push-Location $ctx.N8nRoot
try {
    if ($RemoveData) {
        & $ctx.DockerCli compose down -v --remove-orphans
    } else {
        & $ctx.DockerCli compose down --remove-orphans
    }
} finally {
    Pop-Location
}