param(
    [string]$ClaudeCommand = "",
    [string]$OpenAICompatBaseUrl = "",
    [string]$OpenAICompatApiKey = "",
    [string]$OpenAICompatModel = "gpt-4o-mini"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$EnvPath = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvPath)) {
    New-Item -ItemType File -Path $EnvPath -Force | Out-Null
}

function Set-Or-AddEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }

    $Lines = @()
    if (Test-Path $Path) {
        $Lines = Get-Content $Path -Encoding UTF8
    }

    $Pattern = "^\s*$([regex]::Escape($Key))="
    $Found = $false
    $NewLines = foreach ($Line in $Lines) {
        if ($Line -match $Pattern) {
            $Found = $true
            "$Key=$Value"
        } else {
            $Line
        }
    }

    if (-not $Found) {
        $NewLines += "$Key=$Value"
    }

    [System.IO.File]::WriteAllLines($Path, $NewLines, [System.Text.UTF8Encoding]::new($false))
}

if (-not [string]::IsNullOrWhiteSpace($ClaudeCommand)) {
    $env:CLAUDE_CODE_COMMAND = $ClaudeCommand
    Set-Or-AddEnvValue -Path $EnvPath -Key "CLAUDE_CODE_COMMAND" -Value $ClaudeCommand
    Write-Host "Configured CLAUDE_CODE_COMMAND" -ForegroundColor Green
} else {
    Write-Host "CLAUDE_CODE_COMMAND was not provided. Claude bridge stays disabled." -ForegroundColor Yellow
}

if (-not [string]::IsNullOrWhiteSpace($OpenAICompatBaseUrl)) {
    $env:OPENAI_COMPAT_BASE_URL = $OpenAICompatBaseUrl
    Set-Or-AddEnvValue -Path $EnvPath -Key "OPENAI_COMPAT_BASE_URL" -Value $OpenAICompatBaseUrl
    Write-Host "Configured OPENAI_COMPAT_BASE_URL" -ForegroundColor Green
}

if (-not [string]::IsNullOrWhiteSpace($OpenAICompatApiKey)) {
    $env:OPENAI_COMPAT_API_KEY = $OpenAICompatApiKey
    Set-Or-AddEnvValue -Path $EnvPath -Key "OPENAI_COMPAT_API_KEY" -Value $OpenAICompatApiKey
    Write-Host "Configured OPENAI_COMPAT_API_KEY" -ForegroundColor Green
} else {
    Write-Host "OPENAI_COMPAT_API_KEY was not provided. OpenAI-compatible bridge may stay disabled." -ForegroundColor Yellow
}

if (-not [string]::IsNullOrWhiteSpace($OpenAICompatModel)) {
    $env:OPENAI_COMPAT_MODEL = $OpenAICompatModel
    Set-Or-AddEnvValue -Path $EnvPath -Key "OPENAI_COMPAT_MODEL" -Value $OpenAICompatModel
    Write-Host "Configured OPENAI_COMPAT_MODEL=$OpenAICompatModel" -ForegroundColor Green
}

Write-Host ""
Write-Host "External agent config saved to .env" -ForegroundColor Green
Write-Host "Next:" -ForegroundColor Yellow
Write-Host "1) .\scripts\restart_backend.ps1"
Write-Host "2) .\scripts\jarvis_external_agents_smoke.ps1 -BaseUrl http://127.0.0.1:8015"