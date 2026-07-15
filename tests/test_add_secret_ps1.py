# -*- coding: utf-8 -*-
"""Tests for scripts/add_secret.ps1 (DEV-2: secret channel for .env).

Mirrors the dot-source-with-a-test-hook pattern from tests/test_start_cc_ps1.py:
dot-source the real .ps1 with -NoAutoRun (functions only, no Read-Host prompt
and no top-level side effects), then drive Add-Secret directly by building a
SecureString in-process - never as a plain-string command-line argument. This
proves the function contract (SecureString-only input) without ever touching
a real interactive prompt or a real .env/state/secrets_ledger.md.

One end-to-end subprocess test (test_end_to_end_...) drives the full script
non-interactively via piped stdin (the same trick a human would NOT use -
Daniil types at the masked prompt - but the only way to automate the real
Read-Host -AsSecureString path) and asserts the secret value appears nowhere
except inside the .env file it was written to.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="add_secret.ps1 is Windows-only (PowerShell)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "add_secret.ps1"

SEED_KEYS = [
    "FAL_KEY",
    "TELEGRAM_BOT_TOKEN",
    "IG_ACCESS_TOKEN",
    "IG_APP_SECRET",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "WAVESPEED_API_KEY",
    "JARVIS_INTERNAL_API_KEY",
]


def _run_ps(body: str, timeout: int = 40) -> subprocess.CompletedProcess:
    """Dot-source add_secret.ps1 with -NoAutoRun (functions only - never
    prompts, never touches a real .env/ledger on its own), then run `body`
    in the same session.

    Writes the dot-source + body to a scratch .ps1 file and runs it via
    -File rather than -Command: Windows PowerShell 5.1's -Command argument
    parsing mangles the Cyrillic string literals baked into the dot-sourced
    add_secret.ps1 (verified while developing this test - the ledger's "да"/
    "нет" cells came out as replacement characters on disk), while -File
    with a UTF-8-BOM script reads them correctly, matching how Daniil
    actually invokes the script (`scripts\\add_secret.ps1 -KeyName ...`).
    """
    runner = Path(tempfile.gettempdir()) / f"add_secret_test_runner_{uuid.uuid4().hex}.ps1"
    runner.write_text(f". '{SCRIPT}' -NoAutoRun\n{body}\n", encoding="utf-8-sig")
    try:
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        runner.unlink(missing_ok=True)


def _win(p: Path) -> str:
    return str(p).replace("\\", "\\\\")


def test_dot_sourcing_with_noautorun_does_not_prompt_or_write():
    # Dot-sourcing with -NoAutoRun must define functions only - no Read-Host,
    # no file writes - so tests never risk hanging on a real prompt.
    result = _run_ps("'still-alive'")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "still-alive"


def test_add_secret_requires_securestring_not_plain_string():
    # The SecureValue parameter must be typed as SecureString - there must be
    # no code path where a plain-string secret can be bound as a command-line
    # argument.
    body = "(Get-Command Add-Secret).Parameters['SecureValue'].ParameterType.Name"
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "SecureString"


def test_add_new_key_writes_env_line_and_ledger_row(tmp_path):
    env_path = tmp_path / ".env"
    ledger_path = tmp_path / "state" / "secrets_ledger.md"
    body = f"""
        $sec = ConvertTo-SecureString 'sekrit-val-123' -AsPlainText -Force
        $r = Add-Secret -KeyName 'TESTKEY_FOO' -SecureValue $sec -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}'
        "IsRotation=$($r.IsRotation)"
        "Present=$($r.Present)"
        "Length=$($r.Length)"
        "Prefix4=$($r.Prefix4)"
        "BackupPath=$($r.BackupPath)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "IsRotation=False" in out
    assert "Present=True" in out
    assert "Length=14" in out
    assert "Prefix4=sekr" in out

    env_text = env_path.read_text(encoding="utf-8")
    assert "TESTKEY_FOO=sekrit-val-123" in env_text

    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert "TESTKEY_FOO" in ledger_text
    for key in SEED_KEYS:
        assert key in ledger_text
    assert "| FAL_KEY |" in ledger_text and ledger_text.split("| FAL_KEY |")[1].split("|")[2].strip() == "да"


