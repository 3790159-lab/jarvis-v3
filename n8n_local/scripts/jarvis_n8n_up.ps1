param(
    [string]$ProjectRoot = (Get-Location).Path,
    [switch]$ForceRecreate
)
. "$PSScriptRoot\jarvis_n8n_common.ps1"
$ctx = Get-JarvisN8nContext -ProjectRoot $ProjectRoot

Push-Location $ctx.N8nRoot
try {
    if ($ForceRecreate) {
        & $ctx.DockerCli compose up -d --force-recreate --remove-orphans
    } else {
        & $ctx.DockerCli compose up -d
    }
} finally {
    Pop-Location
}