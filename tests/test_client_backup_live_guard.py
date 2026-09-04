# -*- coding: utf-8 -*-
"""Застава: суита не смеет заливать клиентский набор в БОЕВОЙ бакет.

Замер 04.09.2026. `tests/test_state_backup_script.py::test_main_success_...`
зовёт настоящий `main()` скрипта, подменяя `run_backup`, `rotate_all_backups`
и `send_telegram`, но НЕ подменяя `run_client_backup`, который `main()` зовёт.
В worktree `.env` — ссылка на живой, значит боевые ключи R2 на месте;
`.secrets/*.db` там нет, а `requisites.yaml` туда копирует `new_worktree.py`.
Итог: каждый полный прогон суиты заливал ОДИН объект и ПЕРЕЗАПИСЫВАЛ боевой
`manifest.json` описанием из одной записи. Замер по датам: 01.09, 03.09 и
04.09 испорчены, 02.09 уцелело — в тот день полного прогона после ночной
задачи не было.

🔴 КАКУЮ ВЕТКУ ЭТИ СТОРОЖА ИСПОЛНЯЮТ: двойное условие в самом начале
`run_client_backup` — «признак pytest» И «фактически используется настоящий
загрузчик». Не одно из двух: блокировать всё под pytest нельзя, вокруг
заливки полно законных тестов, и они передают свой `upload_file`.

🔴 ПОЧЕМУ СРАВНЕНИЕ ИДЁТ С ОБЪЕКТОМ, А НЕ С АТРИБУТОМ МОДУЛЯ. Значение по
умолчанию `upload_file` привязано на этапе ОПРЕДЕЛЕНИЯ функции. Тест, который
подменил `r2_storage.upload_file` и не передал аргумент, продолжает
пользоваться ОРИГИНАЛОМ — проверено замером. Застава, спрашивающая «а это
`r2_storage.upload_file`?», такой тест пропустила бы и была бы декоративной.

🔴 БЕЗОПАСНОСТЬ САМИХ СТОРОЖЕЙ. Все они работают на ПУСТОМ временном дереве и
БЕЗ ключа в окружении, поэтому даже без заставы ни один не дошёл бы до
загрузки: фейл-клоуз на ключе стоит раньше. Ни одного живого обращения к R2.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from app.services import state_backup as sb  # noqa: E402


def _recorder(*_a, **_kw) -> str:
    """Подставной загрузчик. Если его позовут — тест это увидит."""
    raise AssertionError("подставной загрузчик не должен был понадобиться")


@pytest.fixture
def empty_tree(tmp_path: Path) -> Path:
    """Дерево без клиентов: даже без заставы заливать здесь нечего."""
    (tmp_path / "chatter" / "clients").mkdir(parents=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_real_key(monkeypatch):
    """Ключа в окружении нет — второй пояс безопасности сторожей."""
    monkeypatch.delenv("JARVIS_BACKUP_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("JARVIS_BACKUP_KEY_SALT", raising=False)


# ── 1. боевой случай: под pytest и с настоящим загрузчиком ────────────────

def test_under_pytest_with_the_real_uploader_is_blocked(empty_tree):
    """СЕГОДНЯ ЭТО КРАСНОЕ. Ровно та комбинация, что портила бакет.

    Застава обязана стоять ПЕРВОЙ — раньше фейл-клоуза на ключе. Иначе на
    машине с ключом (а это наша машина) до неё бы просто не доходило."""
    with pytest.raises(sb.LiveClientBackupBlocked):
        sb.run_client_backup(empty_tree)


# ── 2. законные тесты не задеты ───────────────────────────────────────────

def test_injected_uploader_is_not_blocked(empty_tree):
    """Тест, передавший свой загрузчик, обязан идти дальше заставы.

    Дальше он упирается в фейл-клоуз на ключе — это и доказывает, что застава
    его ПРОПУСТИЛА, а не то, что она молчит вообще."""
    with pytest.raises(sb.ClientBackupRefused):
        sb.run_client_backup(empty_tree, upload_file=_recorder)


# ── 3. тип отказа не тонет в иерархии бэкапа ──────────────────────────────

def test_the_refusal_has_its_own_type_outside_the_backup_hierarchy():
    """`main()` ловит `ClientBackupRefused` и трактует его как СОСТОЯНИЕ
    настройки: rc остаётся 0, отличается только строка сводки. Будь застава
    наследником — её отказ стал бы тихой строчкой вместо аварии."""
    assert not issubclass(sb.LiveClientBackupBlocked, sb.ClientBackupRefused), (
        "отказ заставы наследует ClientBackupRefused — его проглотит main()")


# ── 4. ВСТРЕЧНЫЙ: без признака застава ОБЯЗАНА МОЛЧАТЬ ────────────────────
#
# Ошибка в эту сторону дороже первой: заблокировать ночную заливку значит
# оставить клиентов без бэкапа, и узнаем мы об этом позже всех.

_SUBPROCESS = """
import sys
sys.path.insert(0, {root!r})
from pathlib import Path
from app.services import state_backup as sb

