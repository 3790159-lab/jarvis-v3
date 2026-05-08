param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$JarvisBaseUrlDefault = "http://127.0.0.1:8015"
$WorkflowName         = "jarvis-auto-improvements-to-sheet"
$WebhookPath          = "jarvis-auto-improvements-log"
$SheetTitle           = "Jarvis Auto Improvements LIVE"
$SheetName            = "Improvements"
$LogDirRelative       = "jarvis_stage3_artifacts\integration_logs\n8n"

$EnvPath      = Join-Path $ProjectRoot ".env"
$ArtifactsDir = Join-Path $ProjectRoot $LogDirRelative
New-Item -ItemType Directory -Force -Path $ArtifactsDir | Out-Null

function Read-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )

    if (!(Test-Path $Path)) { return $null }

    $content = Get-Content $Path -Raw -ErrorAction SilentlyContinue
    if ([string]::IsNullOrWhiteSpace($content)) { return $null }

    $escapedKey = [regex]::Escape($Key)
    $m = [regex]::Match($content, "(?m)^$escapedKey=(.*)$")
    if ($m.Success) { return $m.Groups[1].Value.Trim() }

    return $null
}

function Write-JsonLog {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Data
    )

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $path = Join-Path $ArtifactsDir ($stamp + "_" + $Name + ".json")
    $json = $Data | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText(
        $path,
        $json,
        [System.Text.UTF8Encoding]::new($false)
    )
    return $path
}

function Get-ResponseBodyFromException {
    param($ExceptionObject)

    if ($null -eq $ExceptionObject) { return $null }

    $response = $null

    if ($ExceptionObject.PSObject.Properties.Name -contains "Response") {
        $response = $ExceptionObject.Response
    }
    elseif (
        ($ExceptionObject.PSObject.Properties.Name -contains "InnerException") -and
        $null -ne $ExceptionObject.InnerException -and
        ($ExceptionObject.InnerException.PSObject.Properties.Name -contains "Response")
    ) {
        $response = $ExceptionObject.InnerException.Response
    }

    if ($null -eq $response) {
        return $ExceptionObject.Message
    }

    try {
        $stream = $response.GetResponseStream()
        if ($null -eq $stream) {
            return $ExceptionObject.Message
        }

        $reader = New-Object System.IO.StreamReader($stream)
        $text = $reader.ReadToEnd()
        $reader.Dispose()
        return $text
    }
    catch {
        return $ExceptionObject.Message
    }
}

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Headers = $null,
        [AllowNull()]$Body = $null,
        [int]$TimeoutSec = 60
    )

    $params = @{
        Method      = $Method
        Uri         = $Url
        ErrorAction = "Stop"
        TimeoutSec  = $TimeoutSec
    }

    if ($Headers) {
        $params["Headers"] = $Headers
    }

    if ($PSVersionTable.PSVersion.Major -lt 6) {
        $params["UseBasicParsing"] = $true
    }

    if ($null -ne $Body) {
        $params["Body"] = $Body
        $params["ContentType"] = "application/json"
    }

    return Invoke-RestMethod @params
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
            TimeoutSec  = 60
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
        $bodyText = Get-ResponseBodyFromException -ExceptionObject $ex
        if (-not [string]::IsNullOrWhiteSpace($bodyText)) {
            if ($bodyText.Length -gt 1200) {
                $bodyText = $bodyText.Substring(0, 1200)
            }
            $result.body_preview = $bodyText
        }
    }

    return [pscustomobject]$result
}

function Mask-Secret {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    if ($Value.Length -le 8) { return "********" }

    return ($Value.Substring(0, 4) + "..." + $Value.Substring($Value.Length - 4))
}

$N8N_BASE_URL = $env:N8N_BASE_URL
if ([string]::IsNullOrWhiteSpace($N8N_BASE_URL)) {
    $N8N_BASE_URL = Read-EnvValue -Path $EnvPath -Key "N8N_BASE_URL"
}

