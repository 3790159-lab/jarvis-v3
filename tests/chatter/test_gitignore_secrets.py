from __future__ import annotations
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GITIGNORE = REPO_ROOT / ".gitignore"


def test_gitignore_contains_secrets_line():
    """Belt: the raw .gitignore text must contain the /.secrets/ rule --
    checked directly (not just via `git check-ignore`) so this test still
    means something in an environment with no git binary / no repo."""
    text = GITIGNORE.read_text(encoding="utf-8")
    assert "/.secrets/" in text.splitlines(), (
        ".gitignore must contain a literal '/.secrets/' line -- the Telethon "
        "session file under .secrets/ grants full account access and must "
        "never be committed (spec S2)"
    )


def test_secrets_dir_is_actually_git_ignored():
    """Suspenders: ask git itself. The Telethon session path doesn't need to
    exist yet -- `git check-ignore` only consults .gitignore rules, it
    doesn't require the file to be present on disk."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".secrets/chatter_telethon.session"],
            cwd=REPO_ROOT, capture_output=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        import pytest
        pytest.skip("git binary unavailable in this environment")
    assert result.returncode == 0, (
        "git does not consider .secrets/chatter_telethon.session ignored -- "
        f"check-ignore exit={result.returncode} stderr={result.stderr!r}"
    )
