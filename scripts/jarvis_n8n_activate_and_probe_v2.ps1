param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

function Read-EnvValue {
    param(
        [string]$Path,
        [string]$Key
    )

    if (!(Test-Path $Path)) { return $null }

    $content = Get-Content $Path -Raw -ErrorAction SilentlyContinue
    if ([string]::IsNullOrWhiteSpace($content)) { return $null }

    $escapedKey = [regex]::Escape($Key)
    $m = [regex]::Match($content, "(?m)^$escapedKey=(.*)$")
    if ($m.Success) { return $m.Groups[1].Value.Trim() }

    return $null
}

function Get-ResponseObject {
    param($ExceptionObject)

    if ($null -eq $ExceptionObject) { return $null }

    if ($ExceptionObject.PSObject.Properties.Name -contains "Response") {
        return $ExceptionObject.Response
    }

    if ($ExceptionObject.PSObject.Properties.Name -contains "InnerException") {
        $inner = $ExceptionObject.InnerException
        if ($null -ne $inner -and $inner.PSObject.Properties.Name -contains "Response") {
            return $inner.Response
        }
    }

    return $null
}

function Invoke-ProbeRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Headers = $null,
        [AllowNull()][string]$Body = $null
    )

    $result = [ordered]@{
        name         = $Name
        ok           = $false
        url          = $Url
        status_code  = $null
        content_type = $null
        body_preview = $null
        error        = $null
    }

    try {
        $params = @{
            Method      = $Method
            Uri         = $Url
            ErrorAction = "Stop"
            TimeoutSec  = 45
        }

        if ($Headers) {
            $params["Headers"] = $Headers
        }

        if ($PSVersionTable.PSVersion.Major -lt 6) {
            $params["UseBasicParsing"] = $true
        }

        $methodUpper = $Method.ToUpperInvariant()
        $bodyAllowed = @("POST","PUT","PATCH","DELETE") -contains $methodUpper

        if ($bodyAllowed -and -not [string]::IsNullOrWhiteSpace($Body)) {
            $params["Body"] = $Body
            $params["ContentType"] = "application/json"
        }

        $resp = Invoke-WebRequest @params

        $result.ok = $true
        $result.status_code = [int]$resp.StatusCode
        $result.content_type = [string]$resp.Headers["Content-Type"]

        $bodyText = ""
        if ($null -ne $resp.Content) {
            $bodyText = [string]$resp.Content
        }
        if ($bodyText.Length -gt 1200) {
            $bodyText = $bodyText.Substring(0, 1200)
        }

        $result.body_preview = $bodyText
    }
    catch {
        $ex = $_.Exception
        $result.error = $ex.Message

        $responseObj = Get-ResponseObject -ExceptionObject $ex
        if ($null -ne $responseObj) {
            try { $result.status_code = [int]$responseObj.StatusCode.value__ } catch {}
            try { $result.content_type = [string]$responseObj.Headers["Content-Type"] } catch {}

            try {
                $stream = $responseObj.GetResponseStream()
                if ($null -ne $stream) {
                    $reader = New-Object System.IO.StreamReader($stream)
                    $bodyText = $reader.ReadToEnd()
                    $reader.Dispose()

                    if ($bodyText.Length -gt 1200) {
                        $bodyText = $bodyText.Substring(0, 1200)
                    }

                    $result.body_preview = $bodyText
                }
            } catch {}
        }
    }

    return [pscustomobject]$result
}

$EnvPath = Join-Path $ProjectRoot ".env"

$BaseUrl = $env:N8N_BASE_URL
if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    $BaseUrl = Read-EnvValue -Path $EnvPath -Key "N8N_BASE_URL"
}

$ApiKey = $env:N8N_API_KEY
if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    $ApiKey = Read-EnvValue -Path $EnvPath -Key "N8N_API_KEY"
}

$WebhookUrl = $env:N8N_JARVIS_WEBHOOK_URL
if ([string]::IsNullOrWhiteSpace($WebhookUrl)) {
    $WebhookUrl = Read-EnvValue -Path $EnvPath -Key "N8N_JARVIS_WEBHOOK_URL"
}

$WebhookSecret = $env:N8N_JARVIS_WEBHOOK_SECRET
if ([string]::IsNullOrWhiteSpace($WebhookSecret)) {
    $WebhookSecret = Read-EnvValue -Path $EnvPath -Key "N8N_JARVIS_WEBHOOK_SECRET"
}

