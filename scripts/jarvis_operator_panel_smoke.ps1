param(
    [string]$PanelUrl = "http://127.0.0.1:8026"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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
        Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 12 | ConvertTo-Json -Depth 20
    }
    catch {
        Write-Warning $_.Exception.Message
    }
    Write-Host ""
}