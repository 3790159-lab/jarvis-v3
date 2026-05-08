param([string]$ProjectRoot)

Set-Location $ProjectRoot
$PyExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) { $PyExe = "python" }

& $PyExe "tools\jarvis_full_creator_v8_8.py"