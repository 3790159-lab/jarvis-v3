param(
    [string]$Name = "auto_api",
    [int]$Port = 0,
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

function Invoke-WithRetry {
    param(
        [scriptblock]$Script,
        [int]$MaxAttempts = 2,
        [int]$DelaySeconds = 3,
        [string]$Label = "Operation"
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            & $Script
            return
        }
        catch {
            if ($attempt -ge $MaxAttempts) {
                throw
            }
            Write-Host "$Label failed on attempt $attempt. Retrying in $DelaySeconds sec..."
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Test-TcpPortFree {
    param([int]$Port)
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    return ($null -eq $connections)
}

function Remove-PythonCaches {
    param([string]$RootPath)

    Get-ChildItem -LiteralPath $RootPath -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @("__pycache__", ".pytest_cache") } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }

    Get-ChildItem -LiteralPath $RootPath -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
        }
}

function Invoke-HealthCheckWithRetry {
    param(
        [string]$Url,
        [int]$MaxAttempts = 10,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            $resp = Invoke-RestMethod -Method GET -Uri $Url -TimeoutSec 10
            if ($resp.status -eq "healthy") {
                return $resp
            }
        }
        catch {
        }

        if ($attempt -lt $MaxAttempts) {
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    throw "Health-check failed after $MaxAttempts attempts: $Url"
}

function Get-FreeTcpPort {
    param(
        [int]$StartPort = 8010,
        [int]$EndPort = 8099
    )

    for ($p = $StartPort; $p -le $EndPort; $p++) {
        $connections = Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue
        if ($null -eq $connections) {
            return $p
        }
    }

    throw "No free TCP port found in range $StartPort-$EndPort"
}

function Get-ProjectStructure {
    param(
        [string]$RootPath,
        [int]$MaxItems = 200
    )

    $items = Get-ChildItem -LiteralPath $RootPath -Recurse -Force -ErrorAction SilentlyContinue |
        Select-Object -First $MaxItems FullName, Name, Length, LastWriteTime, PSIsContainer

    return $items
}

function Invoke-ProjectDiagnose {
    param(
        [string]$PythonExe,
        [string]$ProjectPath
    )

    $result = [ordered]@{
        python_files = 0
        test_files = 0
        readme_exists = $false
        requirements_exists = $false
        gitignore_exists = $false
        pytest_ini_exists = $false
    }

    $result.python_files = (Get-ChildItem -LiteralPath $ProjectPath -Recurse -Filter *.py -ErrorAction SilentlyContinue | Measure-Object).Count
    $result.test_files = (Get-ChildItem -LiteralPath (Join-Path $ProjectPath "tests") -Recurse -Filter test_*.py -ErrorAction SilentlyContinue | Measure-Object).Count
    $result.readme_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "README.md")
    $result.requirements_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "requirements.txt")
    $result.gitignore_exists = Test-Path -LiteralPath (Join-Path $ProjectPath ".gitignore")
    $result.pytest_ini_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "pytest.ini")

    return $result
}

function Invoke-ProjectValidator {
    param(
        [string]$ProjectPath
    )

    $checks = [ordered]@{
        app_main_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "app\main.py")
        app_init_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "app\__init__.py")
        tests_exist = Test-Path -LiteralPath (Join-Path $ProjectPath "tests")
        readme_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "README.md")
        requirements_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "requirements.txt")
        gitignore_exists = Test-Path -LiteralPath (Join-Path $ProjectPath ".gitignore")
        pytest_ini_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "pytest.ini")
        run_api_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "run_api.ps1")
        run_tests_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "run_tests.ps1")
        manifest_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "project_manifest.json")
        env_snapshot_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "environment_snapshot.json")
        requirements_lock_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "requirements.lock.txt")
    }

    $failed = @()
    foreach ($k in $checks.Keys) {
        if (-not $checks[$k]) {
            $failed += $k
        }
    }

    return [ordered]@{
        ok = ($failed.Count -eq 0)
        checks = $checks
        failed_checks = $failed
    }
}

function Write-ToolRegistry {
    param(
        [string]$ProjectPath
    )

    $registry = [ordered]@{
        generated_at = (Get-Date).ToString("s")
        tools = @(
            [ordered]@{ name = "scaffold_fastapi"; scope = "project_generation"; enabled = $true }
            [ordered]@{ name = "project_pipeline_v2"; scope = "build_test_health_git"; enabled = $true }
            [ordered]@{ name = "jarvis_repair_v2"; scope = "test_recovery"; enabled = $true }
            [ordered]@{ name = "safe_shell"; scope = "restricted_command_execution"; enabled = $true }
        )
    }

    $registry | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ProjectPath "tool_registry.json") -Encoding UTF8
}

