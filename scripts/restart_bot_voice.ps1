$root = "C:\jarvis"
Get-Content "$root\.env" | ForEach-Object {
  if ($_ -match '^\s*([^#=][^=]*)=(.*)$') {
    [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim().Trim('"').Trim("'"), "Process")
  }
}
$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONPATH=$root; $env:BACKEND_BASE_URL="http://127.0.0.1:8010"
$py = "$root\.venv\Scripts\python.exe"

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'jarvis_smart_telegram_control' } |
  ForEach-Object { Write-Output "killing old bot PID $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$k = $env:OPENAI_API_KEY
if ($k.Length -gt 14) { Write-Output ("OPENAI_API_KEY loaded: " + $k.Substring(0,7) + '...' + $k.Substring($k.Length-4) + " len=$($k.Length)") }
else { Write-Output "OPENAI_API_KEY EMPTY/short" }

$bot = Start-Process -FilePath $py -ArgumentList "$root\tools\jarvis_smart_telegram_control.py" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput "$root\logs\bot_stdout.log" -RedirectStandardError "$root\logs\bot_stderr.log" -PassThru
Write-Output "new bot launcher PID = $($bot.Id)"