$N8N_API_KEY = $env:N8N_API_KEY
if ([string]::IsNullOrWhiteSpace($N8N_API_KEY)) {
    $N8N_API_KEY = Read-EnvValue -Path $EnvPath -Key "N8N_API_KEY"
}

$N8N_JARVIS_WEBHOOK_SECRET = $env:N8N_JARVIS_WEBHOOK_SECRET
if ([string]::IsNullOrWhiteSpace($N8N_JARVIS_WEBHOOK_SECRET)) {
    $N8N_JARVIS_WEBHOOK_SECRET = Read-EnvValue -Path $EnvPath -Key "N8N_JARVIS_WEBHOOK_SECRET"
}

$JarvisBaseUrl = $env:JARVIS_BASE_URL
if ([string]::IsNullOrWhiteSpace($JarvisBaseUrl)) {
    $JarvisBaseUrl = Read-EnvValue -Path $EnvPath -Key "JARVIS_BASE_URL"
}
if ([string]::IsNullOrWhiteSpace($JarvisBaseUrl)) {
    $JarvisBaseUrl = $JarvisBaseUrlDefault
}

if ([string]::IsNullOrWhiteSpace($N8N_BASE_URL)) { throw "N8N_BASE_URL is empty" }
if ([string]::IsNullOrWhiteSpace($N8N_API_KEY))  { throw "N8N_API_KEY is empty" }
if ([string]::IsNullOrWhiteSpace($JarvisBaseUrl)) { throw "JarvisBaseUrl is empty" }

$ApiHeaders = @{
    "X-N8N-API-KEY" = $N8N_API_KEY
    "Accept"        = "application/json"
}

$ApiJsonHeaders = @{
    "X-N8N-API-KEY" = $N8N_API_KEY
    "Accept"        = "application/json"
    "Content-Type"  = "application/json"
}

$Summary = [ordered]@{
    started_at                   = (Get-Date).ToString("s")
    n8n_base_url                 = $N8N_BASE_URL
    jarvis_base_url              = $JarvisBaseUrl
    workflow_name                = $WorkflowName
    webhook_path                 = $WebhookPath
    sheet_title                  = $SheetTitle
    sheet_name                   = $SheetName
    webhook_secret_masked        = (Mask-Secret -Value $N8N_JARVIS_WEBHOOK_SECRET)
    preflight                    = @{}
    spreadsheet_bootstrap        = $null
    workflow_deploy              = $null
    activation                   = $null
    webhook_test                 = $null
    spreadsheet_verify           = $null
    finished_at                  = $null
    status                       = "started"
}

Write-Host ""
Write-Host "=== STEP 1: PREFLIGHT CHECKS ===" -ForegroundColor Cyan

$N8nApiProbe = Invoke-ProbeRequest `
    -Name "n8n_public_api" `
    -Method "GET" `
    -Url ($N8N_BASE_URL.TrimEnd("/") + "/api/v1/workflows") `
    -Headers $ApiHeaders

$JarvisHealthProbe = Invoke-ProbeRequest `
    -Name "jarvis_health" `
    -Method "GET" `
    -Url ($JarvisBaseUrl.TrimEnd("/") + "/health")

$SheetsHealthProbe = Invoke-ProbeRequest `
    -Name "jarvis_spreadsheets_health" `
    -Method "GET" `
    -Url ($JarvisBaseUrl.TrimEnd("/") + "/api/spreadsheets/health")

$Summary.preflight = [ordered]@{
    n8n_public_api          = $N8nApiProbe
    jarvis_health           = $JarvisHealthProbe
    jarvis_spreadsheets_api = $SheetsHealthProbe
}

if (-not $N8nApiProbe.ok) {
    $Summary.status = "failed_preflight_n8n"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_preflight_n8n" -Data $Summary | Out-Null
    throw "n8n Public API preflight failed"
}

if (-not $JarvisHealthProbe.ok) {
    $Summary.status = "failed_preflight_jarvis"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_preflight_jarvis" -Data $Summary | Out-Null
    throw "Jarvis backend health preflight failed"
}

