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
    Write-Host "Configured CLAUDE_CODE_COMMAND=$ClaudeCommand" -ForegroundColor Green
}

if (-not [string]::IsNullOrWhiteSpace($OpenAICompatBaseUrl)) {
    if ($OpenAICompatBaseUrl -match "your-openai-compatible-endpoint|example.com") {
        throw "OpenAICompatBaseUrl still looks like a placeholder. Put a real endpoint."
    }
    $env:OPENAI_COMPAT_BASE_URL = $OpenAICompatBaseUrl
    Set-Or-AddEnvValue -Path $EnvPath -Key "OPENAI_COMPAT_BASE_URL" -Value $OpenAICompatBaseUrl
    Write-Host "Configured OPENAI_COMPAT_BASE_URL=$OpenAICompatBaseUrl" -ForegroundColor Green
}

if (-not [string]::IsNullOrWhiteSpace($OpenAICompatApiKey)) {
    $env:OPENAI_COMPAT_API_KEY = $OpenAICompatApiKey
    Set-Or-AddEnvValue -Path $EnvPath -Key "OPENAI_COMPAT_API_KEY" -Value $OpenAICompatApiKey
    Write-Host "Configured OPENAI_COMPAT_API_KEY" -ForegroundColor Green
}

if (-not [string]::IsNullOrWhiteSpace($OpenAICompatModel)) {
    $env:OPENAI_COMPAT_MODEL = $OpenAICompatModel
    Set-Or-AddEnvValue -Path $EnvPath -Key "OPENAI_COMPAT_MODEL" -Value $OpenAICompatModel
    Write-Host "Configured OPENAI_COMPAT_MODEL=$OpenAICompatModel" -ForegroundColor Green
}

Write-Host ""
Write-Host "Config updated. Restart backend next." -ForegroundColor Yellow