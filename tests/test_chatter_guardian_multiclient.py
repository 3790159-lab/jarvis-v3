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


# ── Task 6: конвергенция и наблюдаемое состояние ───────────────────────────
#
# План передаём в функции ЯВНО (-Plan), а не подсовываем стаб: так проверяется
# поведение супервизора, а не наша способность подделать питон в tmp-корне.
# Сам контракт «Python отдал -> PowerShell разобрал» покрыт отдельным тестом
# на РЕАЛЬНОМ CLI.

def _plan_ps(json_text: str) -> str:
    """PowerShell-выражение, дающее объект плана из JSON-литерала."""
    escaped = json_text.replace("'", "''")
    return f"$plan = '{escaped}' | ConvertFrom-Json"


def _state(root: Path) -> dict:
    import json
    return json.loads((root / "state" / "chatter_clients.json").read_text("utf-8"))


PLAN_DISABLED = """
{"fatal": null, "clients": [
  {"slug":"aaa","desired":"disabled","runnable":false,"error":null,
   "personas":["aaa"],"session":".secrets/aaa.session","db":".secrets/aaa.db"}]}
"""

PLAN_CONFLICT = """
{"fatal": null, "clients": [
  {"slug":"aaa","desired":"enabled","runnable":false,
   "error":"registry conflict: session '.secrets/shared.session' shared with enabled client(s) bbb",
   "personas":["aaa"],"session":".secrets/shared.session","db":".secrets/aaa.db"},
  {"slug":"bbb","desired":"enabled","runnable":false,
   "error":"registry conflict: session '.secrets/shared.session' shared with enabled client(s) aaa",
   "personas":["bbb"],"session":".secrets/shared.session","db":".secrets/bbb.db"}]}
"""

PLAN_MIXED = """
{"fatal": null, "clients": [
  {"slug":"off_one","desired":"disabled","runnable":false,"error":null,
   "personas":["off_one"],"session":".secrets/off.session","db":".secrets/off.db"},
  {"slug":"down_one","desired":"enabled","runnable":true,"error":null,
   "personas":["down_one"],"session":".secrets/down.session","db":".secrets/down.db"}]}
"""


def test_disabled_client_is_stopped_and_marked_stopped(tmp_path, kill_after):
    p = _runner(tmp_path, "aaa")
    kill_after.append(p)
    time.sleep(1.5)

    res = _run_ps(_plan_ps(PLAN_DISABLED) +
                  "\nInvoke-Converge -Plan $plan | Out-Null\nWrite-ClientState -Plan $plan", tmp_path)
    assert res.returncode == 0, res.stderr
    assert _wait_dead(p), "выключенный клиент должен быть остановлен"
    assert _state(tmp_path)["clients"]["aaa"]["state"] == "stopped"


def test_running_client_is_not_killed_by_a_validation_error(tmp_path, kill_after):
    """Спека §4 правило 7. Опечатка в реестре не имеет права ронять ЖИВОГО
    клиента: валидация, написанная ради защиты прода, не должна становиться
    способом его уронить."""
    a = _runner(tmp_path, "aaa")
    kill_after.append(a)
    time.sleep(1.5)

    res = _run_ps(_plan_ps(PLAN_CONFLICT) +
                  "\nInvoke-Converge -Plan $plan | Out-Null\nWrite-ClientState -Plan $plan", tmp_path)
    assert res.returncode == 0, res.stderr
    time.sleep(1.0)
    assert a.poll() is None, "живой клиент убит из-за конфликта в реестре"

    st = _state(tmp_path)["clients"]["aaa"]
    assert st["state"] == "invalid"
    assert "bbb" in st["last_error"], "ошибка обязана называть второго участника"


def test_observed_state_distinguishes_stopped_from_down(tmp_path):
    """«Выключен» и «упал» требуют противоположной реакции; слипшись в одно
    состояние, они дают либо ложные алерты, либо пропущенные аварии."""
    res = _run_ps(_plan_ps(PLAN_MIXED) + "\nWrite-ClientState -Plan $plan", tmp_path)
    assert res.returncode == 0, res.stderr
    clients = _state(tmp_path)["clients"]
    assert clients["off_one"]["state"] == "stopped"
    assert clients["down_one"]["state"] == "down"


def test_client_state_json_has_full_shape(tmp_path):
    """Поля фиксированы тестом: их читает дашборд, который ляжет сверху."""
    res = _run_ps(_plan_ps(PLAN_MIXED) + "\nWrite-ClientState -Plan $plan", tmp_path)
    assert res.returncode == 0, res.stderr
    st = _state(tmp_path)
    assert isinstance(st["updated_ts"], int)
    entry = st["clients"]["down_one"]
    for key in ("desired", "state", "pid", "heartbeat_ts",
                "last_transition_ts", "consecutive_fail", "last_error"):
        assert key in entry, f"нет поля {key} — его читает дашборд"


def test_broken_registry_does_not_crash_the_supervisor(tmp_path):
    """DEV-18: сломанный реестр обязан быть ВИДИМЫМ, а не уронить гардиан."""
    plan = '{"fatal": "registry.yaml: нет ключа clients", "clients": []}'
    res = _run_ps(_plan_ps(plan) +
                  "\nInvoke-Converge -Plan $plan | Out-Null\nWrite-ClientState -Plan $plan", tmp_path)
    assert res.returncode == 0, res.stderr
    assert _state(tmp_path)["fatal"]


