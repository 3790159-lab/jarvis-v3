[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path,
    [int]$Port = 8010,
    [int]$Attempts = 12,
    [int]$DelaySeconds = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-AbsolutePath {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return (Get-Location).Path
    }

    try {
        return [System.IO.Path]::GetFullPath((Resolve-Path -Path $PathValue).Path)
    }
    catch {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
}

$ProjectRoot = Resolve-AbsolutePath -PathValue $ProjectRoot
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"
$SmokeFile = Join-Path $ArtifactsDir ("phase3_smoke_{0}.json" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

Ensure-Dir -Path $ArtifactsDir

$results = @()

for ($i = 1; $i -le $Attempts; $i++) {
    $row = [ordered]@{
        attempt = $i
        checked_at = (Get-Date).ToString("s")
        ok = $false
        status = $null
        pid = $null
        uptime_seconds = $null
        error = $null
    }

    try {
        $health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 5
        $row.ok = $true
        if ($health.PSObject.Properties.Name -contains "status") {
            $row.status = $health.status
        }
        if ($health.PSObject.Properties.Name -contains "pid") {
            $row.pid = [int]$health.pid
        }
        if ($health.PSObject.Properties.Name -contains "uptime_seconds") {
            $row.uptime_seconds = $health.uptime_seconds
        }
    }
    catch {
        $row.error = $_.Exception.Message
    }

    $results += $row

    if ($i -lt $Attempts) {
        Start-Sleep -Seconds $DelaySeconds
    }
}

$report = [ordered]@{
    created_at = (Get-Date).ToString("s")
    project_root = $ProjectRoot
    port = $Port
    attempts = $Attempts
    delay_seconds = $DelaySeconds
    success_count = @($results | Where-Object { $_.ok }).Count
    failure_count = @($results | Where-Object { -not $_.ok }).Count
    results = @($results)
}

$report | ConvertTo-Json -Depth 20 | Set-Content -Path $SmokeFile -Encoding UTF8
Write-Host "Phase 3 smoke test complete."
$report | ConvertTo-Json -Depth 10
