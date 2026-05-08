param(
    [string]$ProjectRoot = "C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

$EvidenceScript = Join-Path $ProjectRoot "scripts\jarvis_real_action_evidence_v6_3.ps1"
$OutDir = Join-Path $ProjectRoot "jarvis_stage3_artifacts\real_action_evidence"
$BridgeLog = Join-Path $OutDir "night_mode_truth_guard_bridge.log"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Log-Line {
    param([string]$Text)
    $Line = "[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $Text
    Add-Content -Path $BridgeLog -Value $Line -Encoding UTF8
    Write-Host $Line
}

Log-Line "Starting Truth Guard evidence bridge."

if (-not (Test-Path $EvidenceScript)) {
    Log-Line "Missing evidence script: $EvidenceScript"
    exit 3
}

& $EvidenceScript -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl
$Exit = $LASTEXITCODE

$LatestJson = Join-Path $OutDir "latest_real_action_evidence_report.json"
if (Test-Path $LatestJson) {
    try {
        $Report = Get-Content $LatestJson -Raw -Encoding UTF8 | ConvertFrom-Json
        Log-Line ("TruthGuardVerdict=" + $Report.truth_guard.verdict)
        Log-Line ("StrictNextAction=" + $Report.truth_guard.strict_next_action)
        Log-Line ("BackendStatus=" + $Report.backend.summary.status)
        Log-Line ("N8nPortOpen=" + $Report.n8n.port.open)
    } catch {
        Log-Line ("Failed to parse latest evidence JSON: " + $_.Exception.Message)
    }
} else {
    Log-Line "Latest evidence JSON missing."
}

Log-Line "Evidence bridge completed with ExitCode=$Exit"
exit $Exit