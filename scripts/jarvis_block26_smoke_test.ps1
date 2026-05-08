param(
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory=$true)][string]$Method,
        [Parameter(Mandatory=$true)][string]$Uri,
        [object]$Body = $null
    )

    try {
        if ($null -eq $Body) {
            return Invoke-RestMethod -Method $Method -Uri $Uri -TimeoutSec 20
        }

        $json = $Body | ConvertTo-Json -Depth 80
        return Invoke-RestMethod -Method $Method -Uri $Uri -ContentType "application/json" -Body $json -TimeoutSec 20
    } catch {
        Write-Host "REQUEST FAILED: $Method $Uri" -ForegroundColor Red
        if ($_.Exception.Response) {
            try {
                $stream = $_.Exception.Response.GetResponseStream()
                if ($stream) {
                    $reader = New-Object System.IO.StreamReader($stream)
                    $bodyText = $reader.ReadToEnd()
                    if ($bodyText) {
                        Write-Host "HTTP error body:" -ForegroundColor Yellow
                        Write-Host $bodyText
                    }
                }
            } catch {
            }
        }
        throw
    }
}

Write-Host "== Artifacts health ==" -ForegroundColor Cyan
$artifactsHealth = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/artifacts/health"
$artifactsHealth | ConvertTo-Json -Depth 20

Write-Host "== Create packaged multistep mission ==" -ForegroundColor Cyan
$missionId = "mission_block26_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
$missionBody = @{
    mission_id = $missionId
    objective = "Create a file, read it, process it, and save processed output"
    steps = @(
        @{
            step_id = "artifact_step"
            title = "Artifact packaging step"
            description = "create a file, read it, process it, and save processed output"
            task_type = "coding"
            preferred_provider = "ollama"
            metadata = @{
                phase = "execute"
                router_mode = "hybrid"
                chain_input_name = "block26_input.txt"
                chain_output_name = "block26_output.txt"
            }
        }
    )
}
$missionResult = Invoke-JsonRequest -Method "POST" -Uri "$BaseUrl/api/missions/multistep/execute" -Body $missionBody
$missionResult | ConvertTo-Json -Depth 100

Write-Host "== Artifact manifest ==" -ForegroundColor Cyan
$manifest = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/artifacts/missions/$missionId/manifest"
$manifest | ConvertTo-Json -Depth 60

Write-Host "== Mission result package ==" -ForegroundColor Cyan
$package = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/artifacts/missions/$missionId/result"
$package | ConvertTo-Json -Depth 100

Write-Host "== Artifact mission list ==" -ForegroundColor Cyan
$missionList = Invoke-JsonRequest -Method "GET" -Uri "$BaseUrl/api/artifacts/missions"
$missionList | ConvertTo-Json -Depth 40

Write-Host "Block 2.6 smoke test finished." -ForegroundColor Green
