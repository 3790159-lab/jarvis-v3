param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Targets = @(
    "scripts\jarvis_real_run_helpers.ps1",
    "scripts\jarvis_run_real_sop_package.ps1",
    "scripts\jarvis_operator_console_v2.ps1"
)

foreach ($t in $Targets) {
    Write-Host "Checking syntax in $t" -ForegroundColor DarkGray
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $ProjectRoot $t),
        [ref]$null,
        [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) {
        $parseErrors | ForEach-Object { Write-Host $_.Message -ForegroundColor Red }
        throw "PowerShell parse errors found in $t"
    }
}

Write-Host "Operator console v2 syntax checks passed." -ForegroundColor Green