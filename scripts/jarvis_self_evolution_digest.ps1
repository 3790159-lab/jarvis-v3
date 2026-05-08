# Jarvis Self Evolution Digest Pack
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Base = "http://127.0.0.1:8028"
$Queue = Invoke-RestMethod -Method GET -Uri "$Base/api/self-evolution/proposals" -TimeoutSec 30
$Status = Invoke-RestMethod -Method GET -Uri "$Base/api/self-evolution/status" -TimeoutSec 30
[ordered]@{ queue = $Queue; status = $Status } | ConvertTo-Json -Depth 100
