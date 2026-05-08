param(
    [string]$PanelUrl = "http://127.0.0.1:8026"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Utf8JsonPost {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][hashtable]$Payload
    )

    $json = $Payload | ConvertTo-Json -Depth 10 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    Invoke-RestMethod -Method POST -Uri $Uri -ContentType "application/json; charset=utf-8" -Body $bytes -TimeoutSec 20
}

$paths = @(
    "/api/meta",
    "/api/probe",
    "/api/probe/summary",
    "/api/hardening",
    "/api/runs"
)

foreach ($p in $paths) {
    $uri = $PanelUrl.TrimEnd("/") + $p
    Write-Host "GET $uri" -ForegroundColor Cyan
    try {
        Invoke-RestMethod -Method GET -Uri $uri -TimeoutSec 12 | ConvertTo-Json -Depth 20
    }
    catch {
        Write-Warning $_.Exception.Message
    }
    Write-Host ""
}

$tests = @(
    "Привет, куда ты сохраняешь данные ?",
    "Окей, как тебя зовут ?",
    "Какие сейчас доступны сервисы и ии агенты ?"
)

foreach ($msg in $tests) {
    Write-Host "POST /api/chat => $msg" -ForegroundColor Green
    try {
        Invoke-Utf8JsonPost -Uri ($PanelUrl.TrimEnd("/") + "/api/chat") -Payload @{
            message = $msg
            mode    = "default"
        } | ConvertTo-Json -Depth 20
    }
    catch {
        Write-Warning $_.Exception.Message
    }
    Write-Host ""
}