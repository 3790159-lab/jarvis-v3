# -*- coding: utf-8 -*-
"""Мультиклиентный гардиан: изоляция процессов клиентов друг от друга.

Гоняем НАСТОЯЩИЙ .ps1 через dot-source с `-Root <tmp> -NoLoop` (паттерн
tests/test_bot_guardian_stop_old_bot.py): без Pester, без лока, без цикла и без
касания C:\\jarvis.

Фейковые раннеры — РЕАЛЬНЫЕ живые процессы, а не моки. Проверяемое поведение —
матч по командной строке и настоящий taskkill; мок здесь доказывал бы только
сам себя.

Форма командной строки боевого раннера:
    <root>\\.venv\\Scripts\\python.exe -u -m chatter.telethon_run --llm real --client <slug>
Фейк повторяет значимую часть (root + имя модуля + --client). Интерпретатор
берём системный: копия python.exe без соседних DLL просто не стартует, а под
матч он и не попадает — в шаблон входит путь СКРИПТА, который лежит под root.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="гардиан Windows-only (PowerShell + taskkill + Win32_Process)"),
    pytest.mark.skipif(
        not (Path(sys.base_prefix) / "python.exe").exists(),
        reason="нет базового интерпретатора вне C:\\jarvis — фейки были бы "
               "видимы прод-гардиану, тест опаснее пропуска"),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "chatter_guardian_detached.ps1"

# 🔴 ИНТЕРПРЕТАТОР ФЕЙКОВ БЕРЁМ БАЗОВЫЙ, А НЕ sys.executable.
#
# Под pytest sys.executable == C:\jarvis\.venv\Scripts\python.exe, то есть путь
# САМ содержит 'C:\jarvis'. Живой ПРОД-гардиан ищет раннеры шаблоном
# "*$Root*chatter.telethon_run*" при $Root='C:\jarvis' — и принимал бы каждый
# мой тестовый процесс за настоящего раннера.
#
# Опасно не то, что прод убьёт мой фейк (это терпимо), а обратное: в
# Test-Runner проверка процесса идёт ПЕРЕД heartbeat. Если volska умрёт, пока
# жив мой фейк, гардиан видит «процесс есть» и падает на второй гейт —
# heartbeat, который считается свежим ещё 180с. Восстановление и 🔴-алерт
# уезжают с ~90с до ~270с: Ольга лежит молча три минуты.
#
# Базовый интерпретатор лежит вне C:\jarvis, скрипт фейка — в pytest-tmp,
# поэтому командная строка фейка не содержит 'C:\jarvis' вовсе.
# Инвариант держится тестом test_fake_runners_are_invisible_to_prod_guardian.
BASE_PY = Path(sys.base_prefix) / "python.exe"
PROD_ROOT = r"C:\jarvis"


def _run_ps(body: str, root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    command = f". '{SCRIPT}' -Root '{root}' -NoLoop\n{body}\n"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")


def _spawn(root: Path, *extra: str) -> subprocess.Popen:
    """Живой процесс, чья командная строка содержит <root>\\chatter.telethon_run."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "chatter.telethon_run"
    script.write_text("import time\nwhile True: time.sleep(0.2)\n", encoding="utf-8")
    return subprocess.Popen(
        [str(BASE_PY), "-u", str(script), "--llm", "real", *extra],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _runner(root: Path, slug: str) -> subprocess.Popen:
    return _spawn(root, "--client", slug)


def _legacy_runner(root: Path) -> subprocess.Popen:
    """Раннер СТАРОЙ формы — без --client, как его запускал прежний скрипт."""
    return _spawn(root)


def _count(root: Path, slug: str) -> str:
    res = _run_ps(f"(Get-RunnerProcesses -Slug {slug} | Measure-Object).Count", root)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1].strip()


def _wait_dead(p: subprocess.Popen, timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while p.poll() is None and time.time() < deadline:
        time.sleep(0.3)
    return p.poll() is not None


@pytest.fixture
def kill_after():
    procs: list[subprocess.Popen] = []
    yield procs
    for p in procs:
        if p.poll() is None:
            p.kill()


# ── 🔴 сторож: фейки не должны быть видны ЖИВОМУ прод-гардиану ─────────────

def test_fake_runners_are_invisible_to_prod_guardian(tmp_path, kill_after):
    """Тестовые процессы обязаны быть невидимы гардиану, который прямо сейчас
    стережёт Ольгу.

    Опасность НЕ в том, что прод убьёт мой фейк (терпимо, максимум флейк), а в
    обратном направлении: в Test-Runner проверка процесса идёт ПЕРЕД heartbeat,
    поэтому живой посторонний матч заставляет гардиан считать мёртвого volska
    живым, пока heartbeat не протухнет (180с). Восстановление и 🔴-алерт
    уезжают с ~90с до ~270с — Ольга лежит молча три минуты.

    Проверяем ФАКТОМ по реальной командной строке запущенного процесса, а не
    по конструкции строки в тесте."""
    p = _runner(tmp_path, "aaa")
    kill_after.append(p)
    time.sleep(1.5)

    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command",
         f"(Get-CimInstance Win32_Process -Filter \"ProcessId={p.pid}\").CommandLine"],
        capture_output=True, text=True, timeout=40, encoding="utf-8", errors="replace")
    assert res.returncode == 0, res.stderr
    cmdline = res.stdout.strip()
    assert cmdline, "не удалось прочитать командную строку фейка"

    assert PROD_ROOT.lower() not in cmdline.lower(), (
        f"командная строка тестового процесса содержит {PROD_ROOT} и будет "
        f"принята живым прод-гардианом за раннера volska: {cmdline!r}")


