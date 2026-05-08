$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
python .\tools\jarvis_real_tools.py smoke
