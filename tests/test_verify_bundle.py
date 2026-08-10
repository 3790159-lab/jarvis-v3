# -*- coding: utf-8 -*-
"""`scripts/verify_bundle.py` — приёмка бандла секретов РАСШИФРОВКОЙ.

Зачем инструмент вообще существует. Экспорт бандла печатает «OK» по факту того,
что файл записался, — а собирается бандл из того, что лежит на диске. 2026-08-10
`.env.enc` был от 23.07, и переэкспорт дал бы файл ровно такого же вида, с тем же
весом и свежим mtime, но БЕЗ `R2_BACKUP_BUCKET`. «OK» при экспорте этого не
показывает; девятая проверка сторожа — тоже (она сравнивает ДАТЫ, не содержимое,
см. PROBLEMS P26). Единственное доказательство пригодности — открыть бандл
паролем и посмотреть, что внутри.

Отсюда и форма тестов: каждый красный сценарий — это бандл, который выглядит
здоровым снаружи (файл есть, вес нормальный, пароль подходит) и негоден внутри.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "verify_bundle_tool", ROOT / "scripts" / "verify_bundle.py")
vb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vb)

from chatter.security.recovery import export_bundle_to_file  # noqa: E402

PW = "правильный-пароль"
ENV_OK = (b"# comment\n"
          b"BOT_TOKEN=123:AAA-secret-token\n"
          b"R2_BACKUP_BUCKET=jarvis-secrets\n"
          b"  INDENTED_KEY = spaced-value\n")


def _bundle(env: bytes | None = ENV_OK, sessions=("volska",), entropy=True) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if env is not None:
        out[".env"] = env
    for slug in sessions:
        out["%s.session" % slug] = b"1BVtsOK-session-string-%s" % slug.encode()
    if entropy:
        out["entropy.bin"] = b"\x00\x01\x02\x03"
    return out


def _write(tmp_path: Path, bundle: dict[str, bytes], name="jarvis-secrets-2026-08-10.jrvbak") -> Path:
    path = tmp_path / name
    export_bundle_to_file(path, bundle, PW)
    return path


def _run(path, capsys, *, pw=PW, argv_extra=(), require=()):
    argv = [str(path), *argv_extra]
    for key in require:
        argv += ["--require", key]
    code = vb.main(argv, ask_password=lambda _prompt="": pw)
    return code, capsys.readouterr().out


# ── чистая часть: разбор .env ───────────────────────────────────────────────

def test_env_key_names_reads_plain_and_indented_keys():
    assert vb.env_key_names(ENV_OK) == {"BOT_TOKEN", "R2_BACKUP_BUCKET", "INDENTED_KEY"}


def test_env_key_names_ignores_commented_out_keys():
    """Закомментированный ключ — это ОТСУТСТВУЮЩИЙ ключ. Иначе бандл, где
    `R2_BACKUP_BUCKET` закомментировали при отладке, зачтётся пригодным."""
    assert vb.env_key_names(b"# R2_BACKUP_BUCKET=x\n#R2_OTHER=y\nLIVE=1\n") == {"LIVE"}


def test_env_key_names_ignores_lines_without_assignment():
    assert vb.env_key_names(b"JUST_A_WORD\nOK=1\n") == {"OK"}


# ── inspect_bundle: пригоден ────────────────────────────────────────────────

def test_healthy_bundle_is_fit():
    rep = vb.inspect_bundle(_bundle())
    assert rep["ok"] is True
    assert rep["problems"] == []


def test_report_lists_sessions_sorted():
    rep = vb.inspect_bundle(_bundle(sessions=("volska", "anya", "demo")))
    assert rep["sessions"] == ["anya", "demo", "volska"]


def test_report_counts_env_keys_not_bytes_only():
    rep = vb.inspect_bundle(_bundle())
    assert rep["env_keys"] == 3 and rep["env_bytes"] == len(ENV_OK)


# ── inspect_bundle: негоден, хотя снаружи здоров ────────────────────────────

def test_missing_required_env_key_is_unfit():
    """Случай 10.08: бандл собрался из `.env.enc` от 23.07 — файл на вид тот же,
    ключа внутри нет."""
    env = ENV_OK.replace(b"R2_BACKUP_BUCKET=jarvis-secrets\n", b"")
    rep = vb.inspect_bundle(_bundle(env=env))
    assert rep["ok"] is False
    assert rep["missing_env"] == ["R2_BACKUP_BUCKET"]
    assert any("R2_BACKUP_BUCKET" in p for p in rep["problems"]), \
        "проблема не названа — владелец не узнает, ЧЕГО не хватает"


def test_bundle_without_sessions_is_unfit():
    """Бандл без сессий восстанавливает .env и не восстанавливает клиентов —
    каждый логинится заново. Это не «частично пригоден», это негоден."""
    rep = vb.inspect_bundle(_bundle(sessions=()))
    assert rep["ok"] is False and rep["sessions"] == []


def test_bundle_without_entropy_is_unfit():
    """Без entropy.bin DPAPI-материал не расшифруется на новой машине."""
    rep = vb.inspect_bundle(_bundle(entropy=False))
    assert rep["ok"] is False
    assert any("entropy" in p for p in rep["problems"])


def test_bundle_without_env_is_unfit_and_does_not_crash():
    rep = vb.inspect_bundle(_bundle(env=None))
    assert rep["ok"] is False
    assert rep["env_keys"] is None
    assert any(".env" in p for p in rep["problems"])


def test_extra_required_keys_are_honoured():
    rep = vb.inspect_bundle(_bundle(), required_env=("R2_BACKUP_BUCKET", "FAL_KEY"))
    assert rep["ok"] is False and rep["missing_env"] == ["FAL_KEY"]


# ── печать: имена и факты, НИКОГДА значения ─────────────────────────────────

def test_output_never_prints_secret_values(tmp_path, capsys):
    """Инструмент запускают в шелле, чей вывод уезжает в транскрипт сессии.
    Печатаем имена ключей и факты наличия — значения не печатаем никогда."""
    code, out = _run(_write(tmp_path, _bundle()), capsys)
    assert code == 0
    assert "123:AAA-secret-token" not in out
    assert "1BVtsOK-session-string" not in out
    assert "spaced-value" not in out
    assert "R2_BACKUP_BUCKET" in out, "имя ключа печатать НУЖНО — иначе отчёт бесполезен"


def test_report_carries_no_secret_material():
    """Автомат вместо ритуала (DEV-26): значений нет не потому, что печать
    аккуратная, а потому что их НЕТ в отчёте. Тест ловит удобную добавку
    вроде `env_preview` в момент её появления, а не на ревью вывода."""
    rep = vb.inspect_bundle(_bundle())
    blob = repr(rep)
    for secret in ("123:AAA-secret-token", "1BVtsOK-session-string", "spaced-value"):
        assert secret not in blob, "в отчёт просочилось значение секрета: %s" % secret


def test_password_is_not_echoed(tmp_path, capsys):
    code, out = _run(_write(tmp_path, _bundle()), capsys)
    assert code == 0 and PW not in out


# ── CLI: коды возврата ──────────────────────────────────────────────────────

def test_fit_bundle_exits_zero_with_verdict(tmp_path, capsys):
    code, out = _run(_write(tmp_path, _bundle()), capsys)
    assert code == 0
    assert "пригоден" in out


def test_unfit_bundle_exits_one_and_says_do_not_replace(tmp_path, capsys):
    env = ENV_OK.replace(b"R2_BACKUP_BUCKET=jarvis-secrets\n", b"")
    code, out = _run(_write(tmp_path, _bundle(env=env)), capsys)
    assert code == 1, "негодный бандл с кодом 0 — это тот же ложный «OK», от которого инструмент и защищает"
    assert "НЕПРИГОДЕН" in out


def test_wrong_password_exits_one_without_traceback(tmp_path, capsys):
    code, out = _run(_write(tmp_path, _bundle()), capsys, pw="не тот пароль")
    assert code == 1
    assert "Traceback" not in out
    assert "СТОП" in out


def test_missing_file_exits_two(tmp_path, capsys):
    code, out = _run(tmp_path / "нет-такого.jrvbak", capsys)
    assert code == 2


def test_no_arguments_exits_two(capsys):
    code = vb.main([], ask_password=lambda _prompt="": PW)
    assert code == 2


def test_not_a_bundle_exits_one(tmp_path, capsys):
    junk = tmp_path / "junk.jrvbak"
    junk.write_bytes("это не бандл".encode("utf-8"))
    code, out = _run(junk, capsys)
    assert code == 1 and "СТОП" in out


def test_require_flag_can_add_keys_from_cli(tmp_path, capsys):
    code, out = _run(_write(tmp_path, _bundle()), capsys, require=("FAL_KEY",))
    assert code == 1 and "FAL_KEY" in out


# ── пароль берётся ТОЛЬКО промптом ──────────────────────────────────────────

def test_password_is_never_taken_from_argv(tmp_path, capsys):
    """Пароль в argv светится в списке процессов и в истории шелла. Тест
    различающий: правильный пароль лежит в argv, промпт отдаёт неправильный —
    если бы CLI подхватывал argv, бандл открылся бы и код был бы 0."""
    path = _write(tmp_path, _bundle())
    code, _ = _run(path, capsys, pw="не тот пароль", argv_extra=(PW,))
    assert code == 1


def test_prompt_is_asked_exactly_once(tmp_path, capsys):
    calls = []
    code = vb.main([str(_write(tmp_path, _bundle()))],
                   ask_password=lambda prompt="": (calls.append(prompt), PW)[1])
    capsys.readouterr()
    assert code == 0 and len(calls) == 1
