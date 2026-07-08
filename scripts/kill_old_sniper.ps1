$killed = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'runpod_gpu_sniper' } |
  ForEach-Object { $killed += $_.ProcessId; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ($killed.Count -gt 0) { Write-Output ("killed old A100-only sniper PIDs: " + ($killed -join ', ')) }
else { Write-Output "no runpod_gpu_sniper process found" }