# ── 🔴 главный регресс арки ────────────────────────────────────────────────

def test_stop_old_runner_kills_only_the_named_client(tmp_path, kill_after):
    """ДО фикса Stop-OldRunner убивал ОБОИХ: матч шёл по '*chatter.telethon_run*'
    без различения клиента. Из-за этого подъём второго клиента гасил живого
    первого — то есть второй одновременный клиент был невозможен независимо от
    хардкода semidemo-флага."""
    a = _runner(tmp_path, "aaa")
    b = _runner(tmp_path, "bbb")
    kill_after += [a, b]
    time.sleep(1.5)
    assert a.poll() is None and b.poll() is None, "фейковые раннеры не поднялись"

    res = _run_ps("Stop-OldRunner -Slug aaa | Out-Null", tmp_path)
    assert res.returncode == 0, res.stderr

    assert _wait_dead(a), "клиент aaa должен быть убит"
    assert b.poll() is None, (
        "клиент bbb ОБЯЗАН остаться живым — это и есть дефект, ради которого "
        "делается арка")


def test_get_runner_processes_is_scoped_to_slug(tmp_path, kill_after):
    """Каждый клиент видим сам по себе, чужого в выдаче нет.

    Считаем «>=1, и оба клиента видны», а не «ровно 1»: венвовый python.exe на
    Windows ре-экзекает базовый интерпретатор, поэтому здоровый раннер — это
    ДВА процесса (launcher + worker). Так и в проде; ассерт на точную единицу
    проверял бы деталь интерпретатора, а не скоуп."""
    a = _runner(tmp_path, "aaa")
    b = _runner(tmp_path, "bbb")
    kill_after += [a, b]
    time.sleep(1.5)
    n_a, n_b = int(_count(tmp_path, "aaa")), int(_count(tmp_path, "bbb"))
    assert n_a >= 1 and n_b >= 1
    assert _count(tmp_path, "ccc") == "0", "несуществующий клиент не должен находиться"


def test_slug_match_does_not_catch_prefix_siblings(tmp_path, kill_after):
    """'--client volska' не смеет матчить '--client volska2' — иначе подъём
    volska2 убил бы volska. Отсюда -match с границей токена вместо -like."""
    v2 = _runner(tmp_path, "volska2")
    kill_after.append(v2)
    time.sleep(1.5)
    assert _count(tmp_path, "volska") == "0"
    assert int(_count(tmp_path, "volska2")) >= 1


def test_root_match_does_not_catch_path_prefix_siblings(tmp_path, kill_after):
    """$Root не смеет матчиться как ПОДСТРОКА пути.

    Реальный случай: -Root 'C:\\jarvis' под шаблоном '*$Root*' ловил
    'C:\\jarvis_worktrees\\...' — прод-гардиан считал своими раннеры из
    worktree-веток и убил бы их. Здесь чужой корень назван так же, плюс
    суффикс: без разделителя в конце шаблона тест красный."""
    other = tmp_path.parent / (tmp_path.name + "_other")
    p = _runner(other, "aaa")
    kill_after.append(p)
    time.sleep(1.5)
    assert _count(tmp_path, "aaa") == "0"
    assert p.poll() is None, "чужой инстанс не должен даже находиться"


# ── 🔴 мина деплоя (спека §9.1) ────────────────────────────────────────────

def test_legacy_runner_is_invisible_to_slug_lookup(tmp_path, kill_after):
    """Причина мины: живой раннер, запущенный СТАРЫМ скриптом, не имеет
    --client. Точечный поиск его не видит → супервизор решит, что клиент упал,
    и поднимет ВТОРОЙ процесс на ту же сессию."""
    legacy = _legacy_runner(tmp_path)
    kill_after.append(legacy)
    time.sleep(1.5)
    assert legacy.poll() is None
    assert _count(tmp_path, "volska") == "0"


def test_legacy_runner_without_client_is_swept(tmp_path, kill_after):
    """Поэтому супервизор обязан зачистить легаси отдельно, ДО конвергенции."""
    legacy = _legacy_runner(tmp_path)
    kill_after.append(legacy)
    time.sleep(1.5)

    res = _run_ps("Stop-LegacyRunners | Out-Null", tmp_path)
    assert res.returncode == 0, res.stderr
    assert _wait_dead(legacy), "легаси-раннер без --client должен быть зачищен"


def test_legacy_sweep_does_not_touch_managed_runners(tmp_path, kill_after):
    """Зачистка бьёт ТОЛЬКО процессы без --client: иначе она убивала бы
    клиентов, которых сам супервизор и поднял, и он зациклился бы на
    рестартах."""
    managed = _runner(tmp_path, "aaa")
    kill_after.append(managed)
    time.sleep(1.5)

    res = _run_ps("Stop-LegacyRunners | Out-Null", tmp_path)
    assert res.returncode == 0, res.stderr
    time.sleep(1.0)
    assert managed.poll() is None, "клиент с --client зачисткой не трогается"


def test_legacy_sweep_ignores_other_roots(tmp_path, kill_after):
    """Зачистка — самая широкая операция супервизора (бьёт по отсутствию
    признака), поэтому её скоуп по корню проверяется отдельно. Чужой корень
    здесь снова префикс-сосед: зачистка не имеет права выйти за свой инстанс."""
    other = tmp_path.parent / (tmp_path.name + "_otherlegacy")
    p = _legacy_runner(other)
    kill_after.append(p)
    time.sleep(1.5)

    res = _run_ps("Stop-LegacyRunners | Out-Null", tmp_path)
    assert res.returncode == 0, res.stderr
    time.sleep(1.0)
    assert p.poll() is None, "чужой инстанс зачисткой не трогается"