function Write-MissionManifest {
    param(
        [string]$ProjectPath,
        [string]$ProjectName,
        [int]$Port
    )

    $mission = [ordered]@{
        mission_type = "project_pipeline_v2"
        project_name = $ProjectName
        project_path = $ProjectPath
        requested_port = $Port
        generated_at = (Get-Date).ToString("s")
        objective = "Generate, validate, test, health-check and initialize a FastAPI project"
    }

    $mission | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ProjectPath "mission_manifest.json") -Encoding UTF8
}

function Write-ProjectStateSnapshot {
    param(
        [string]$ProjectPath,
        [string]$PythonExe
    )

    $snapshot = [ordered]@{
        generated_at = (Get-Date).ToString("s")
        cwd = $ProjectPath
        python_version = (& $PythonExe --version | Out-String).Trim()
        files_top_level = @(Get-ChildItem -LiteralPath $ProjectPath -Force | Select-Object -ExpandProperty Name)
    }

    $snapshot | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ProjectPath "project_state_snapshot.json") -Encoding UTF8
}

function Write-FileHashSnapshot {
    param(
        [string]$ProjectPath
    )

    $hashTargets = Get-ChildItem -LiteralPath $ProjectPath -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -notmatch '\\.venv\\' -and
            $_.FullName -notmatch '\\__pycache__\\' -and
            $_.Name -notmatch '\.pyc$'
        } |
        Select-Object -First 300

    $hashes = foreach ($f in $hashTargets) {
        try {
            $h = Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256
            [ordered]@{
                path = $f.FullName
                sha256 = $h.Hash
                length = $f.Length
            }
        }
        catch {
        }
    }

    $payload = [ordered]@{
        generated_at = (Get-Date).ToString("s")
        file_count = @($hashes).Count
        files = @($hashes)
    }

    $payload | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ProjectPath "file_hash_snapshot.json") -Encoding UTF8
}

function Write-RuntimePolicy {
    param(
        [string]$ProjectPath,
        [int]$Port
    )

    $policy = [ordered]@{
        generated_at = (Get-Date).ToString("s")
        default_host = "127.0.0.1"
        default_port = $Port
        test_command = "python -m pytest tests -q --import-mode=importlib"
        health_url = "http://127.0.0.1:$Port/health"
        startup_command = "python -m uvicorn app.main:app --host 127.0.0.1 --port $Port"
        recovery_enabled = $true
        validation_required = $true
    }

    $policy | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ProjectPath "project_runtime_policy.json") -Encoding UTF8
}

