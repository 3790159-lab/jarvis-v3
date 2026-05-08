param(
    [string]$BaseUrl = "http://127.0.0.1:8010"
)

$ErrorActionPreference = "Stop"

try {
    $resp = Invoke-RestMethod -Method GET -Uri ($BaseUrl + "/health") -TimeoutSec 10
    Write-Host "[OK] Health response:"
    $resp | ConvertTo-Json -Depth 10
} catch {
    Write-Host ("[WARN] Health check failed: {0}" -f $_.Exception.Message)
    exit 1
}
