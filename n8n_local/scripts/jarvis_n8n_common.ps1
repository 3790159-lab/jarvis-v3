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

function Get-DockerCliPath {
    $candidates = @()
    $cmd = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $candidates += (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe")
    $candidates += (Join-Path ${env:ProgramFiles(x86)} "Docker\Docker\resources\bin\docker.exe")

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    throw "docker.exe не найден. Проверь установку Docker Desktop."
}

function Get-JarvisN8nContext {
    param([string]$ProjectRoot)

    $root = (Resolve-Path $ProjectRoot).Path
    $n8nRoot = Join-Path $root "infra\docker\n8n"
    $projectEnvPath = Join-Path $root ".env"
    $n8nEnvPath = Join-Path $n8nRoot ".env"

    return [pscustomobject]@{
        ProjectRoot     = $root
        N8nRoot         = $n8nRoot
        ProjectEnvPath  = $projectEnvPath
        N8nEnvPath      = $n8nEnvPath
        ProjectEnv      = (Import-SimpleEnv $projectEnvPath)
        N8nEnv          = (Import-SimpleEnv $n8nEnvPath)
        DockerCli       = (Get-DockerCliPath)
    }
}