def test_real_cli_output_parses_in_powershell(tmp_path):
    """Сквозной контракт Python -> PowerShell на РЕАЛЬНОМ CLI: стабы выше
    доказывают поведение, но не то, что стороны понимают друг друга."""
    body = (
        f"$raw = & '{sys.executable}' -m chatter.registry_cli --root '{REPO_ROOT}' ;"
        "$p = $raw | ConvertFrom-Json ;"
        "Write-Output ($p.clients.Count) ;"
        "Write-Output ($p.clients[0].slug) ;"
        "Write-Output ($p.clients[0].runnable.GetType().Name)"
    )
    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", f"Set-Location '{REPO_ROOT}'; {body}"],
        capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert res.returncode == 0, res.stderr
    out = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
    assert out[0] == "2", f"ожидали 2 клиента в реестре, получили {out}"
    assert out[1] == "volska"
    assert out[2] == "Boolean", "runnable должен разбираться как bool, а не строка"


# ── Task 7: chatter_client.ps1 — старт/стоп через реестр ───────────────────

CLIENT_PS = REPO_ROOT / "scripts" / "chatter_client.ps1"

REG_TEXT = """# комментарий-инструкция вверху файла
clients:
  aaa:
    enabled: true
    personas: [aaa]
  bbb:
    enabled: false                   # взаимоисключим с aaa: тот же аккаунт
    personas: [bbb]
"""


def _write_registry(root: Path, text: str = REG_TEXT) -> Path:
    d = root / "chatter" / "clients"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "registry.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def _client_ps(root: Path, slug: str, action: str) -> subprocess.CompletedProcess:
    """Прогон пульта клиента на временном -Root.

    С 17.08 у `-Action start` появился обязательный шаг ДО правки реестра:
    замер, кого переответит catch-up (решение владельца после случая, когда
    рестарт ответил клиенту через 13 ч 49 мин поверх человека). Замер — это
    python-скрипт, а у временного `-Root` своего `.venv` нет, поэтому
    интерпретатор передаётся явно. Утверждения тестов ниже не менялись: они
    по-прежнему про правку реестра, а не про замер — его сторожа стоят в
    `test_chatter_client_start_measures_radius.py`.
    """
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(CLIENT_PS), "-Root", str(root), "-Slug", slug, "-Action", action,
         "-PythonExe", sys.executable],
        capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")


def test_stop_action_flips_registry_and_does_not_kill_directly(tmp_path, kill_after):
    """Скрипт НЕ убивает процессы сам: иначе появилась бы вторая ручка
    управления, конкурирующая с супервизором, и наблюдаемое состояние
    разошлось бы с желаемым. Останавливает — супервизор, по реестру."""
    reg = _write_registry(tmp_path)
    p = _runner(tmp_path, "aaa")
    kill_after.append(p)
    time.sleep(1.0)

    res = _client_ps(tmp_path, "aaa", "stop")
    assert res.returncode == 0, res.stdout + res.stderr

    text = reg.read_text(encoding="utf-8")
    aaa_block = text.split("aaa:")[1].split("bbb:")[0]
    assert "enabled: false" in aaa_block
    assert p.poll() is None, "chatter_client.ps1 не должен убивать процесс сам"


def test_start_action_flips_enabled_true(tmp_path):
    reg = _write_registry(tmp_path)
    res = _client_ps(tmp_path, "bbb", "start")
    assert res.returncode == 0, res.stdout + res.stderr
    bbb_block = reg.read_text(encoding="utf-8").split("bbb:")[1]
    assert "enabled: true" in bbb_block


def test_flip_preserves_comments_and_other_clients(tmp_path):
    """Реестр — рабочий документ с инструкциями; правка одного поля не имеет
    права снести комментарии (тот же принцип, что у /funnel_gate)."""
    reg = _write_registry(tmp_path)
    res = _client_ps(tmp_path, "bbb", "start")
    assert res.returncode == 0, res.stdout + res.stderr
    text = reg.read_text(encoding="utf-8")
    # правка ДЕЙСТВИТЕЛЬНО произошла (иначе тест зелёный вакуумно)
    assert "enabled: true" in text.split("bbb:")[1]
    assert "# комментарий-инструкция вверху файла" in text
    assert "# взаимоисключим с aaa: тот же аккаунт" in text
    assert "enabled: true" in text.split("aaa:")[1].split("bbb:")[0], "клиент aaa не тронут"


def test_unknown_slug_fails_loudly(tmp_path):
    """DEV-18: молчаливый успех на опечатке в slug'е = владелец уверен, что
    остановил клиента, а тот работает."""
    _write_registry(tmp_path)
    res = _client_ps(tmp_path, "ghost", "stop")
    assert res.returncode != 0
    assert "ghost" in (res.stdout + res.stderr)


def test_status_reads_observed_state(tmp_path):
    _write_registry(tmp_path)
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "chatter_clients.json").write_text(
        '{"updated_ts":1,"fatal":null,"clients":{"aaa":{"desired":"enabled",'
        '"state":"alive","pid":42,"heartbeat_ts":1,"last_transition_ts":1,'
        '"consecutive_fail":0,"last_error":null}}}', encoding="utf-8")
    res = _client_ps(tmp_path, "aaa", "status")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "alive" in res.stdout
