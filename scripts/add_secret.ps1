# DEV-2: secret channel for .env - add or rotate a secret without EVER putting
# its value in a command-line argument, a script argument, or PowerShell
# history.
#
# How the value stays out of history/argv:
#   - The value is read via `Read-Host -AsSecureString` (masked terminal
#     input). PSReadLine/console history records typed COMMAND LINES, not
#     characters typed at an interactive prompt inside a running script - so
#     the secret never lands in $HOME/.../ConsoleHost_history.txt.
#   - Add-Secret takes a [SecureString], never a plain [string] - there is no
#     parameter path that accepts the raw value as an argument, so it can
#     never appear in argv, transcripts, or shell history either.
#   - The plaintext only ever exists in a local variable for the duration of
#     the .env write, then is dropped ($plain = $null).
#
# CC (Claude Code) must NEVER be given the raw value through chat - only this
# script (run interactively by Daniil) touches it. CC verifies success via the
# Present/Length/Prefix4 fields this script prints/returns - never the value
# itself. See CLAUDE.md "Секрет-канал" section.
#
# Usage (Daniil, interactive):
#   scripts\add_secret.ps1 -KeyName FAL_KEY
#     -> prompts (masked) for the value, adds or rotates FAL_KEY in .env,
#        backs up .env first, records metadata-only in state/secrets_ledger.md.

param(
    [string]$KeyName,
    [string]$EnvPath,
    [string]$LedgerPath,
    # Test hook: define the functions below only - never call Read-Host or
    # touch a real .env/ledger on its own. Mirrors the -NoAutoRun convention
    # in scripts/start_cc.ps1.
    [switch]$NoAutoRun
)

$ErrorActionPreference = 'Stop'

$script:RepoRoot = Split-Path -Parent $PSScriptRoot

