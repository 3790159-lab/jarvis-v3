param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

$targets = @(
    "app.telegram_bot",
    "app\telegram_bot.py",
    "telegram_bot.py"
)

$killed = 0

Get-CimInstance Win32_Process | ForEach-Object {
    $cmd = $_.CommandLine
    if ([string]::IsNullOrWhiteSpace($cmd)) { return }

    foreach ($target in $targets) {
        if ($cmd -like "*$target*") {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                Write-Host ("[OK] Stopped PID {0}: {1}" -f $_.ProcessId, $cmd)
                $script:killed++
                break
            } catch {
                Write-Host ("[WARN] Failed to stop PID {0}: {1}" -f $_.ProcessId, $_.Exception.Message)
            }
        }
    }
}

if ($killed -eq 0) {
    Write-Host "[INFO] No running telegram bot processes found."
} else {
    Write-Host ("[OK] Stopped {0} bot process(es)." -f $killed)
}
