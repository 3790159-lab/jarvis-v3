param([string]$ProjectRoot)

Set-Location $ProjectRoot
$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

& $PyExe "tools\jarvis_self_healing_v7_8.py"