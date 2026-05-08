param(
    [string]$ProjectRoot = "",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$ScriptsDir  = Join-Path $ProjectRoot "scripts"
$HelperPath  = Join-Path $ScriptsDir "jarvis_real_run_helpers.ps1"
$RunRealPath = Join-Path $ScriptsDir "jarvis_run_real_sop_package.ps1"
$Phase2Path  = Join-Path $ScriptsDir "jarvis_sop_phase2_finalize.ps1"
$ConsoleV4   = Join-Path $ScriptsDir "jarvis_operator_console_v4.ps1"
$NormalizePy = Join-Path $ScriptsDir "jarvis_artifact_normalize_utf8.py"
$PyExe       = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PyExe)) { $PyExe = "python" }

function Test-Parse {
    param([string]$Path)
    $tokens = $null
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    return (-not ($errors -and $errors.Count -gt 0))
}

$summary = [ordered]@{}
$summary["helper_exists"]               = (Test-Path $HelperPath)
$summary["run_real_exists"]             = (Test-Path $RunRealPath)
$summary["phase2_exists"]               = (Test-Path $Phase2Path)
$summary["console_v4_exists"]           = (Test-Path $ConsoleV4)
$summary["normalize_py_exists"]         = (Test-Path $NormalizePy)
$summary["helper_parse_ok"]             = $false
$summary["run_real_parse_ok"]           = $false
$summary["phase2_parse_ok"]             = $false
$summary["console_v4_parse_ok"]         = $false
$summary["normalize_py_compile_ok"]     = $false
$summary["governed_request_hook"]       = $false
$summary["approval_guard"]              = $false
$summary["phase2_helper"]               = $false
$summary["normalize_helper"]            = $false
$summary["prompt_hardening_present"]    = $false
$summary["api_health_ok"]               = $null
$summary["api_health_error"]            = ""

if ($summary["helper_exists"])     { $summary["helper_parse_ok"] = Test-Parse $HelperPath }
if ($summary["run_real_exists"])   { $summary["run_real_parse_ok"] = Test-Parse $RunRealPath }
if ($summary["phase2_exists"])     { $summary["phase2_parse_ok"] = Test-Parse $Phase2Path }
if ($summary["console_v4_exists"]) { $summary["console_v4_parse_ok"] = Test-Parse $ConsoleV4 }

if ($summary["normalize_py_exists"]) {
    & $PyExe -m py_compile $NormalizePy
    $summary["normalize_py_compile_ok"] = ($LASTEXITCODE -eq 0)
}

. $HelperPath

$summary["governed_request_hook"] = [bool](Get-Command Invoke-GovernedJsonRequest -ErrorAction SilentlyContinue)
$summary["approval_guard"]        = [bool](Get-Command Assert-AdapterResponseApproved -ErrorAction SilentlyContinue)
$summary["phase2_helper"]         = [bool](Get-Command Invoke-SopPhase2Finalize -ErrorAction SilentlyContinue)
$summary["normalize_helper"]      = [bool](Get-Command Normalize-ArtifactRunDir -ErrorAction SilentlyContinue)

$runRealText = Get-Content -Path $RunRealPath -Raw -Encoding UTF8
$summary["prompt_hardening_present"] = $runRealText.Contains("Return ONLY the final complete operator-ready SOP document in markdown for this topic:")

try {
    $null = Invoke-RestMethod -Method Get -Uri ($BaseUrl.TrimEnd("/") + "/health") -TimeoutSec 10
    $summary["api_health_ok"] = $true
}
catch {
    $summary["api_health_ok"] = $false
    $summary["api_health_error"] = $_.Exception.Message
}

$summary | ConvertTo-Json -Depth 10