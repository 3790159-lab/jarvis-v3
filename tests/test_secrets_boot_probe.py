# -*- coding: utf-8 -*-
"""P1/P2 задача 4: boot-probe приёмки «декрипт при AtStartup-старте».

Probe гоняет ПРОДАКШН-пути расшифровки (load_env / load_string_session,
machine-scope DPAPI + entropy) на ОДНОРАЗОВЫХ фикстурах в отдельном
каталоге — живые .secrets/.env не трогаются никогда. Регистрируется
TEMP-таском AtStartup/S4U; вердикт после ребута читается из
probe_result.log. Probe не имеет права умирать молча: любой сбой = FAIL
строка в лог (DEV-18)."""
import importlib.util as _ilu
import sys
from pathlib import Path as _P

import pytest

_spec = _ilu.spec_from_file_location(
    "secrets_boot_probe",
    _P(__file__).resolve().parent.parent / "scripts" / "secrets_boot_probe.py")
probe = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(probe)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI есть только на Windows")


def test_setup_creates_fixtures_idempotently(tmp_path):
    d = tmp_path / "probe"
    acl_calls: list[str] = []
    probe.setup_fixtures(d, acl=lambda p: acl_calls.append(str(p)))
    entropy = d / "entropy.bin"
    assert entropy.stat().st_size == 32
    assert (d / "probe.env.enc").exists()
    assert (d / "probe.session.enc").exists()
    assert str(d) in acl_calls  # каталог закрыт ACL
    # идемпотентность: повторный setup не пересоздаёт entropy (иначе
    # существующие блобы стали бы нечитаемы)
    before = entropy.read_bytes()
    probe.setup_fixtures(d, acl=lambda p: None)
    assert entropy.read_bytes() == before


def test_run_probe_ok_on_good_fixtures(tmp_path):
    d = tmp_path / "probe"
    probe.setup_fixtures(d, acl=lambda p: None)
    ok, line = probe.run_probe(d)
    assert ok is True
    assert "OK" in line
    assert "uptime=" in line           # доказательная привязка ко времени бута
    # значения фикстур в лог не пишутся (гигиена: лог не канал утечки)
    assert "boot-probe-ok" not in line


def test_run_probe_missing_entropy_is_fail_line_not_crash(tmp_path):
    d = tmp_path / "probe"
    probe.setup_fixtures(d, acl=lambda p: None)
    (d / "entropy.bin").unlink()
    ok, line = probe.run_probe(d)
    assert ok is False
    assert "FAIL" in line
    assert "entropy" in line           # причина видна из строки


def test_run_probe_tampered_blob_is_fail(tmp_path):
    d = tmp_path / "probe"
    probe.setup_fixtures(d, acl=lambda p: None)
    blob = bytearray((d / "probe.env.enc").read_bytes())
    blob[-1] ^= 0xFF
    (d / "probe.env.enc").write_bytes(bytes(blob))
    ok, line = probe.run_probe(d)
    assert ok is False and "FAIL" in line


def test_main_appends_result_log(tmp_path, capsys):
    d = tmp_path / "probe"
    probe.setup_fixtures(d, acl=lambda p: None)
    assert probe.main(["--dir", str(d)]) == 0
    assert probe.main(["--dir", str(d)]) == 0
    log_lines = (d / "probe_result.log").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(log_lines) == 2         # append, не перезапись
    assert all("OK" in ln for ln in log_lines)