if ([string]::IsNullOrWhiteSpace($BaseUrl))    { throw "N8N_BASE_URL is empty" }
if ([string]::IsNullOrWhiteSpace($ApiKey))     { throw "N8N_API_KEY is empty" }
if ([string]::IsNullOrWhiteSpace($WebhookUrl)) { throw "N8N_JARVIS_WEBHOOK_URL is empty" }

$WebhookUri  = [uri]$WebhookUrl
$WebhookPath = $WebhookUri.AbsolutePath -replace '^/webhook/', ''

$ApiHeaders = @{
    "X-N8N-API-KEY" = $ApiKey
    "Accept"        = "application/json"
}

$ApiJsonHeaders = @{
    "X-N8N-API-KEY" = $ApiKey
    "Accept"        = "application/json"
    "Content-Type"  = "application/json"
}

Write-Host ""
Write-Host "=== STEP 1: Check API and resolve workflow ===" -ForegroundColor Cyan

$ApiProbe = Invoke-ProbeRequest `
    -Name "public_api" `
    -Method "GET" `
    -Url ($BaseUrl.TrimEnd("/") + "/api/v1/workflows") `
    -Headers $ApiHeaders

if (-not $ApiProbe.ok) {
    $result = [ordered]@{
        base_url   = $BaseUrl
        webhook_url = $WebhookUrl
        public_api = $ApiProbe
    }
    $result | ConvertTo-Json -Depth 10 | Write-Host
    throw "Public API is not reachable"
}

$WorkflowsRaw = Invoke-RestMethod `
    -Method GET `
    -Uri ($BaseUrl.TrimEnd("/") + "/api/v1/workflows") `
    -Headers $ApiHeaders `
    -TimeoutSec 45

$Workflows = @()
if ($WorkflowsRaw -is [System.Array]) {
    $Workflows = @($WorkflowsRaw)
}
elseif ($null -ne $WorkflowsRaw.data) {
    $Workflows = @($WorkflowsRaw.data)
}
else {
    $Workflows = @($WorkflowsRaw)
}

$WorkflowMatches = @()

foreach ($wf in $Workflows) {
    $nodes = @()
    if ($null -ne $wf.nodes) {
        $nodes = @($wf.nodes)
    }

    foreach ($node in $nodes) {
        $nodeType = ""
        $nodePath = ""
        $nodeMethod = ""
        try { $nodeType = [string]$node.type } catch {}
        try { $nodePath = [string]$node.parameters.path } catch {}
        try { $nodeMethod = [string]$node.parameters.httpMethod } catch {}

        if ($nodeType -like "*webhook*" -and $nodePath -eq $WebhookPath) {
            $WorkflowMatches += [pscustomobject]@{
                id         = [string]$wf.id
                name       = [string]$wf.name
                active     = [bool]$wf.active
                node_name  = [string]$node.name
                node_type  = $nodeType
                httpMethod = $nodeMethod
                path       = $nodePath
            }
        }
    }
}

if ($WorkflowMatches.Count -eq 0) {
    $result = [ordered]@{
        base_url         = $BaseUrl
        webhook_url      = $WebhookUrl
        webhook_path     = $WebhookPath
        public_api       = $ApiProbe
        workflow_matches = $WorkflowMatches
    }
    $result | ConvertTo-Json -Depth 10 | Write-Host
    throw "No workflow found with webhook path '$WebhookPath'"
}

if ($WorkflowMatches.Count -gt 1) {
    $result = [ordered]@{
        base_url         = $BaseUrl
        webhook_url      = $WebhookUrl
        webhook_path     = $WebhookPath
        public_api       = $ApiProbe
        workflow_matches = $WorkflowMatches
    }
    $result | ConvertTo-Json -Depth 10 | Write-Host
    throw "Multiple workflows found with the same webhook path '$WebhookPath'"
}

$Match = $WorkflowMatches[0]

$WorkflowFull = Invoke-RestMethod `
    -Method GET `
    -Uri ($BaseUrl.TrimEnd("/") + "/api/v1/workflows/" + $Match.id) `
    -Headers $ApiHeaders `
    -TimeoutSec 45

$ActivationTry1 = $null
$ActivationTry2 = $null
$RefreshedWorkflow = $null

Write-Host ""
Write-Host "=== STEP 2: Try activation endpoint ===" -ForegroundColor Cyan

