Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Import-SimpleEnv {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }

    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line.TrimStart().StartsWith("#")) { continue }

        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { continue }

        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1)
        $map[$key] = $value
    }

    return $map
}

function Get-JarvisContext {
    param([string]$ProjectRoot)

    $root = (Resolve-Path $ProjectRoot).Path
    $infra = Join-Path $root "infra\docker\n8n"
    $envPath = Join-Path $root ".env"

    return [pscustomobject]@{
        ProjectRoot = $root
        InfraRoot   = $infra
        EnvPath     = $envPath
        Env         = (Import-SimpleEnv $envPath)
    }
}