function ConvertFrom-SecureStringPlain {
    # Single choke point for unwrapping a SecureString to plaintext, so the
    # unwrap (and the resulting plaintext lifetime) is easy to audit.
    param([Parameter(Mandatory = $true)][System.Security.SecureString]$SecureValue)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Test-SecretKeyName {
    # Guards against KeyName containing '=', newlines, etc. that could
    # corrupt the .env line format or inject extra lines.
    param([Parameter(Mandatory = $true)][string]$KeyName)
    if ($KeyName -notmatch '^[A-Z][A-Z0-9_]*$') {
        throw "Invalid -KeyName '$KeyName': must be UPPER_SNAKE_CASE (letters/digits/underscore, starting with a letter)."
    }
}

function Backup-EnvFile {
    # Auto-backup before any edit. No-op (returns $null) when .env does not
    # exist yet - nothing to back up on a first-ever Add-Secret call.
    param([Parameter(Mandatory = $true)][string]$EnvPath)
    if (-not (Test-Path -LiteralPath $EnvPath)) { return $null }
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backupPath = "$EnvPath.backup_$stamp"
    Copy-Item -LiteralPath $EnvPath -Destination $backupPath -Force
    return $backupPath
}

function Set-EnvValue {
    # Atomically add-or-replace a KEY=value line: write the full new content
    # to a temp file in the same directory, then Move-Item -Force over the
    # real path - readers never observe a partially-written .env.
    param(
        [Parameter(Mandatory = $true)][string]$EnvPath,
        [Parameter(Mandatory = $true)][string]$KeyName,
        [Parameter(Mandatory = $true)][string]$PlainValue
    )
    $lines = @()
    if (Test-Path -LiteralPath $EnvPath) {
        $lines = @(Get-Content -LiteralPath $EnvPath -Encoding UTF8)
    }
    $pattern = "^$([regex]::Escape($KeyName))="
    $isRotation = $false
    $newLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if ($line -match $pattern) {
            $newLines.Add("$KeyName=$PlainValue")
            $isRotation = $true
        } else {
            $newLines.Add($line)
        }
    }
    if (-not $isRotation) {
        $newLines.Add("$KeyName=$PlainValue")
    }

    $dir = Split-Path -Parent $EnvPath
    if (-not $dir) { $dir = '.' }
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $tmpPath = Join-Path $dir (".env.tmp_$([guid]::NewGuid().ToString('N'))")
    [System.IO.File]::WriteAllLines($tmpPath, [string[]]$newLines, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmpPath -Destination $EnvPath -Force

    return @{ IsRotation = $isRotation }
}

# Seed rows written the first time state/secrets_ledger.md is created, per
# DEV-2 spec: FAL_KEY is the one known-leaked key (светился=да); everything
# else defaults to нет until proven otherwise. Values are NEVER stored here -
# only key name / dates / exposure status.
$script:SeedKeys = [ordered]@{
    'FAL_KEY'                = 'да'
    'TELEGRAM_BOT_TOKEN'      = 'нет'
    'IG_ACCESS_TOKEN'         = 'нет'
    'IG_APP_SECRET'           = 'нет'
    'R2_ACCESS_KEY_ID'        = 'нет'
    'R2_SECRET_ACCESS_KEY'    = 'нет'
    'WAVESPEED_API_KEY'       = 'нет'
    'JARVIS_INTERNAL_API_KEY' = 'нет'
}

function New-SecretsLedgerContent {
    param([Parameter(Mandatory = $true)][string]$Today)
    $header = @(
        '# Secrets Ledger',
        '',
        'Реестр секретов Jarvis. Значения секретов здесь НИКОГДА не хранятся - ' +
        'только метаданные: имя ключа, даты, статус "светился" (когда-либо был ' +
        'виден в чате/логах/публичном месте). Файл гитигнорится (`state/`) - ' +
        'живёт только на диске исполняющей машины, не в git.',
        '',
        '| Ключ | Дата добавления | Последняя ротация | Светился |',
        '|------|------------------|--------------------|----------|'
    )
    $rows = foreach ($key in $script:SeedKeys.Keys) {
        "| $key | $Today | - | $($script:SeedKeys[$key]) |"
    }
    return @($header + $rows) -join "`n"
}

function Initialize-SecretsLedger {
    # Creates state/secrets_ledger.md with the seed rows above if (and only
    # if) it does not exist yet - idempotent, safe to call on every run.
    param([Parameter(Mandatory = $true)][string]$LedgerPath)
    if (Test-Path -LiteralPath $LedgerPath) { return }
    $dir = Split-Path -Parent $LedgerPath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $today = Get-Date -Format 'yyyy-MM-dd'
    $content = (New-SecretsLedgerContent -Today $today) + "`n"
    [System.IO.File]::WriteAllText($LedgerPath, $content, (New-Object System.Text.UTF8Encoding($false)))
}

function Update-SecretsLedger {
    # Upserts the ledger row for $KeyName: new key -> appended row with
    # "дата добавления"=today; existing key + rotation -> "последняя
    # ротация"=today, added-date and леaked status left untouched.
    param(
        [Parameter(Mandatory = $true)][string]$LedgerPath,
        [Parameter(Mandatory = $true)][string]$KeyName,
        [Parameter(Mandatory = $true)][bool]$IsRotation
    )
    Initialize-SecretsLedger -LedgerPath $LedgerPath
    $today = Get-Date -Format 'yyyy-MM-dd'
    $lines = @(Get-Content -LiteralPath $LedgerPath -Encoding UTF8)
    $pattern = "^\|\s*$([regex]::Escape($KeyName))\s*\|"
    $found = $false
    $newLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if ($line -match $pattern) {
            $found = $true
            $cells = $line.Split('|')
            $added = $cells[2].Trim()
            $leaked = $cells[4].Trim()
            if ([string]::IsNullOrWhiteSpace($added)) { $added = $today }
            $rotated = if ($IsRotation) { $today } else { $cells[3].Trim() }
            $newLines.Add("| $KeyName | $added | $rotated | $leaked |")
        } else {
            $newLines.Add($line)
        }
    }
    if (-not $found) {
        $newLines.Add("| $KeyName | $today | - | нет |")
    }

    $dir = Split-Path -Parent $LedgerPath
    if (-not $dir) { $dir = '.' }
    $tmpPath = Join-Path $dir (".secrets_ledger.tmp_$([guid]::NewGuid().ToString('N'))")
    [System.IO.File]::WriteAllLines($tmpPath, [string[]]$newLines, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmpPath -Destination $LedgerPath -Force
}

function Add-Secret {
    # Core entry point: validates KeyName, backs up .env, atomically
    # add-or-replaces the KEY= line, updates the metadata-only ledger, and
    # returns ONLY present/length/prefix4 - never the plaintext value - so
    # callers (including CC) can verify success without ever seeing the
    # secret.
    param(
        [Parameter(Mandatory = $true)][string]$KeyName,
        [Parameter(Mandatory = $true)][System.Security.SecureString]$SecureValue,
        [Parameter(Mandatory = $true)][string]$EnvPath,
        [Parameter(Mandatory = $true)][string]$LedgerPath
    )
    Test-SecretKeyName -KeyName $KeyName

    $plain = ConvertFrom-SecureStringPlain -SecureValue $SecureValue
    try {
        if ([string]::IsNullOrEmpty($plain)) {
            throw "Empty secret value for $KeyName - aborting, nothing written."
        }

        $backupPath = Backup-EnvFile -EnvPath $EnvPath
        $setResult = Set-EnvValue -EnvPath $EnvPath -KeyName $KeyName -PlainValue $plain
        Update-SecretsLedger -LedgerPath $LedgerPath -KeyName $KeyName -IsRotation $setResult.IsRotation

        $len = $plain.Length
        $prefix4 = $plain.Substring(0, [Math]::Min(4, $len))

        return [pscustomobject]@{
            KeyName    = $KeyName
            IsRotation = $setResult.IsRotation
            BackupPath = $backupPath
            Present    = $true
            Length     = $len
            Prefix4    = $prefix4
        }
    } finally {
        $plain = $null
    }
}

if (-not $NoAutoRun) {
    if (-not $KeyName) {
        throw "Usage: scripts\add_secret.ps1 -KeyName <ENV_VAR_NAME>  (e.g. -KeyName FAL_KEY)"
    }
    if (-not $EnvPath) { $EnvPath = Join-Path $script:RepoRoot '.env' }
    if (-not $LedgerPath) { $LedgerPath = Join-Path $script:RepoRoot 'state/secrets_ledger.md' }

    $secure = Read-Host -Prompt "Значение для $KeyName (ввод скрыт, не попадёт в историю)" -AsSecureString
    $result = Add-Secret -KeyName $KeyName -SecureValue $secure -EnvPath $EnvPath -LedgerPath $LedgerPath

    Write-Host ""
    Write-Host "[OK] $($result.KeyName): $(if ($result.IsRotation) { 'ротация выполнена' } else { 'добавлен новый ключ' })"
    Write-Host "  present=$($result.Present) len=$($result.Length) prefix4=$($result.Prefix4)..."
    if ($result.BackupPath) { Write-Host "  backup .env: $($result.BackupPath)" }
    Write-Host "  ledger: $LedgerPath"
}
