param(
    [string]$ProjectRoot = (Get-Location).Path
)
. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisN8nContext -ProjectRoot $ProjectRoot

Push-Location $ctx.N8nRoot
try {
    & $ctx.DockerCli compose logs -f --tail 200
} finally {
    Pop-Location
}