def _recorder(*a, **kw):
    print("UPLOAD_CALLED")
    return "x"

try:
    sb.run_client_backup(Path({tree!r}), upload_file=_recorder)
    print("RESULT:no-exception")
except sb.ClientBackupRefused as exc:
    print("RESULT:ClientBackupRefused")
except Exception as exc:
    print("RESULT:" + type(exc).__name__)
"""


def test_without_the_pytest_marker_the_guard_stays_silent(empty_tree, tmp_path):
    """Подпроцесс БЕЗ `PYTEST_CURRENT_TEST` — форма ночной задачи.

    Застава не смеет сработать: доказательством служит то, что процесс дошёл
    до фейл-клоуза на ключе, то есть прошёл заставу насквозь. Сети нет —
    загрузчик подставной, дерево пустое, ключа нет."""
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env.pop("JARVIS_BACKUP_PUBLIC_KEY", None)
    env.pop("JARVIS_BACKUP_KEY_SALT", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    code = _SUBPROCESS.format(root=str(ROOT), tree=str(empty_tree))
    got = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120)
    out = (got.stdout or "") + (got.stderr or "")

    assert "PYTEST_CURRENT_TEST" not in env, "предпосылка сторожа сломана"
    assert "RESULT:LiveClientBackupBlocked" not in out, (
        "застава сработала БЕЗ признака теста — так она заблокирует ночную "
        "заливку, и клиенты останутся без бэкапа:\n" + out[-2000:])
    assert "RESULT:ClientBackupRefused" in out, (
        "процесс не дошёл до фейл-клоуза на ключе — значит сторож не доказал, "
        "что застава его пропустила:\n" + out[-2000:])
    assert "UPLOAD_CALLED" not in out, "сторож дошёл до загрузки — так нельзя"


# ── 5. СКВОЗНОЙ: тот самый путь, что портил бакет ─────────────────────────
#
# Пункты 1–4 проверяют функцию. Этот — весь ход целиком: `main()` скрипта,
# ровно с тем набором подмен, который стоял в
# `test_state_backup_script.py::test_main_success_...` и который заливал
# по-настоящему. Единственное прямое доказательство, что застава работает на
# БОЕВОМ случае, а не на выдуманном.
#
# Сам тот тест теперь герметичен (подменяет `run_client_backup` фикстурой), и
# именно поэтому нужен ЭТОТ сторож: если герметичность когда-нибудь снимут,
# упереться должно в заставу, а не в боевой бакет.

def _load_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "state_backup_script_under_guard", ROOT / "scripts" / "state_backup.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["state_backup_script_under_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_script_path_that_corrupted_the_bucket_now_hits_the_guard(monkeypatch):
    """`main()` обязан ПРОБРОСИТЬ отказ заставы, а не превратить его в строку.

    В `main()` стоит широкий `except Exception`, который сделал бы из отказа
    обычное «клиентский набор упал»: rc=1, строчка в сводке — и сторож,
    поставленный ровно на этот случай, утонул бы в собственном рапорте."""
    mod = _load_script()

    result = sb.BackupResult(date="2026-07-15", uploaded=["users.json"],
                             manifest_key="backups/state/2026-07-15/manifest.json",
                             total_bytes=100)
    monkeypatch.setattr(sb, "run_backup", lambda root: result)
    monkeypatch.setattr(sb, "rotate_all_backups",
                        lambda: {"backups/state": [], "backups/client": []})
    monkeypatch.setattr(mod, "send_telegram", lambda text: True)
    # `run_client_backup` НАМЕРЕННО не подменяем — это и есть боевой случай.

    with pytest.raises(sb.LiveClientBackupBlocked) as caught:
        mod.main([])

    assert "manifest.json" in str(caught.value), (
        "сообщение отказа не называет, чем это грозит: %s" % caught.value)


# ── 6. сравнение идёт с ОБЪЕКТОМ, а не с атрибутом модуля ─────────────────

def test_patching_the_module_attribute_does_not_slip_past_the_guard(
        empty_tree, monkeypatch):
    """Тест подменил `r2_storage.upload_file` и НЕ передал аргумент.

    Умолчание привязано на этапе определения функции, поэтому фактически
    работает ОРИГИНАЛ — то есть заливка была бы настоящей. Застава,
    сравнивающая с текущим атрибутом модуля, здесь бы промолчала и оказалась
    декоративной: ровно тот случай, которым я вчера сам испортил манифест,
    думая, что перехватил загрузку."""
    from app.services import r2_storage

    monkeypatch.setattr(r2_storage, "upload_file",
                        lambda *a, **k: "подменено, но не используется")

    with pytest.raises(sb.LiveClientBackupBlocked):
        sb.run_client_backup(empty_tree)