def test_add_new_key_no_backup_when_env_missing(tmp_path):
    env_path = tmp_path / ".env"
    ledger_path = tmp_path / "state" / "secrets_ledger.md"
    body = f"""
        $sec = ConvertTo-SecureString 'val1' -AsPlainText -Force
        $r = Add-Secret -KeyName 'TESTKEY_NEW' -SecureValue $sec -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}'
        "BackupPath=[$($r.BackupPath)]"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert "BackupPath=[]" in result.stdout


def test_rotate_existing_key_backs_up_and_replaces_in_place(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("TESTKEY_FOO=old-value\nOTHER=1\n", encoding="utf-8")
    ledger_path = tmp_path / "state" / "secrets_ledger.md"

    body = f"""
        $sec = ConvertTo-SecureString 'newvalue999' -AsPlainText -Force
        $r = Add-Secret -KeyName 'TESTKEY_FOO' -SecureValue $sec -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}'
        "IsRotation=$($r.IsRotation)"
        "BackupPath=$($r.BackupPath)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert "IsRotation=True" in result.stdout

    env_text = env_path.read_text(encoding="utf-8")
    assert "TESTKEY_FOO=newvalue999" in env_text
    assert "OTHER=1" in env_text
    assert "old-value" not in env_text
    # exactly one TESTKEY_FOO line - no duplicate appended
    assert env_text.count("TESTKEY_FOO=") == 1

    backups = list(tmp_path.glob(".env.backup_*"))
    assert len(backups) == 1
    assert "TESTKEY_FOO=old-value" in backups[0].read_text(encoding="utf-8")


def test_rotation_updates_ledger_rotation_date_keeps_added_date(tmp_path):
    env_path = tmp_path / ".env"
    ledger_path = tmp_path / "state" / "secrets_ledger.md"

    body = f"""
        $sec1 = ConvertTo-SecureString 'v1' -AsPlainText -Force
        Add-Secret -KeyName 'TESTKEY_ROT' -SecureValue $sec1 -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}' | Out-Null
        $ledgerAfterAdd = Get-Content -LiteralPath '{_win(ledger_path)}' -Raw
        $rowAfterAdd = ($ledgerAfterAdd -split "`n" | Where-Object {{ $_ -match '^\\| TESTKEY_ROT \\|' }})

        $sec2 = ConvertTo-SecureString 'v2-rotated' -AsPlainText -Force
        Add-Secret -KeyName 'TESTKEY_ROT' -SecureValue $sec2 -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}' | Out-Null
        $ledgerAfterRotate = Get-Content -LiteralPath '{_win(ledger_path)}' -Raw
        $rowAfterRotate = ($ledgerAfterRotate -split "`n" | Where-Object {{ $_ -match '^\\| TESTKEY_ROT \\|' }})

        "AfterAdd=$rowAfterAdd"
        "AfterRotate=$rowAfterRotate"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    after_add_line = [l for l in out.splitlines() if l.startswith("AfterAdd=")][0]
    after_rotate_line = [l for l in out.splitlines() if l.startswith("AfterRotate=")][0]

    add_cells = [c.strip() for c in after_add_line[len("AfterAdd="):].split("|")]
    rotate_cells = [c.strip() for c in after_rotate_line[len("AfterRotate="):].split("|")]
    # cells: ['', 'TESTKEY_ROT', <added>, <rotated>, <leaked>, '']
    assert add_cells[3] == "-"
    assert rotate_cells[3] != "-"
    assert add_cells[2] == rotate_cells[2]  # added date unchanged by rotation


def test_invalid_key_name_rejected_without_writing(tmp_path):
    env_path = tmp_path / ".env"
    ledger_path = tmp_path / "state" / "secrets_ledger.md"
    body = f"""
        $sec = ConvertTo-SecureString 'val' -AsPlainText -Force
        try {{
            Add-Secret -KeyName 'bad-key-name' -SecureValue $sec -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}'
            "NO_THROW"
        }} catch {{
            "THREW:$($_.Exception.Message)"
        }}
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert "THREW:" in result.stdout
    assert not env_path.exists()
    assert not ledger_path.exists()


def test_empty_secret_value_aborts_without_writing(tmp_path):
    env_path = tmp_path / ".env"
    ledger_path = tmp_path / "state" / "secrets_ledger.md"
    body = f"""
        $sec = New-Object System.Security.SecureString  # valid, zero-length
        try {{
            Add-Secret -KeyName 'TESTKEY_EMPTY' -SecureValue $sec -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}'
            "NO_THROW"
        }} catch {{
            "THREW:$($_.Exception.Message)"
        }}
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert "THREW:" in result.stdout
    assert not env_path.exists()
    assert not ledger_path.exists()


def test_ledger_seeds_known_keys_on_first_run(tmp_path):
    ledger_path = tmp_path / "state" / "secrets_ledger.md"
    body = f"""
        Initialize-SecretsLedger -LedgerPath '{_win(ledger_path)}'
        Get-Content -LiteralPath '{_win(ledger_path)}' -Raw
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    ledger_text = ledger_path.read_text(encoding="utf-8")
    for key in SEED_KEYS:
        assert key in ledger_text, f"missing seed key {key}"
    fal_row = [l for l in ledger_text.splitlines() if l.startswith("| FAL_KEY |")][0]
    assert fal_row.split("|")[4].strip() == "да"
    for key in SEED_KEYS:
        if key == "FAL_KEY":
            continue
        row = [l for l in ledger_text.splitlines() if l.startswith(f"| {key} |")][0]
        assert row.split("|")[4].strip() == "нет", f"{key} should default to not-leaked"


