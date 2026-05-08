param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

function Get-Json {
    param(
        [Parameter(Mandatory=$true)][string]$Uri
    )
    Invoke-RestMethod -Method GET -Uri $Uri -ContentType "application/json"
}

function Post-Json {
    param(
        [Parameter(Mandatory=$true)][string]$Uri,
        [Parameter(Mandatory=$true)][object]$Body
    )
    $json = $Body | ConvertTo-Json -Depth 20
    Invoke-RestMethod -Method POST -Uri $Uri -Body $json -ContentType "application/json"
}

Write-Host "== AI health ==" -ForegroundColor Cyan
$aiHealth = Get-Json -Uri "$BaseUrl/api/ai/health"
$aiHealth | ConvertTo-Json -Depth 10

$configuredProviders = @(
    $aiHealth.providers |
    Where-Object { $_.enabled -eq $true -and $_.configured -eq $true } |
    ForEach-Object { $_.provider }
)

if (-not $configuredProviders -or $configuredProviders.Count -eq 0) {
    throw "No configured AI providers found."
}

$reasoningProvider = if ($configuredProviders -contains "anthropic") {
    "anthropic"
} elseif ($configuredProviders -contains "openai") {
    "openai"
} else {
    $configuredProviders[0]
}

$codingProvider = if ($configuredProviders -contains "openai") {
    "openai"
} elseif ($configuredProviders -contains "anthropic") {
    "anthropic"
} else {
    $configuredProviders[0]
}

Write-Host ""
Write-Host "Resolved providers:" -ForegroundColor Yellow
Write-Host " - reasoning: $reasoningProvider"
Write-Host " - coding:    $codingProvider"

$missionId = "mission_multistep_" + ([guid]::NewGuid().ToString("N").Substring(0, 8))

$payload = @{
    mission_id = $missionId
    objective  = "Test multi-step execution across planning, coding, and validation"
    steps      = @(
        @{
            step_id            = "plan"
            title              = "Plan execution approach"
            description        = "Create a concise execution plan for the requested objective."
            task_type          = "reasoning"
            preferred_provider = $reasoningProvider
            metadata           = @{ phase = "plan" }
        },
        @{
            step_id            = "execute"
            title              = "Execute core implementation"
            description        = "Create a Python retry helper, then validate its quality and possible improvements."
            task_type          = "coding"
            preferred_provider = $codingProvider
            metadata           = @{ phase = "execute" }
        },
        @{
            step_id            = "validate"
            title              = "Validate execution result"
            description        = "Review the produced result, identify risks, and suggest final improvements."
            task_type          = "reasoning"
            preferred_provider = $reasoningProvider
            metadata           = @{ phase = "validate" }
        }
    )
}

Write-Host ""
Write-Host "== Discover endpoint from OpenAPI ==" -ForegroundColor Cyan

$discoveredUris = @()
try {
    $openapi = Get-Json -Uri "$BaseUrl/openapi.json"
    foreach ($p in $openapi.paths.PSObject.Properties) {
        $path = $p.Name
        $meta = $p.Value
        $hasPost = $null -ne $meta.post
        if ($hasPost -and (
            $path -match 'multi' -or
            $path -match 'step' -or
            $path -match 'mission' -or
            $path -match 'autonomy'
        )) {
            $discoveredUris += "$BaseUrl$path"
        }
    }
} catch {
    Write-Host "OpenAPI discovery failed, fallback to static candidates." -ForegroundColor DarkYellow
}

$possibleUris = @()
$possibleUris += $discoveredUris
$possibleUris += @(
    "$BaseUrl/api/missions/multistep/execute",
    "$BaseUrl/api/missions/multi-step/execute",
    "$BaseUrl/api/autonomy/missions/multistep/execute",
    "$BaseUrl/api/autonomy/multi-step/execute"
)
$possibleUris = $possibleUris | Select-Object -Unique

Write-Host "Candidate endpoints:" -ForegroundColor Yellow
$possibleUris | ForEach-Object { Write-Host " - $_" }

Write-Host ""
Write-Host "== Submit multi-step mission ==" -ForegroundColor Cyan

$result = $null
$lastError = $null
$workingUri = $null

foreach ($uri in $possibleUris) {
    try {
        Write-Host "Trying: $uri" -ForegroundColor DarkGray
        $result = Post-Json -Uri $uri -Body $payload
        if ($null -ne $result) {
            $workingUri = $uri
            break
        }
    } catch {
        $lastError = $_
    }
}

if ($null -eq $result) {
    throw "Smoke test failed to find a working multi-step endpoint. Last error: $lastError"
}

Write-Host ""
Write-Host "Working endpoint: $workingUri" -ForegroundColor Green
Write-Host "== Smoke result ==" -ForegroundColor Cyan
$result | ConvertTo-Json -Depth 20

$allSteps = @($result.steps)
$failedSteps = @($allSteps | Where-Object { $_.status -eq "failed" })

if ($result.ok -eq $true -or $failedSteps.Count -eq 0) {
    Write-Host ""
    Write-Host "Block 6.3 smoke test passed." -ForegroundColor Green
} else {
    throw "Block 6.3 smoke test failed. Failed steps: $($failedSteps.Count)"
}