if (-not $SheetsHealthProbe.ok) {
    $Summary.status = "failed_preflight_spreadsheets"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_preflight_spreadsheets" -Data $Summary | Out-Null
    throw "Jarvis spreadsheets health preflight failed"
}

Write-Host "Preflight passed." -ForegroundColor Green

Write-Host ""
Write-Host "=== STEP 2: BOOTSTRAP SHEET ===" -ForegroundColor Cyan

$SpreadsheetBootstrapBody = @{
    payload = @{
        mode       = "google_sheets"
        action     = "create_table"
        title      = $SheetTitle
        sheet_name = $SheetName
        headers    = @(
            "TimestampUtc",
            "ImprovementId",
            "Title",
            "Category",
            "Summary",
            "Status",
            "Source",
            "MissionId",
            "TaskId",
            "RatingBefore",
            "RatingAfter",
            "Delta",
            "Notes",
            "IdempotencyKey"
        )
        rows = @()
    }
} | ConvertTo-Json -Depth 30

try {
    $SpreadsheetBootstrapResult = Invoke-JsonRequest `
        -Method "POST" `
        -Url ($JarvisBaseUrl.TrimEnd("/") + "/api/spreadsheets/execute") `
        -Body $SpreadsheetBootstrapBody

    $Summary.spreadsheet_bootstrap = @{
        ok     = $true
        result = $SpreadsheetBootstrapResult
    }
}
catch {
    $Summary.spreadsheet_bootstrap = @{
        ok    = $false
        error = (Get-ResponseBodyFromException -ExceptionObject $_.Exception)
    }

    $Summary.status = "failed_spreadsheet_bootstrap"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_spreadsheet_bootstrap" -Data $Summary | Out-Null
    throw "Spreadsheet bootstrap failed"
}

Write-Host "Spreadsheet bootstrap passed." -ForegroundColor Green

Write-Host ""
Write-Host "=== STEP 3: BUILD HARDENED WORKFLOW DEFINITION ===" -ForegroundColor Cyan

$NormalizeJs = @"
const raw = \$input.first().json || {};
const body = raw.body ?? raw;
const headers = raw.headers ?? {};

function getHeader(name) {
  const lower = name.toLowerCase();
  return headers[name] ?? headers[lower] ?? headers[name.toUpperCase()] ?? null;
}

const expectedSecret = "$N8N_JARVIS_WEBHOOK_SECRET";
const incomingSecret = getHeader("x-jarvis-secret");

if (expectedSecret && incomingSecret && incomingSecret !== expectedSecret) {
  throw new Error("Invalid X-JARVIS-SECRET");
}

const title = String(body.improvement_title ?? body.title ?? "").trim();
const category = String(body.category ?? "").trim();
const summary = String(body.summary ?? body.description ?? "").trim();

if (!title) throw new Error("improvement_title is required");
if (!category) throw new Error("category is required");
if (!summary) throw new Error("summary is required");

const improvementId =
  String(body.improvement_id ?? "").trim() ||
  ("imp_" + Date.now().toString());

const status = String(body.status ?? "proposed").trim() || "proposed";
const source = String(body.source ?? "jarvis").trim() || "jarvis";
const missionId = String(body.mission_id ?? "").trim();
const taskId = String(body.task_id ?? "").trim();
const notes = String(body.notes ?? "").trim();

const ratingBefore =
  body.rating_before === undefined || body.rating_before === null || body.rating_before === ""
    ? null
    : Number(body.rating_before);

const ratingAfter =
  body.rating_after === undefined || body.rating_after === null || body.rating_after === ""
    ? null
    : Number(body.rating_after);

const delta =
  Number.isFinite(ratingBefore) && Number.isFinite(ratingAfter)
    ? (ratingAfter - ratingBefore)
    : null;

const idempotencyKey =
  String(getHeader("x-idempotency-key") ?? body.idempotency_key ?? "").trim();

const row = [
  new Date().toISOString(),
  improvementId,
  title,
  category,
  summary,
  status,
  source,
  missionId,
  taskId,
  ratingBefore ?? "",
  ratingAfter ?? "",
  delta ?? "",
  notes,
  idempotencyKey
];

return [
  {
    json: {
      ok: true,
      improvement_id: improvementId,
      improvement_title: title,
      category,
      summary,
      spreadsheetRequest: {
        mode: "google_sheets",
        action: "append_rows",
        title: "$SheetTitle",
        sheet_name: "$SheetName",
        rows: [row]
      }
    }
  }
];
"@

$FinalizeJs = @"
const appendResult = \$input.first().json || {};
return [
  {
    json: {
      ok: true,
      workflow: "$WorkflowName",
      sheet_title: "$SheetTitle",
      sheet_name: "$SheetName",
      append_result: appendResult
    }
  }
];
"@

$WorkflowBody = @{
    name = $WorkflowName
    nodes = @(
        @{
            parameters = @{
                httpMethod   = "POST"
                path         = $WebhookPath
                responseMode = "lastNode"
                responseData = "firstEntryJson"
                options      = @{}
            }
            type        = "n8n-nodes-base.webhook"
            typeVersion = 2.1
            position    = @(0, 0)
            id          = "webhook_jarvis_auto_improvements"
            name        = "Webhook"
        },
        @{
            parameters = @{
                jsCode = $NormalizeJs
            }
            type        = "n8n-nodes-base.code"
            typeVersion = 2
            position    = @(260, 0)
            id          = "code_normalize_improvement"
            name        = "Normalize Improvement"
        },
        @{
            parameters = @{
                method      = "POST"
                url         = ($JarvisBaseUrl.TrimEnd("/") + "/api/spreadsheets/execute")
                sendHeaders = $true
                headerParameters = @{
                    parameters = @(
                        @{
                            name  = "Content-Type"
                            value = "application/json"
                        }
                    )
                }
                sendBody    = $true
                specifyBody = "json"
                jsonBody    = '={{ {"payload": $json.spreadsheetRequest} }}'
                options     = @{
                    timeout = 60000
                }
            }
            type        = "n8n-nodes-base.httpRequest"
            typeVersion = 4.2
            position    = @(560, 0)
            id          = "http_append_improvement_to_sheet"
            name        = "Append Improvement To Sheet"
        },
        @{
            parameters = @{
                jsCode = $FinalizeJs
            }
            type        = "n8n-nodes-base.code"
            typeVersion = 2
            position    = @(860, 0)
            id          = "code_finalize_response"
            name        = "Finalize Response"
        }
    )
    connections = @{
        "Webhook" = @{
            main = @(
                @(
                    @{
                        node  = "Normalize Improvement"
                        type  = "main"
                        index = 0
                    }
                )
            )
        }
        "Normalize Improvement" = @{
            main = @(
                @(
                    @{
                        node  = "Append Improvement To Sheet"
                        type  = "main"
                        index = 0
                    }
                )
            )
        }
        "Append Improvement To Sheet" = @{
            main = @(
                @(
                    @{
                        node  = "Finalize Response"
                        type  = "main"
                        index = 0
                    }
                )
            )
        }
    }
    settings = @{
        executionOrder = "v1"
        binaryMode     = "separate"
    }
}

$WorkflowJson = $WorkflowBody | ConvertTo-Json -Depth 100

Write-Host "Workflow definition prepared." -ForegroundColor Green

Write-Host ""
Write-Host "=== STEP 4: CREATE OR UPDATE WORKFLOW ===" -ForegroundColor Cyan

$ExistingRaw = Invoke-JsonRequest `
    -Method "GET" `
    -Url ($N8N_BASE_URL.TrimEnd("/") + "/api/v1/workflows") `
    -Headers $ApiHeaders

$ExistingWorkflows = @()
if ($ExistingRaw -is [System.Array]) {
    $ExistingWorkflows = @($ExistingRaw)
}
elseif ($null -ne $ExistingRaw.data) {
    $ExistingWorkflows = @($ExistingRaw.data)
}
else {
    $ExistingWorkflows = @($ExistingRaw)
}

$ExistingMatch = $ExistingWorkflows | Where-Object { $_.name -eq $WorkflowName } | Select-Object -First 1

$WorkflowId = $null
$DeployAction = ""
$DeployRaw = $null

if ($null -eq $ExistingMatch) {
    $DeployAction = "create"
    $DeployRaw = Invoke-JsonRequest `
        -Method "POST" `
        -Url ($N8N_BASE_URL.TrimEnd("/") + "/api/v1/workflows") `
        -Headers $ApiJsonHeaders `
        -Body $WorkflowJson

    $WorkflowId = [string]$DeployRaw.id
}
else {
    $DeployAction = "update"

    $ExistingFull = Invoke-JsonRequest `
        -Method "GET" `
        -Url ($N8N_BASE_URL.TrimEnd("/") + "/api/v1/workflows/" + $ExistingMatch.id) `
        -Headers $ApiHeaders

    $UpdateBody = @{
        name        = $WorkflowBody.name
        nodes       = $WorkflowBody.nodes
        connections = $WorkflowBody.connections
        settings    = $WorkflowBody.settings
    }

    if ($null -ne $ExistingFull.versionId) {
        $UpdateBody["versionId"] = $ExistingFull.versionId
    }

    $UpdateJson = $UpdateBody | ConvertTo-Json -Depth 100

    $DeployRaw = Invoke-JsonRequest `
        -Method "PUT" `
        -Url ($N8N_BASE_URL.TrimEnd("/") + "/api/v1/workflows/" + $ExistingMatch.id) `
        -Headers $ApiJsonHeaders `
        -Body $UpdateJson

    $WorkflowId = [string]$ExistingMatch.id
}

if ([string]::IsNullOrWhiteSpace($WorkflowId)) {
    $Summary.status = "failed_workflow_deploy"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_workflow_deploy" -Data $Summary | Out-Null
    throw "Failed to determine workflow id"
}

$Summary.workflow_deploy = @{
    action      = $DeployAction
    workflow_id = $WorkflowId
    raw         = $DeployRaw
}

Write-Host "Workflow deploy passed. Action: $DeployAction, Id: $WorkflowId" -ForegroundColor Green

Write-Host ""
Write-Host "=== STEP 5: ACTIVATE WORKFLOW ===" -ForegroundColor Cyan

$ActivationRaw = $null
try {
    $ActivationRaw = Invoke-JsonRequest `
        -Method "POST" `
        -Url ($N8N_BASE_URL.TrimEnd("/") + "/api/v1/workflows/" + $WorkflowId + "/activate") `
        -Headers $ApiJsonHeaders `
        -Body "{}"
}
catch {
    $Summary.activation = @{
        ok    = $false
        error = (Get-ResponseBodyFromException -ExceptionObject $_.Exception)
    }

    $Summary.status = "failed_activate_endpoint"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_activate_endpoint" -Data $Summary | Out-Null
    throw "Workflow activation endpoint failed"
}

Start-Sleep -Seconds 2

$RefreshedWorkflow = Invoke-JsonRequest `
    -Method "GET" `
    -Url ($N8N_BASE_URL.TrimEnd("/") + "/api/v1/workflows/" + $WorkflowId) `
    -Headers $ApiHeaders

$Summary.activation = @{
    ok                 = $true
    activation_raw     = $ActivationRaw
    refreshed_workflow = @{
        id              = $RefreshedWorkflow.id
        name            = $RefreshedWorkflow.name
        active          = $RefreshedWorkflow.active
        activeVersionId = $RefreshedWorkflow.activeVersionId
        versionId       = $RefreshedWorkflow.versionId
        triggerCount    = $RefreshedWorkflow.triggerCount
    }
}

if ($RefreshedWorkflow.active -ne $true) {
    $Summary.status = "failed_inactive_after_activation"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_inactive_after_activation" -Data $Summary | Out-Null
    throw "Workflow is still not active after activation"
}

if ([string]::IsNullOrWhiteSpace([string]$RefreshedWorkflow.activeVersionId)) {
    $Summary.status = "failed_no_active_version"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_no_active_version" -Data $Summary | Out-Null
    throw "Workflow activeVersionId is empty after activation"
}

Write-Host "Activation passed." -ForegroundColor Green

Write-Host ""
Write-Host "=== STEP 6: WEBHOOK SMOKE TEST ===" -ForegroundColor Cyan

$WebhookUrl = $N8N_BASE_URL.TrimEnd("/") + "/webhook/" + $WebhookPath

$TestPayloadObject = @{
    improvement_id     = "test-" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
    improvement_title  = "n8n bridge hardening"
    category           = "automation"
    summary            = "Activated workflow, verified production webhook, and created sheet logging pipeline"
    status             = "implemented"
    source             = "jarvis"
    mission_id         = "test-mission"
    task_id            = "test-task"
    rating_before      = 6
    rating_after       = 8
    notes              = "bootstrap verification"
}

$TestPayloadJson = $TestPayloadObject | ConvertTo-Json -Depth 20

$WebhookHeaders = @{
    "Accept"            = "application/json"
    "X-JARVIS-SOURCE"   = "jarvis_test"
    "X-IDEMPOTENCY-KEY" = [guid]::NewGuid().ToString()
}

if (-not [string]::IsNullOrWhiteSpace($N8N_JARVIS_WEBHOOK_SECRET)) {
    $WebhookHeaders["X-JARVIS-SECRET"] = $N8N_JARVIS_WEBHOOK_SECRET
}

$WebhookProbe = Invoke-ProbeRequest `
    -Name "workflow_webhook_test" `
    -Method "POST" `
    -Url $WebhookUrl `
    -Headers $WebhookHeaders `
    -Body $TestPayloadJson

$Summary.webhook_test = $WebhookProbe

if (-not $WebhookProbe.ok) {
    $Summary.status = "failed_webhook_test"
    $Summary.finished_at = (Get-Date).ToString("s")
    Write-JsonLog -Name "deploy_summary_failed_webhook_test" -Data $Summary | Out-Null
    throw "Webhook smoke test failed"
}

Write-Host "Webhook smoke test passed." -ForegroundColor Green

Write-Host ""
Write-Host "=== STEP 7: VERIFY TABLE ACCESS ===" -ForegroundColor Cyan

$VerifyBody = @{
    payload = @{
        mode       = "google_sheets"
        action     = "inspect"
        title      = $SheetTitle
        sheet_name = $SheetName
    }
} | ConvertTo-Json -Depth 20

try {
    $VerifyResult = Invoke-JsonRequest `
        -Method "POST" `
        -Url ($JarvisBaseUrl.TrimEnd("/") + "/api/spreadsheets/execute") `
        -Body $VerifyBody

    $Summary.spreadsheet_verify = @{
        ok     = $true
        result = $VerifyResult
    }
}
catch {
    $Summary.spreadsheet_verify = @{
        ok    = $false
        error = (Get-ResponseBodyFromException -ExceptionObject $_.Exception)
    }
}

$Summary.status = "ok"
$Summary.finished_at = (Get-Date).ToString("s")
$SummaryLogPath = Write-JsonLog -Name "deploy_summary_ok" -Data $Summary

Write-Host ""
Write-Host "=== DONE ===" -ForegroundColor Green
Write-Host "Workflow name : $WorkflowName"
Write-Host "Workflow id   : $WorkflowId"
Write-Host "Webhook URL   : $WebhookUrl"
Write-Host "Sheet target  : $SheetTitle / $SheetName"
Write-Host "Summary log   : $SummaryLogPath"

Write-Host ""
Write-Host "Short summary:" -ForegroundColor Cyan
[pscustomobject]@{
    workflow_name   = $WorkflowName
    workflow_id     = $WorkflowId
    workflow_active = $RefreshedWorkflow.active
    activeVersionId = $RefreshedWorkflow.activeVersionId
    webhook_ok      = $WebhookProbe.ok
    sheet_verify_ok = $Summary.spreadsheet_verify.ok
} | Format-List