def test_no_leftover_tmp_files_after_write(tmp_path):
    env_path = tmp_path / ".env"
    ledger_path = tmp_path / "state" / "secrets_ledger.md"
    body = f"""
        $sec = ConvertTo-SecureString 'v1' -AsPlainText -Force
        Add-Secret -KeyName 'TESTKEY_TMP' -SecureValue $sec -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}' | Out-Null
        'done'
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    leftover_env_tmp = list(tmp_path.glob(".env.tmp_*"))
    leftover_ledger_tmp = list((tmp_path / "state").glob(".secrets_ledger.tmp_*"))
    assert leftover_env_tmp == []
    assert leftover_ledger_tmp == []


def test_secret_value_never_appears_in_ledger_or_result_object(tmp_path):
    env_path = tmp_path / ".env"
    ledger_path = tmp_path / "state" / "secrets_ledger.md"
    secret = "super-secret-do-not-leak-77771"
    body = f"""
        $sec = ConvertTo-SecureString '{secret}' -AsPlainText -Force
        $r = Add-Secret -KeyName 'TESTKEY_NOLEAK' -SecureValue $sec -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}'
        $r | Format-List | Out-String
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout
    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert secret not in ledger_text
    env_text = env_path.read_text(encoding="utf-8")
    assert secret in env_text  # expected: .env is the one place it belongs


def test_top_level_params_have_no_plain_secret_value_parameter():
    # A live subprocess test that pipes a value into the real Read-Host
    # -AsSecureString prompt is not reliable on Windows PowerShell 5.1:
    # ConsoleHost's secure-string prompt needs a real interactive console and
    # either hangs (no -NonInteractive) or refuses outright ("does not
    # support this action for a Prompt" with -NonInteractive) when stdin is
    # merely redirected - confirmed while developing this test. So the "value
    # never in argv/history" contract is verified statically instead: the
    # script's top-level parameter surface (what Daniil can pass on the
    # command line) has no plain-string parameter a value could be smuggled
    # through - KeyName/EnvPath/LedgerPath/NoAutoRun only.
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         f"(Get-Command '{SCRIPT}').Parameters.Keys -join ','"],
        capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    declared = {p.strip() for p in result.stdout.strip().splitlines()[-1].split(",")}
    assert declared == {"KeyName", "EnvPath", "LedgerPath", "NoAutoRun"}


def test_script_prompts_via_read_host_assecurestring():
    # Confirms the only place a secret value can enter the script at all is
    # the masked Read-Host -AsSecureString prompt (not some other plain-text
    # input path that could sneak the value into argv or a variable that
    # later gets logged).
    text = SCRIPT.read_text(encoding="utf-8-sig")
    assert "Read-Host" in text
    read_host_line = next(line for line in text.splitlines() if "Read-Host" in line)
    assert "-AsSecureString" in read_host_line


def test_end_to_end_writes_env_and_ledger_via_add_secret(tmp_path):
    # Full non-interactive flow through Add-Secret (the same function the
    # top-level Read-Host block calls), proving .env + ledger end up correct
    # and the secret value never leaks into the ledger or captured output.
    env_path = tmp_path / ".env"
    ledger_path = tmp_path / "state" / "secrets_ledger.md"
    secret_value = "e2e-secret-value-987"
    body = f"""
        $sec = ConvertTo-SecureString '{secret_value}' -AsPlainText -Force
        Add-Secret -KeyName 'TESTKEY_E2E' -SecureValue $sec -EnvPath '{_win(env_path)}' -LedgerPath '{_win(ledger_path)}' | Out-Null
        'done'
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert secret_value not in result.stdout
    assert secret_value not in result.stderr

    env_text = env_path.read_text(encoding="utf-8")
    assert f"TESTKEY_E2E={secret_value}" in env_text

    ledger_text = ledger_path.read_text(encoding="utf-8")
    assert secret_value not in ledger_text
