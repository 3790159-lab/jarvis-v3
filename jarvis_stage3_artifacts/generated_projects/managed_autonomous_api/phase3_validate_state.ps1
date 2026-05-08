[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path
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

$StateDir      = Join-Path $ProjectRoot "state"
$ArtifactsDir  = Join-Path $ProjectRoot "artifacts"
$ValidateFile  = Join-Path $ArtifactsDir "phase3_state_validation.json"

$targets = @(
    (Join-Path $StateDir "missions.json"),
    (Join-Path $StateDir "tasks.json"),
    (Join-Path $StateDir "agents.json"),
    (Join-Path $StateDir "queue.json"),
    (Join-Path $StateDir "runtime_config.json")
)

$results = @()

foreach ($file in $targets) {
    $row = [ordered]@{
        path = $file
        exists = (Test-Path $file)
        valid_json = $false
        error = $null
    }

    if ($row.exists) {
        try {
            $null = Get-Content -Path $file -Raw -Encoding UTF8 | ConvertFrom-Json
            $row.valid_json = $true
        }
        catch {
            $row.error = $_.Exception.Message
        }
    }

    $results += $row
}

$report = [ordered]@{
    created_at = (Get-Date).ToString("s")
    project_root = $ProjectRoot
    all_valid = (@($results | Where-Object { -not $_.exists -or -not $_.valid_json }).Count -eq 0)
    results = @($results)
}

$report | ConvertTo-Json -Depth 10 | Set-Content -Path $ValidateFile -Encoding UTF8
Write-Host "Phase 3 state validation complete."
$report | ConvertTo-Json -Depth 10