if (-not $Match.active) {
    try {
        $ActivationTry1 = Invoke-RestMethod `
            -Method POST `
            -Uri ($BaseUrl.TrimEnd("/") + "/api/v1/workflows/" + $Match.id + "/activate") `
            -Headers $ApiJsonHeaders `
            -Body "{}" `
            -TimeoutSec 45
    }
    catch {
        $ActivationTry1 = [pscustomobject]@{
            error = $_.Exception.Message
        }
    }

    Start-Sleep -Seconds 2

    try {
        $RefreshedWorkflow = Invoke-RestMethod `
            -Method GET `
            -Uri ($BaseUrl.TrimEnd("/") + "/api/v1/workflows/" + $Match.id) `
            -Headers $ApiHeaders `
            -TimeoutSec 45
    }
    catch {
        $RefreshedWorkflow = [pscustomobject]@{
            error = $_.Exception.Message
        }
    }

    if ($null -ne $RefreshedWorkflow.active -and $RefreshedWorkflow.active -eq $false) {
        Write-Host "Activation endpoint did not make workflow active. Trying PUT fallback..." -ForegroundColor Yellow

        $UpdateBody = [ordered]@{
            name        = $WorkflowFull.name
            nodes       = $WorkflowFull.nodes
            connections = $WorkflowFull.connections
            settings    = $WorkflowFull.settings
            active      = $true
        }

        if ($null -ne $WorkflowFull.versionId) {
            $UpdateBody["versionId"] = $WorkflowFull.versionId
        }

        $UpdateJson = $UpdateBody | ConvertTo-Json -Depth 100

        try {
            $ActivationTry2 = Invoke-RestMethod `
                -Method PUT `
                -Uri ($BaseUrl.TrimEnd("/") + "/api/v1/workflows/" + $Match.id) `
                -Headers $ApiJsonHeaders `
                -Body $UpdateJson `
                -TimeoutSec 45
        }
        catch {
            $ActivationTry2 = [pscustomobject]@{
                error = $_.Exception.Message
            }
        }

        Start-Sleep -Seconds 2

        try {
            $RefreshedWorkflow = Invoke-RestMethod `
                -Method GET `
                -Uri ($BaseUrl.TrimEnd("/") + "/api/v1/workflows/" + $Match.id) `
                -Headers $ApiHeaders `
                -TimeoutSec 45
        }
        catch {
            $RefreshedWorkflow = [pscustomobject]@{
                error = $_.Exception.Message
            }
        }
    }
}
else {
    $ActivationTry1 = [pscustomobject]@{
        skipped = $true
        reason  = "workflow already active"
    }
    $RefreshedWorkflow = $WorkflowFull
}

Write-Host ""
Write-Host "=== STEP 3: Probe production webhook ===" -ForegroundColor Cyan

$WebhookBody = @{
    action    = "health_check"
    source    = "jarvis_repair_v2"
    message   = "repair probe"
    timestamp = (Get-Date).ToString("s")
} | ConvertTo-Json -Depth 10

$WebhookHeaders = @{
    "Accept"            = "application/json"
    "X-JARVIS-SOURCE"   = "jarvis_repair_v2"
    "X-IDEMPOTENCY-KEY" = [guid]::NewGuid().ToString()
}

if (-not [string]::IsNullOrWhiteSpace($WebhookSecret)) {
    $WebhookHeaders["X-JARVIS-SECRET"] = $WebhookSecret
}

$WebhookProbe = Invoke-ProbeRequest `
    -Name "production_webhook" `
    -Method "POST" `
    -Url $WebhookUrl `
    -Headers $WebhookHeaders `
    -Body $WebhookBody

$Result = [ordered]@{
    base_url            = $BaseUrl
    webhook_url         = $WebhookUrl
    webhook_path        = $WebhookPath
    public_api          = $ApiProbe
    workflow_match      = $Match
    activation_try_1    = $ActivationTry1
    activation_try_2    = $ActivationTry2
    refreshed_workflow  = $RefreshedWorkflow
    production_webhook  = $WebhookProbe
}

$Result | ConvertTo-Json -Depth 100 | Write-Host

Write-Host ""
if ($WebhookProbe.ok) {
    Write-Host "SUCCESS: production webhook is live." -ForegroundColor Green
}
elseif ($null -ne $RefreshedWorkflow.active -and $RefreshedWorkflow.active -eq $true -and $null -eq $RefreshedWorkflow.activeVersionId) {
    throw "Workflow is active, but activeVersionId is null. Final manual step: open workflow 'jarvis-improvements-log' in n8n and click Publish."
}
elseif ($null -ne $RefreshedWorkflow.active -and $RefreshedWorkflow.active -eq $true) {
    throw "Workflow is active, but webhook still fails. Check the workflow in UI and verify the published version and path."
}
else {
    throw "Workflow activation still did not take effect. Inspect activation_try_1, activation_try_2, and refreshed_workflow."
}