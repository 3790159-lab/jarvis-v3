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

$ContractsDir = Join-Path $ProjectRoot "contracts"
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"

$MissionContractFile = Join-Path $ContractsDir "mission_contract.json"
$TaskContractFile = Join-Path $ContractsDir "task_contract.json"
$StatusRulesFile = Join-Path $ContractsDir "status_transition_rules.json"
$ValidationFile = Join-Path $ArtifactsDir "phase4_contract_validation.json"

$targets = @($MissionContractFile, $TaskContractFile, $StatusRulesFile)
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

$report | ConvertTo-Json -Depth 10 | Set-Content -Path $ValidationFile -Encoding UTF8
Write-Host "Phase 4 contract validation complete."
$report | ConvertTo-Json -Depth 10
