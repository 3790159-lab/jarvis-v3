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

function Invoke-HttpProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Headers,
        [string]$Body
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

        if ($null -ne $Body) {
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
        if ($bodyText.Length -gt 600) {
            $bodyText = $bodyText.Substring(0, 600)
        }

        $result.body_preview = $bodyText
    }
    catch {
        $ex = $_.Exception
        $result.error = $ex.Message

        $responseObj = Get-ResponseObject -ExceptionObject $ex
        if ($null -ne $responseObj) {
            try {
                $result.status_code = [int]$responseObj.StatusCode.value__
            } catch {}

            try {
                $result.content_type = [string]$responseObj.Headers["Content-Type"]
            } catch {}

            try {
                $stream = $responseObj.GetResponseStream()
                if ($null -ne $stream) {
                    $reader = New-Object System.IO.StreamReader($stream)
                    $bodyText = $reader.ReadToEnd()
                    $reader.Dispose()
                    if ($bodyText.Length -gt 600) {
                        $bodyText = $bodyText.Substring(0, 600)
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

$RootProbe = Invoke-HttpProbe -Name "workspace_root" -Method "GET" -Url $BaseUrl

$ApiProbe = Invoke-HttpProbe `
    -Name "public_api" `
    -Method "GET" `
    -Url ($BaseUrl.TrimEnd("/") + "/api/v1/workflows") `
    -Headers @{
        "X-N8N-API-KEY" = $ApiKey
        "Accept" = "application/json"
    }

$WebhookBody = @{
    action = "health_check"
    source = "jarvis_probe"
    message = "probe"
    timestamp = (Get-Date).ToString("s")
} | ConvertTo-Json -Depth 10

$WebhookHeaders = @{
    "Accept" = "application/json"
    "X-JARVIS-SOURCE" = "jarvis_probe"
    "X-IDEMPOTENCY-KEY" = [guid]::NewGuid().ToString()
}
if (-not [string]::IsNullOrWhiteSpace($WebhookSecret)) {
    $WebhookHeaders["X-JARVIS-SECRET"] = $WebhookSecret
}

$WebhookProbe = Invoke-HttpProbe `
    -Name "production_webhook" `
    -Method "POST" `
    -Url $WebhookUrl `
    -Headers $WebhookHeaders `
    -Body $WebhookBody

$result = [ordered]@{
    base_url           = $BaseUrl
    webhook_url        = $WebhookUrl
    workspace_root     = $RootProbe
    public_api         = $ApiProbe
    production_webhook = $WebhookProbe
}

$result | ConvertTo-Json -Depth 10 | Write-Host

if (-not $ApiProbe.ok -or -not $WebhookProbe.ok) {
    throw "n8n probe failed"
}

Write-Host ""
Write-Host "n8n probe passed." -ForegroundColor Green