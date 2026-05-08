param(
    [string]$ProjectRoot = 'C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram',
    [int]$PollSeconds = 8
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$WatchDir = Join-Path $ProjectRoot 'jarvis_stage3_artifacts\watchers'
$RunsRoot = Join-Path $ProjectRoot 'jarvis_stage3_artifacts\real_runs'
$PidFile = Join-Path $WatchDir 'sop_phase2_watcher.pid'
$LogFile = Join-Path $WatchDir 'sop_phase2_watcher.log'
$WrapperPath = Join-Path $ProjectRoot 'scripts\jarvis_sop_phase2_finalize.ps1'

function Ensure-Dir([string]$Path) {
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
}
Ensure-Dir $WatchDir

$PID | Set-Content -Path $PidFile -Encoding utf8

while ($true) {
    try {
        if (-not (Test-Path $RunsRoot)) {
            Start-Sleep -Seconds $PollSeconds
            continue
        }

        $LatestRun = Get-ChildItem $RunsRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'sop_*' } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1

        if ($LatestRun) {
            $ReviewFile = Join-Path $LatestRun.FullName 'openai_review.md'
            $Phase2Report = Join-Path $LatestRun.FullName 'phase2_report.txt'

            if ((Test-Path $ReviewFile) -and (-not (Test-Path $Phase2Report))) {
                Add-Content -Path $LogFile -Value ("[{0}] Phase2 finalize -> {1}" -f (Get-Date), $LatestRun.FullName) -Encoding utf8
                & $WrapperPath -ProjectRoot $ProjectRoot -RunDir $LatestRun.FullName | Out-Null
            }
        }
    }
    catch {
        Add-Content -Path $LogFile -Value ("[{0}] ERROR: {1}" -f (Get-Date), $_.Exception.Message) -Encoding utf8
    }

    Start-Sleep -Seconds $PollSeconds
}
