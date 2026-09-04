# -*- coding: utf-8 -*-
"""Дрил восстановления обязан получать конфигурацию бакета САМ.

Замер 04.09.2026: задача `JarvisRestoreDrill` падает с `rc=2` — «конфигурация
бакета бэкапа не собралась (R2ConfigError)». Пять переменных
(`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`,
`R2_BACKUP_BUCKET`) живут ТОЛЬКО в `.env`; в User и Machine их нет ни одной, а
`scripts/restore_drill.py` `app.env_bootstrap` не импортировал. Писатель
бэкапа импортирует — поэтому ЗАЛИВКА шла, а ПРОВЕРКА молчала неделю, и
единственным следом был `LastTaskResult=2` в планировщике.

🔴 КАКУЮ ВЕТКУ ЭТИ СТОРОЖА ИСПОЛНЯЮТ (называю вслух, потому что дважды
обжёгся): модульный `import app.env_bootstrap` в `scripts/restore_drill.py` и
его сайд-эффект — переменные `.env` оказываются в `os.environ` ДО того, как
`load_backup_config()` их спросит. Никаких подмен и никаких инъекций: запуск
в ОТДЕЛЬНОМ ПРОЦЕССЕ, ровно как это делает планировщик.

🔴 ПОЧЕМУ CWD — ВРЕМЕННЫЙ КАТАЛОГ. В корне репозитория лежит
`sitecustomize.py`, который сам зовёт `load_dotenv`. Он подхватывается
интерпретатором, только если корень попал в `sys.path` на старте (например,
когда процесс запущен ИЗ корня), и молчит, когда скрипт запускают по пути —
как это делает задача. Не уведи мы cwd в сторону, сторож зеленел бы от
`sitecustomize`, а боевая задача продолжала бы падать: зелёное по не той
причине. Третий путь загрузки `.env` назван здесь вслух, чтобы следующий
читатель не искал его заново.

Сети нет: подпроцесс только импортирует и собирает конфиг, в R2 не ходит.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRILL = ROOT / "scripts" / "restore_drill.py"
ENV_FILE = ROOT / ".env"

from app.services.state_backup import _REQUIRED_ENV  # noqa: E402


def _env_file_declares_all() -> bool:
    """Объявляет ли `.env` все пять переменных.

    Если нет — сторожа НЕ зеленеют молча, а пропускаются с причиной: их
    предпосылка не выполнена, и тихое «passed» было бы враньём."""
    try:
        text = ENV_FILE.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return False
    lines = [ln.strip() for ln in text.splitlines()]
    return all(any(ln.startswith(name + "=") for ln in lines)
               for name in _REQUIRED_ENV)


needs_env_file = pytest.mark.skipif(
    not _env_file_declares_all(),
    reason="`.env` этой машины не объявляет все пять R2-переменных — "
           "предпосылка сторожа не выполнена, зелёное было бы враньём")


def _run(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Подпроцесс с ВЫЧИЩЕННЫМИ R2-переменными и cwd вне корня репозитория."""
    env = {k: v for k, v in os.environ.items() if k not in _REQUIRED_ENV}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120)


_PROBE = """
import sys
sys.path.insert(0, {root!r})
{load}
from app.services.state_backup import load_backup_config
try:
    load_backup_config()
    print("ASSEMBLED")
except Exception as exc:
    print("REFUSED:" + type(exc).__name__)
"""

_IMPORT_DRILL = """
import importlib.util
_spec = importlib.util.spec_from_file_location("rd_under_test", {drill!r})
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
"""


@needs_env_file
def test_importing_the_drill_makes_the_bucket_config_assemble(tmp_path):
    """СЕГОДНЯ ЭТО КРАСНОЕ. Импорт дрила обязан принести конфигурацию с собой.

    Это и есть та строка, ради которой сторож написан: без модульного
    `import app.env_bootstrap` подпроцесс с вычищенным окружением получает
    `R2ConfigError` — ровно то, что видит планировщик с 30.08."""
    got = _run(_PROBE.format(root=str(ROOT),
                             load=_IMPORT_DRILL.format(drill=str(DRILL))),
               tmp_path)
    out = (got.stdout or "") + (got.stderr or "")
    assert "ASSEMBLED" in out, (
        "дрил импортирован, а конфигурация бакета не собралась — задача "
        "проверки бэкапа остаётся мёртвой:\n" + out[-2000:])


@needs_env_file
def test_control_without_the_drill_import_the_config_is_refused(tmp_path):
    """ВСТРЕЧНЫЙ. Без импорта дрила конфигурация собраться НЕ ДОЛЖНА.

    Без него первый сторож ничего не доказывает: он был бы зелёным и от
    протёкшей переменной родителя, и от `sitecustomize`, подобравшего `.env`
    сам. Здесь тот же подпроцесс, то же вычищенное окружение, тот же cwd — и
    отказ обязателен."""
    got = _run(_PROBE.format(root=str(ROOT), load=""), tmp_path)
    out = (got.stdout or "") + (got.stderr or "")
    assert "REFUSED:R2ConfigError" in out, (
        "конфигурация собралась БЕЗ дрила — значит первый сторож зелен не "
        "потому, что дрил грузит `.env`, а по постороннней причине "
        "(протёкшее окружение или sitecustomize):\n" + out[-2000:])