function Write-ValidatorScript {
    param(
        [string]$ProjectPath
    )

    @"
`$ErrorActionPreference = "Stop"
Set-Location -LiteralPath `$PSScriptRoot

`$required = @(
    "app\main.py",
    "app\__init__.py",
    "tests",
    "README.md",
    "requirements.txt",
    ".gitignore",
    "pytest.ini",
    "project_manifest.json",
    "mission_manifest.json",
    "tool_registry.json",
    "project_state_snapshot.json",
    "project_validation.json",
    "project_runtime_policy.json",
    "file_hash_snapshot.json"
)

`$missing = @()
foreach (`$item in `$required) {
    if (!(Test-Path -LiteralPath (Join-Path `$PSScriptRoot `$item))) {
        `$missing += `$item
    }
}

if (`$missing.Count -gt 0) {
    Write-Host "VALIDATION FAILED"
    `$missing | ForEach-Object { Write-Host ("Missing: " + `$_) }
    exit 1
}

Write-Host "VALIDATION OK"
exit 0
"@ | Set-Content -LiteralPath (Join-Path $ProjectPath "validate_project.ps1") -Encoding UTF8
}

function Write-ControlCenterScript {
    param(
        [string]$ProjectPath,
        [int]$Port
    )

    @"
param(
    [ValidateSet("start","test","health","validate","summary","stop")]
    [string]`$Action = "summary"
)

`$ErrorActionPreference = "Stop"
Set-Location -LiteralPath `$PSScriptRoot

`$pythonExe = Join-Path `$PSScriptRoot ".venv\Scripts\python.exe"
if (!(Test-Path -LiteralPath `$pythonExe)) {
    throw "Python venv not found: `$pythonExe"
}

switch (`$Action) {
    "start" {
        Start-Process -FilePath `$pythonExe -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","$Port" -WorkingDirectory `$PSScriptRoot
        Write-Host "API start requested on port $Port"
    }
    "test" {
        & `$pythonExe -m pytest tests -q --import-mode=importlib
        exit `$LASTEXITCODE
    }
    "health" {
        `$resp = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 10
        `$resp | ConvertTo-Json -Depth 10
    }
    "validate" {
        & (Join-Path `$PSScriptRoot "validate_project.ps1")
        exit `$LASTEXITCODE
    }
    "summary" {
        Get-Content -LiteralPath (Join-Path `$PSScriptRoot "pipeline_summary.json") -Raw
    }
    "stop" {
        Get-CimInstance Win32_Process |
            Where-Object { `$_.Name -match "python(.exe)?$" -and `$_.CommandLine -match "uvicorn app.main:app" -and `$_.CommandLine -match "$Port" } |
            ForEach-Object { Stop-Process -Id `$_.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Host "Stop requested for port $Port"
    }
}
"@ | Set-Content -LiteralPath (Join-Path $ProjectPath "project_control.ps1") -Encoding UTF8
}

function Invoke-ProjectSelfCheck {
    param(
        [string]$ProjectPath,
        [string]$PythonExe,
        [int]$Port
    )

    $result = [ordered]@{
        tests_ok = $false
        validator_ok = $false
        manifest_exists = $false
        runtime_policy_exists = $false
        file_hash_snapshot_exists = $false
        control_script_exists = $false
    }

    $result.manifest_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "project_manifest.json")
    $result.runtime_policy_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "project_runtime_policy.json")
    $result.file_hash_snapshot_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "file_hash_snapshot.json")
    $result.control_script_exists = Test-Path -LiteralPath (Join-Path $ProjectPath "project_control.ps1")

    & $PythonExe -m pytest tests -q --import-mode=importlib | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $result.tests_ok = $true
    }

    $validator = Join-Path $ProjectPath "validate_project.ps1"
    if (Test-Path -LiteralPath $validator) {
        & $validator | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $result.validator_ok = $true
        }
    }

    return $result
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$reportsDir = Join-Path $Root "jarvis_stage3_artifacts\reports"
New-Item -ItemType Directory -Force -Path $reportsDir | Out-Null

$locksDir = Join-Path $Root "jarvis_stage3_artifacts\locks"
New-Item -ItemType Directory -Force -Path $locksDir | Out-Null
$lockFile = Join-Path $locksDir "$Name.lock"

if (Test-Path -LiteralPath $lockFile) {
    throw "Pipeline lock already exists: $lockFile"
}
Set-Content -LiteralPath $lockFile -Value ("started_at=" + (Get-Date).ToString("s")) -Encoding UTF8

if ($Port -eq 0) {
    $Port = Get-FreeTcpPort -StartPort 8010 -EndPort 8099
}

$startedAt = Get-Date
$serverProcess = $null
$healthOk = $false
$testOk = $false
$gitOk = $false
$venvCreated = $false
$venvReused = $false

try {
    Write-Step "STEP 1: Creating project"
    $scaffoldJson = python .\tools\jarvis_real_tools.py scaffold-fastapi --name $Name | Out-String
    $scaffold = $scaffoldJson | ConvertFrom-Json

    if (-not $scaffold.ok) {
        throw "Scaffold failed: $($scaffold.message)"
    }

    $projectPath = Join-Path $Root "jarvis_stage3_artifacts\generated_projects\$Name"
    if (!(Test-Path -LiteralPath $projectPath)) {
        throw "Project path not found: $projectPath"
    }

    Set-Location -LiteralPath $projectPath
    Write-Step "Project path: $projectPath"

    $venvPath = Join-Path $projectPath ".venv"
    if ($RecreateVenv -and (Test-Path -LiteralPath $venvPath)) {
        Write-Step "Removing existing venv"
        Remove-Item -LiteralPath $venvPath -Recurse -Force
    }

    if (!(Test-Path -LiteralPath $venvPath)) {
        Write-Step "STEP 2: Creating venv"
        python -m venv .venv
        $venvCreated = $true
    }
    else {
        Write-Step "STEP 2: Reusing existing venv"
        $venvReused = $true
    }

    $pythonExe = Join-Path $projectPath ".venv\Scripts\python.exe"
    if (!(Test-Path -LiteralPath $pythonExe)) {
        throw "Venv python not found: $pythonExe"
    }

    Write-Step "STEP 3: Installing dependencies"
    Invoke-WithRetry -Label "pip install" -MaxAttempts 2 -DelaySeconds 4 -Script {
        & $pythonExe -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    }

    Write-Step "STEP 3.5: Cleaning Python caches"
    Remove-PythonCaches -RootPath $projectPath

    Write-Step "STEP 4: Running tests"
    & $pythonExe -m pytest tests -q --import-mode=importlib
    if ($LASTEXITCODE -ne 0) {
        Write-Step "Tests failed. Starting recovery"
        $repairScript = Join-Path $Root "scripts\jarvis_repair_v2.ps1"

        if (!(Test-Path -LiteralPath $repairScript)) {
            throw "pytest failed and recovery script not found: $repairScript"
        }

        & $repairScript -ProjectPath $projectPath

        if ($LASTEXITCODE -ne 0) {
            throw "pytest failed and recovery failed"
        }

        Write-Step "Recovery succeeded. Re-running tests"
        Remove-PythonCaches -RootPath $projectPath
        & $pythonExe -m pytest tests -q --import-mode=importlib
        if ($LASTEXITCODE -ne 0) {
            throw "pytest failed even after successful recovery"
        }
    }
    $testOk = $true

    if (-not (Test-TcpPortFree -Port $Port)) {
        throw "Port $Port is busy. Run again with another -Port value."
    }

    Write-Step "STEP 5: Starting API for health-check"
    $serverProcess = Start-Process -FilePath $pythonExe `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port" `
        -WorkingDirectory $projectPath `
        -PassThru `
        -WindowStyle Hidden

    Start-Sleep -Seconds 2

    Write-Step "STEP 6: Checking /health"
    $health = Invoke-HealthCheckWithRetry -Url "http://127.0.0.1:$Port/health" -MaxAttempts 10 -DelaySeconds 2
    $healthOk = $true

    Write-Step "STEP 7: Initializing git"
    if (!(Test-Path ".git")) {
        git init | Out-Null
    }

    $gitUserName = git config user.name 2>$null
    $gitUserEmail = git config user.email 2>$null

    if ([string]::IsNullOrWhiteSpace($gitUserName)) {
        git config user.name "Jarvis Local"
    }
    if ([string]::IsNullOrWhiteSpace($gitUserEmail)) {
        git config user.email "jarvis-local@example.invalid"
    }

    git add . | Out-Null
    git commit -m "Initial Jarvis generated project" | Out-Null 2>$null
    $gitOk = $true

    Write-Step "STEP 8: Capturing environment and manifest"

    @"
`$ErrorActionPreference = "Stop"
Set-Location -LiteralPath `$PSScriptRoot
if (Test-Path .\.venv\Scripts\Activate.ps1) { . .\.venv\Scripts\Activate.ps1 }
python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
"@ | Set-Content -LiteralPath (Join-Path $projectPath "run_api.ps1") -Encoding UTF8

    @"
`$ErrorActionPreference = "Stop"
Set-Location -LiteralPath `$PSScriptRoot
if (Test-Path .\.venv\Scripts\Activate.ps1) { . .\.venv\Scripts\Activate.ps1 }
python -m pytest tests -q --import-mode=importlib
"@ | Set-Content -LiteralPath (Join-Path $projectPath "run_tests.ps1") -Encoding UTF8

    $projectDiagnose = Invoke-ProjectDiagnose -PythonExe $pythonExe -ProjectPath $projectPath
    $projectStructure = Get-ProjectStructure -RootPath $projectPath -MaxItems 200

    & $pythonExe -m pip freeze | Set-Content -LiteralPath (Join-Path $projectPath "requirements.lock.txt") -Encoding UTF8

    $envSnapshot = [ordered]@{
        python_exe = $pythonExe
        python_version = (& $pythonExe --version | Out-String).Trim()
        project_path = $projectPath
        port = $Port
        generated_at = (Get-Date).ToString("s")
    }
    $envSnapshot | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $projectPath "environment_snapshot.json") -Encoding UTF8

    $manifest = [ordered]@{
        project_name = $Name
        project_path = $projectPath
        kind = "fastapi_service"
        port = $Port
        tests_passed = $testOk
        health_check_passed = $healthOk
        git_initialized = $gitOk
        created_at = (Get-Date).ToString("s")
        diagnose = $projectDiagnose
    }
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $projectPath "project_manifest.json") -Encoding UTF8

    Write-MissionManifest -ProjectPath $projectPath -ProjectName $Name -Port $Port
    Write-ToolRegistry -ProjectPath $projectPath
    Write-ProjectStateSnapshot -ProjectPath $projectPath -PythonExe $pythonExe
    Write-RuntimePolicy -ProjectPath $projectPath -Port $Port
    Write-FileHashSnapshot -ProjectPath $projectPath
    Write-ValidatorScript -ProjectPath $projectPath
    Write-ControlCenterScript -ProjectPath $projectPath -Port $Port

    $projectValidation = Invoke-ProjectValidator -ProjectPath $projectPath
    $projectValidation | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $projectPath "project_validation.json") -Encoding UTF8

    if (-not $projectValidation.ok) {
        throw ("Project validation failed: " + ($projectValidation.failed_checks -join ", "))
    }

    $projectSelfCheck = Invoke-ProjectSelfCheck -ProjectPath $projectPath -PythonExe $pythonExe -Port $Port
    $projectSelfCheck | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $projectPath "project_self_check.json") -Encoding UTF8

    if (-not $projectSelfCheck.tests_ok) {
        throw "Project self-check failed: tests_ok=false"
    }

    if (-not $projectSelfCheck.validator_ok) {
        throw "Project self-check failed: validator_ok=false"
    }

    $finishedAt = Get-Date
    $durationSec = [math]::Round((New-TimeSpan -Start $startedAt -End $finishedAt).TotalSeconds, 2)

    $report = [ordered]@{
        project_name = $Name
        project_path = $projectPath
        status = "success"
        started_at = $startedAt.ToString("s")
        finished_at = $finishedAt.ToString("s")
        duration_seconds = $durationSec
        venv_created = $venvCreated
        venv_reused = $venvReused
        tests_passed = $testOk
        health_check_passed = $healthOk
        git_initialized = $gitOk
        port = $Port
        diagnose = $projectDiagnose
        manifest_path = (Join-Path $projectPath "project_manifest.json")
        env_snapshot_path = (Join-Path $projectPath "environment_snapshot.json")
        requirements_lock_path = (Join-Path $projectPath "requirements.lock.txt")
        mission_manifest_path = (Join-Path $projectPath "mission_manifest.json")
        tool_registry_path = (Join-Path $projectPath "tool_registry.json")
        state_snapshot_path = (Join-Path $projectPath "project_state_snapshot.json")
        validation_path = (Join-Path $projectPath "project_validation.json")
        self_check_path = (Join-Path $projectPath "project_self_check.json")
        runtime_policy_path = (Join-Path $projectPath "project_runtime_policy.json")
        file_hash_snapshot_path = (Join-Path $projectPath "file_hash_snapshot.json")
        control_script_path = (Join-Path $projectPath "project_control.ps1")
        validator_script_path = (Join-Path $projectPath "validate_project.ps1")
        validation_ok = $projectValidation.ok
        self_check_ok = ($projectSelfCheck.tests_ok -and $projectSelfCheck.validator_ok)
        structure_sample_count = @($projectStructure).Count
    }

    $jsonPath = Join-Path $reportsDir ("pipeline_{0}_{1}.json" -f $Name, (Get-Date -Format "yyyyMMdd_HHmmss"))
    $mdPath   = Join-Path $reportsDir ("pipeline_{0}_{1}.md"   -f $Name, (Get-Date -Format "yyyyMMdd_HHmmss"))

    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $jsonPath -Encoding UTF8
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $projectPath "pipeline_summary.json") -Encoding UTF8

    @"
# Jarvis Pipeline Report

- Project: $Name
- Status: success
- Project path: $projectPath
- Started at: $($startedAt.ToString("s"))
- Finished at: $($finishedAt.ToString("s"))
- Duration: $durationSec sec
- Venv created: $venvCreated
- Venv reused: $venvReused
- Tests passed: $testOk
- Health-check passed: $healthOk
- Git initialized: $gitOk
- Port: $Port
- Manifest: $(Join-Path $projectPath "project_manifest.json")
- Environment snapshot: $(Join-Path $projectPath "environment_snapshot.json")
- Requirements lock: $(Join-Path $projectPath "requirements.lock.txt")
- Python files: $($projectDiagnose.python_files)
- Test files: $($projectDiagnose.test_files)
- Mission manifest: $(Join-Path $projectPath "mission_manifest.json")
- Tool registry: $(Join-Path $projectPath "tool_registry.json")
- State snapshot: $(Join-Path $projectPath "project_state_snapshot.json")
- Validation report: $(Join-Path $projectPath "project_validation.json")
- Self-check report: $(Join-Path $projectPath "project_self_check.json")
- Runtime policy: $(Join-Path $projectPath "project_runtime_policy.json")
- File hash snapshot: $(Join-Path $projectPath "file_hash_snapshot.json")
- Control script: $(Join-Path $projectPath "project_control.ps1")
- Validator script: $(Join-Path $projectPath "validate_project.ps1")
- Validation OK: $($projectValidation.ok)
- Self-check OK: $($projectSelfCheck.tests_ok -and $projectSelfCheck.validator_ok)
"@ | Set-Content -LiteralPath $mdPath -Encoding UTF8

    Write-Step "SUCCESS: PROJECT READY, TESTED, HEALTH-CHECKED, REPORTED"
    Write-Host "JSON report: $jsonPath"
    Write-Host "MD report:   $mdPath"
}
catch {
    $finishedAt = Get-Date
    $durationSec = [math]::Round((New-TimeSpan -Start $startedAt -End $finishedAt).TotalSeconds, 2)

    $errorMessage = $_.Exception.Message
    Write-Host "PIPELINE FAILED: $errorMessage"

    $failReport = [ordered]@{
        project_name = $Name
        project_path = (Join-Path $Root "jarvis_stage3_artifacts\generated_projects\$Name")
        status = "failed"
        started_at = $startedAt.ToString("s")
        finished_at = $finishedAt.ToString("s")
        duration_seconds = $durationSec
        venv_created = $venvCreated
        venv_reused = $venvReused
        tests_passed = $testOk
        health_check_passed = $healthOk
        git_initialized = $gitOk
        port = $Port
        error = $errorMessage
    }

    $jsonPath = Join-Path $reportsDir ("pipeline_{0}_{1}_FAILED.json" -f $Name, (Get-Date -Format "yyyyMMdd_HHmmss"))
    $mdPath   = Join-Path $reportsDir ("pipeline_{0}_{1}_FAILED.md"   -f $Name, (Get-Date -Format "yyyyMMdd_HHmmss"))

    $failReport | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

    @"
# Jarvis Pipeline Report

- Project: $Name
- Status: failed
- Error: $errorMessage
- Started at: $($startedAt.ToString("s"))
- Finished at: $($finishedAt.ToString("s"))
- Duration: $durationSec sec
- Tests passed: $testOk
- Health-check passed: $healthOk
- Git initialized: $gitOk
- Port: $Port
- Manifest: $(Join-Path $projectPath "project_manifest.json")
- Environment snapshot: $(Join-Path $projectPath "environment_snapshot.json")
- Requirements lock: $(Join-Path $projectPath "requirements.lock.txt")
- Python files: $($projectDiagnose.python_files)
- Test files: $($projectDiagnose.test_files)
- Mission manifest: $(Join-Path $projectPath "mission_manifest.json")
- Tool registry: $(Join-Path $projectPath "tool_registry.json")
- State snapshot: $(Join-Path $projectPath "project_state_snapshot.json")
- Validation report: $(Join-Path $projectPath "project_validation.json")
- Self-check report: $(Join-Path $projectPath "project_self_check.json")
- Runtime policy: $(Join-Path $projectPath "project_runtime_policy.json")
- File hash snapshot: $(Join-Path $projectPath "file_hash_snapshot.json")
- Control script: $(Join-Path $projectPath "project_control.ps1")
- Validator script: $(Join-Path $projectPath "validate_project.ps1")
- Validation OK: $($projectValidation.ok)
- Self-check OK: $($projectSelfCheck.tests_ok -and $projectSelfCheck.validator_ok)
"@ | Set-Content -LiteralPath $mdPath -Encoding UTF8

    Write-Host "Failure report JSON: $jsonPath"
    Write-Host "Failure report MD:   $mdPath"
    exit 1
}
finally {
    if ($serverProcess -and !$serverProcess.HasExited) {
        try {
            Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
            Write-Step "Server process stopped"
        }
        catch {
        }
    }

    if ($lockFile -and (Test-Path -LiteralPath $lockFile)) {
        try {
            Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
            Write-Step "Pipeline lock removed"
        }
        catch {
        }
    }
}





