Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Supervisor = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8015/health" -TimeoutSec 15
$Director   = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8024/health" -TimeoutSec 15
$Bridge     = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8030/health" -TimeoutSec 15

$Result = [ordered]@{ supervisor = $Supervisor; director = $Director; bridge = $Bridge }
$Result | ConvertTo-Json -Depth 50
