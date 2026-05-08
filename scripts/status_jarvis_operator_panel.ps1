param(
    [int]$PanelPort = 8026
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    $conn = Get-NetTCPConnection -LocalPort $PanelPort -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($conn) {
        [PSCustomObject]@{
            ok        = $true
            port      = $PanelPort
            pid       = $conn.OwningProcess
            localaddr = $conn.LocalAddress
        } | ConvertTo-Json -Depth 5
    }
    else {
        [PSCustomObject]@{
            ok   = $false
            port = $PanelPort
            message = "No listener"
        } | ConvertTo-Json -Depth 5
    }
}
catch {
    [PSCustomObject]@{
        ok   = $false
        port = $PanelPort
        message = "No listener"
    } | ConvertTo-Json -Depth 5
}