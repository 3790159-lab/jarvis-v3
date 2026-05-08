param(
    [Parameter(Mandatory=$true)]
    [string]$Command
)
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
python .\tools\jarvis_real_tools.py safe-shell --command-text $Command
