# -*- coding: utf-8 -*-
r"""ЖИВОЙ проход гардиана: настоящий процесс, настоящая проводка, настоящая
панель. Ничего не подменяется дот-сорсом.

Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md.

ПОЧЕМУ ЭТОТ ФАЙЛ ВООБЩЕ ПОЯВИЛСЯ. Соседний `test_panel_client_guardian.py`
дот-сорсит скрипт с `-NoLoop` и зовёт функции по одной. Так проверяется, что
ФУНКЦИЯ правильная. Но зовёт ли её цикл — и что он делает с тем, что она
вернула, — так не проверяется НИКОГДА. Между «функция правильная» и «её
правильно зовут» лежит зазор, и в нём живут все четыре дефекта проводки,
которые нашёл мутационный гейт.

Вынести тело цикла в `Invoke-PanelGuardianCycle` этот зазор не закрывает — оно
его ПЕРЕДВИГАЕТ на этаж выше: мутация «`while` зовёт шаг, но игнорирует
возвращённое состояние» проходит мимо всех пофункциональных сторожей. Поэтому
здесь гардиан запускается ОТДЕЛЬНЫМ ПРОЦЕССОМ:

    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass
                   -File <гардиан> -MaxCycles N -Root <временный корень> ...

и утверждения делаются по СЛЕДАМ В МИРЕ: жив ли http-сервер, изображающий
панель; что легло в журнал гардиана; появился ли лог подъёма. Подмена — не
кода, а ОКРУЖЕНИЯ: во временном корне лежит свой `scripts/run_panel_client.py`
(гардиан спрашивает адрес именно у него) и свой `.venv\Scripts\python.exe`.

ЧТО ТРЕБУЕТСЯ ОТ РЕАЛИЗАЦИИ: параметр `-MaxCycles <int>`, где 0 — бесконечно
(поведение прода не меняется), а N — ровно N полных проходов и выход с rc 0,
без сна после последнего. Без него живой прогон невозможен: цикл вечен.

БЕЗОПАСНОСТЬ СТЕНДА. Порты только эфемерные (ядро выдаёт свободный), корень
только временный, задачи планировщика не трогаются, боевой :8011 не
упоминается. Убить в этих тестах можно ровно один процесс — тот http-сервер,
который тест сам и поднял.
"""
from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="гардиан Windows-only (PowerShell + Get-NetTCPConnection)")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "panel_client_guardian_detached.ps1"
SLUG = "yarina"

# Уникальный текст, который печатает подставная панель перед rc 1. Он обязан
# доехать до журнала гардиана дословно — это и есть проверка §2.2 «строка на
# каждой попытке с причиной». Константа в коде гардиана его не содержит.
REFUSAL_MARK = "TAMAPI_DB-nezadan-unikalnaya-prichina-etogo-progona"

# ВТОРАЯ причина, и она КИРИЛЛИЧЕСКАЯ — дословно та, что приехала абракадаброй
# на живой приёмке 20.08 16:33. ASCII-сентинел выше проходит через любую
# кодировку невредимым, поэтому на нём сторож зелен при ЛЮБОЙ поломке
# перекодировки. Читаемость проверяется только текстом, который перекодировку
# переживает по-разному.
CYRILLIC_MARK = ("JARVIS_PANELS_KEY не задан: панель закрыта по умолчанию "
                 "и без ключа не поднимается")


# ──────────────────────── стенд ────────────────────────

def _free_port() -> int:
    """Порт, который ядро только что признало свободным.

    Эфемерный, а не 8011: боевой порт в тесте — это способ однажды снести
    настоящую панель клиента."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _fake_python(root: Path) -> None:
    """`<root>\\.venv\\Scripts\\python.exe` — настоящий интерпретатор.

    Гардиан берёт python ИЗ КОРНЯ. Полагаться на `python` из PATH нельзя:
    там чужой venv, и состав его пакетов к делу не относится (см. память
    «python в PATH — чужой venv»). Собираем venv-образный каталог: копия
    базового интерпретатора плюс `pyvenv.cfg`, по которому он находит дом."""
    base = Path(sys.base_prefix)
    exe = base / "python.exe"
    assert exe.exists(), "нет базового интерпретатора %s" % exe
    scripts = root / ".venv" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, scripts / "python.exe")
    (root / ".venv" / "pyvenv.cfg").write_text(
        "home = %s\ninclude-system-site-packages = false\n" % base,
        encoding="utf-8")


FAKE_RUNNER = '''# -*- coding: utf-8 -*-
"""Подставной run_panel_client: и резолвер адреса, и сама панель.

Гардиан обращается к этому файлу ДВАЖДЫ и по-разному — так же, как к
настоящему: импортирует, чтобы спросить адрес бинда, и запускает процессом,
чтобы поднять панель. Обе роли здесь настоящие, подменено только поведение.

FLIP — сколько ПЕРВЫХ обращений за адресом изобразить пропавшим тайнетом.
Нужен, чтобы прогнать ПЕРЕХОД «не могу измерить» -> «измеряю»: часть дефектов
проводки внутри самого состояния молчит и видна только после перехода.
"""
import os
import sys

HOST_VAR = "PANEL_CLIENT_HOST"
DEFAULT_PORT = %(port)d
TAILNET = %(tailnet)r
FLIP = %(flip)d


def _seen():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "bindcalls.txt")
    n = 0
    try:
        with open(path) as fh:
            n = int((fh.read() or "0").strip() or 0)
    except Exception:
        n = 0
    n += 1
    with open(path, "w") as fh:
        fh.write(str(n))
    return n


def tailnet_ip(exe=None):
    if FLIP and _seen() <= FLIP:
        return ""
    return TAILNET


def resolve_client_host(explicit=None, environ=None, ip="", allow_any=False):
    return %(bind)r, None


if __name__ == "__main__":
    print("[panel_client] ОТКАЗ, инстанс не поднят:")
    print("  * %(mark)s")
    print("  * %(cyr)s")
    sys.exit(1)
'''


def _stand(root: Path, *, tailnet: str, bind: str, port: int,
           flip: int = 0) -> None:
    """Временный корень, из которого гардиан живёт целиком."""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "run_panel_client.py").write_text(
        FAKE_RUNNER % {"tailnet": tailnet, "bind": bind, "port": port,
                       "mark": REFUSAL_MARK, "cyr": CYRILLIC_MARK,
                       "flip": flip},
        encoding="utf-8")
    _fake_python(root)


def _run_guardian(root: Path, port: int, *, cycles: int = 1,
                  backoff: int = 3600, long_retry: int = 3600,
                  max_refusals: int = 3, interval: int = 0,
                  timeout: int = 180) -> subprocess.CompletedProcess:
    """Гардиан НАСТОЯЩИМ ПРОЦЕССОМ.

    `-File`, а не `-Command ". script"`: дот-сорс исполняет тело в чужой
    области и не проходит через `param()` так, как это делает планировщик.
    Здесь запуск ровно тот же, каким его делает задача."""
    assert SCRIPT.exists(), (
        "нет скрипта %s — присмотра за клиентской панелью не существует"
        % SCRIPT)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         "-Root", str(root), "-Slug", SLUG, "-Port", str(port),
         "-MaxCycles", str(cycles), "-IntervalSeconds", str(interval),
         "-BackoffSeconds", str(backoff), "-LongRetrySeconds", str(long_retry),
         "-MaxRefusals", str(max_refusals)],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")


def _journal_path(root: Path) -> Path:
    return root / "state" / "logs" / "panel_client_guardian.stdout.log"


def _journal(root: Path) -> list[str]:
    """Журнал ГАРДИАНА (§5 п.5) — отдельный от журнала панели."""
    p = root / "state" / "logs" / "panel_client_guardian.stdout.log"
    if not p.exists():
        return []
    return [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()]


def _panel_log(root: Path) -> str:
    p = root / "logs" / ("panel_%s.stdout.log" % SLUG)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


class _LivePanel:
    """Настоящий http-сервер на эфемерном порту — «живая панель».

    Настоящий, а не мок, ровно по той же причине, по которой настоящий сервер
    понадобился для разбора 500: предмет проверки в том, что гардиан НЕ
    СНЕСЁТ владельца порта, а снести можно только настоящий процесс."""

    def __init__(self, tmp_path: Path):
        script = tmp_path / "live_panel.py"
        script.write_text(
            "import sys\n"
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        body = b'{\"ok\": true}'\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Length', str(len(body)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(body)\n"
            "    def log_message(self, *a):\n"
            "        pass\n"
            "srv = HTTPServer(('127.0.0.1', 0), H)\n"
            "print(srv.server_address[1], flush=True)\n"
            "srv.serve_forever()\n", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(script)], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True)
        self.port = int(self.proc.stdout.readline().strip())

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)


@pytest.fixture()
def live_panel(tmp_path):
    panel = _LivePanel(tmp_path)
    try:
        yield panel
    finally:
        panel.kill()


# ═════════════ ДЫРА A ЖИВЬЁМ: пропал тайнет, а панель ЖИВА ═════════════

def test_a_live_panel_survives_a_vanished_tailnet(tmp_path, live_panel):
    """🔴 САМЫЙ ДОРОГОЙ ДЕФЕКТ АРКИ, проверенный поведением, а не текстом.

    Стенд воспроизводит §2.3 целиком: `tailnet_ip()` пуст, `PANEL_CLIENT_HOST`
    не задан, резолвер МОЛЧА отдаёт петлю. На этой петле сейчас слушает живой
    процесс — он и есть панель.

    Правильный гардиан говорит `no_bind_address` («не могу измерить») и не
    трогает НИЧЕГО. Гардиан с мутацией `no_bind_address` -> `no_response`
    считает панель мёртвой, идёт освобождать порт по его владельцу — и
    убивает ЖИВУЮ панель.

    Поэтому главное утверждение здесь — не строка в журнале, а то, что
    процесс ПЕРЕЖИЛ проход. Сторож на текст файла этого не различал вовсе:
    слово `no_bind_address` остаётся в скрипте и после мутации."""
    root = tmp_path / "root"
    _stand(root, tailnet="", bind="127.0.0.1", port=live_panel.port)

    run = _run_guardian(root, live_panel.port, cycles=1)

    assert run.returncode == 0, (run.stdout, run.stderr)
    assert live_panel.alive(), (
        "гардиан СНЁС ЖИВУЮ ПАНЕЛЬ, потеряв тайнет: «не могу измерить» "
        "прочитано как «мертва» (§2.3).\nЖурнал:\n%s"
        % "\n".join(_journal(root)))
    journal = _journal(root)
    assert any("no_bind_address" in ln for ln in journal), (
        "молчаливое зелёное: гардиан не сказал, что не может измерить.\n%s"
        % "\n".join(journal))
    assert not _panel_log(root), (
        "гардиан пытался поднять панель при неизвестном адресе бинда")


def test_a_live_panel_is_left_alone_when_it_answers(tmp_path, live_panel):
    """ГРАНИЦА к предыдущему: здоровую панель на ИЗВЕСТНОМ адресе тоже никто
    не трогает. Иначе §2.3 можно «выполнить», перестав поднимать что-либо
    вообще, и оба сторожа выше позеленеют."""
    root = tmp_path / "root"
    _stand(root, tailnet="127.0.0.1", bind="127.0.0.1", port=live_panel.port)

    run = _run_guardian(root, live_panel.port, cycles=1)

    assert run.returncode == 0, (run.stdout, run.stderr)
    assert live_panel.alive(), "гардиан снёс ОТВЕЧАЮЩУЮ панель"
    assert not _panel_log(root), "гардиан поднимал панель поверх живой"


# ═══════════ ДЫРА C ЖИВЬЁМ: проводку проверяет ТОЛЬКО проход ═══════════

def _refusal_lines(journal: list[str]) -> list[str]:
    return [ln for ln in journal if REFUSAL_MARK in ln]


def _other_lines(journal: list[str]) -> list[str]:
    return [ln for ln in journal if REFUSAL_MARK not in ln]


def test_the_reason_from_the_panel_reaches_the_journal_on_a_real_pass(tmp_path):
    """🔴 Мутация «убрать `Write-G (Format-RefusalLine ...)`» из цикла.

    Путь проверяется ЦЕЛИКОМ и живьём: подставная панель печатает уникальный
    текст и выходит с rc 1 -> Start-Process перенаправляет его в
    `logs/panel_<slug>.stdout.log` -> Get-RefusalReason читает файл ->
    Format-RefusalLine собирает строку -> цикл пишет её в журнал гардиана.

    Ни одно звено не подменено. Функция форматирования может быть идеально
    правильной и при этом никем не вызванной — здесь это видно."""
    root = tmp_path / "root"
    port = _free_port()
    _stand(root, tailnet="127.0.0.1", bind="127.0.0.1", port=port)

    run = _run_guardian(root, port, cycles=1)

    assert run.returncode == 0, (run.stdout, run.stderr)
    assert REFUSAL_MARK in _panel_log(root), (
        "предпосылка сломана: подставная панель не напечатала свою причину")
    assert _refusal_lines(_journal(root)), (
        "причина отказа не доехала до журнала гардиана — через час по нему "
        "не отличить «всё та же ошибка» от «уже другая» (§2.2).\n%s"
        % "\n".join(_journal(root)))


def test_the_refusal_clock_starts_on_a_real_pass(tmp_path):
    """🔴 Мутация «не запоминать момент отказа».

    Пауза после отказа задана огромной (3600 с), проходов два. Правильный
    гардиан делает РОВНО ОДНУ попытку: вторая упирается в паузу. Гардиан,
    забывший отметить время, видит «отказа не было никогда» и ломится
    заново — то есть возвращается к перезапуску раз в интервал, засыпая лог
    и пряча настоящую причину.

    Считаем ПОПЫТКИ, а не читаем текст: счёт строк с причиной — это и есть
    число реальных запусков подставной панели."""
    root = tmp_path / "root"
    port = _free_port()
    _stand(root, tailnet="127.0.0.1", bind="127.0.0.1", port=port)

    run = _run_guardian(root, port, cycles=2, backoff=3600)

    assert run.returncode == 0, (run.stdout, run.stderr)
    tries = _refusal_lines(_journal(root))
    assert len(tries) == 1, (
        "за два прохода при паузе 3600 с сделано попыток: %d. Часы отказа не "
        "пошли — паузы не будет никогда.\n%s"
        % (len(tries), "\n".join(_journal(root))))


def test_the_exhausted_state_is_announced_on_a_real_pass(tmp_path):
    """🔴 Мутация «не читать `$st.Alert`» — та самая, которую до сегодня не
    краснило НИЧТО.

    `Step-RefusalState` честно возвращает `Alert`, все сторожа С8 на ней
    зелёные, а строка о входе в состояние не появляется никогда.

    Сравнение ДИФФЕРЕНЦИАЛЬНОЕ и без единого слова из текста реализации: два
    прогона отличаются ровно порогом. При `MaxRefusals=3` третий отказ вводит
    в состояние; при `MaxRefusals=99` те же три отказа не вводят никуда.
    Разница обязана быть — ровно одна строка сверх обычных."""
    port_a, port_b = _free_port(), _free_port()
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    _stand(root_a, tailnet="127.0.0.1", bind="127.0.0.1", port=port_a)
    _stand(root_b, tailnet="127.0.0.1", bind="127.0.0.1", port=port_b)

    entered = _run_guardian(root_a, port_a, cycles=3, backoff=0,
                            long_retry=0, max_refusals=3)
    never = _run_guardian(root_b, port_b, cycles=3, backoff=0,
                          long_retry=0, max_refusals=99)

    assert entered.returncode == 0, (entered.stdout, entered.stderr)
    assert never.returncode == 0, (never.stdout, never.stderr)

    j_entered, j_never = _journal(root_a), _journal(root_b)
    assert len(_refusal_lines(j_entered)) == 3, (
        "предпосылка сломана: отказов не три, а %d\n%s"
        % (len(_refusal_lines(j_entered)), "\n".join(j_entered)))
    assert len(_refusal_lines(j_never)) == 3, (
        "предпосылка сломана: во втором прогоне отказов %d\n%s"
        % (len(_refusal_lines(j_never)), "\n".join(j_never)))

    extra = len(_other_lines(j_entered)) - len(_other_lines(j_never))
    assert extra == 1, (
        "вход в состояние исчерпанных отказов не отмечен ровно одной строкой "
        "(разница %d).\nС порогом 3:\n%s\nС порогом 99:\n%s"
        % (extra, "\n".join(_other_lines(j_entered)),
           "\n".join(_other_lines(j_never))))


def test_the_exhausted_state_is_announced_only_once_on_a_real_pass(tmp_path):
    """Парный к предыдущему: четвёртая, пятая и шестая попытка молчат.

    Довод владельца дословно: «повторяющееся сообщение перестают читать, мы
    это видели на 25 тестовых вопросах». Здесь это проверено ПРОХОДОМ, а не
    чистой функцией: `Alert` цикл может читать и на каждом обороте.

    Сравнение снова дифференциальное и снова без единого слова из текста
    реализации. Оба прогона делают РОВНО ПО ШЕСТЬ попыток, поэтому всё, что
    растёт вместе с попытками (строка о запуске, строка о причине), в разнице
    сокращается. Отличается только порог: с ним в состояние входят, без него
    — нет. Разница обязана остаться ОДНОЙ строкой, а не четырьмя.

    🔴 Первая редакция этого сторожа сравнивала три прохода с шестью и
    покраснела на здоровом коде: строка о запуске панели растёт вместе с
    попытками и изображала собой повторный алерт. Сравнивать надо равные
    прогоны, отличающиеся ОДНИМ."""
    port_a, port_b = _free_port(), _free_port()
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    _stand(root_a, tailnet="127.0.0.1", bind="127.0.0.1", port=port_a)
    _stand(root_b, tailnet="127.0.0.1", bind="127.0.0.1", port=port_b)

    entered = _run_guardian(root_a, port_a, cycles=6, backoff=0,
                            long_retry=0, max_refusals=3, timeout=300)
    never = _run_guardian(root_b, port_b, cycles=6, backoff=0,
                          long_retry=0, max_refusals=99, timeout=300)

    assert entered.returncode == 0, (entered.stdout, entered.stderr)
    assert never.returncode == 0, (never.stdout, never.stderr)

    j_entered, j_never = _journal(root_a), _journal(root_b)
    assert len(_refusal_lines(j_entered)) == 6, (
        "предпосылка сломана: попыток %d, а не шесть"
        % len(_refusal_lines(j_entered)))
    assert len(_refusal_lines(j_never)) == 6, (
        "предпосылка сломана: во втором прогоне попыток %d"
        % len(_refusal_lines(j_never)))

    extra = len(_other_lines(j_entered)) - len(_other_lines(j_never))
    assert extra == 1, (
        "за шесть попыток состояние объявлено %d раз(а) вместо одного — "
        "через сутки таких строк было бы 48.\nС порогом 3:\n%s\nС порогом 99:\n%s"
        % (extra, "\n".join(_other_lines(j_entered)),
           "\n".join(_other_lines(j_never))))


def test_an_unmeasurable_state_never_grows_anything_on_real_passes(tmp_path,
                                                                   live_panel):
    """🔴 Мутация «в ветке §2.3 позвать Step-RefusalState с 'refused'».

    Пропавший тайнет копил бы отказы, и через три оборота гардиан объявил бы
    исчерпанные отказы — про попытки, которых НЕ БЫЛО. Заодно это ежецикловая
    запись, которую §2.3 запрещает прямым текстом (за ночь 2880 строк).

    Утверждение без единого слова из реализации: журнал за один проход и за
    пять проходов обязан быть ОДИНАКОВОЙ длины. Растёт — значит состояние
    «не могу измерить» что-то накапливает."""
    root_one, root_five = tmp_path / "one", tmp_path / "five"
    for root in (root_one, root_five):
        _stand(root, tailnet="", bind="127.0.0.1", port=live_panel.port)

    one = _run_guardian(root_one, live_panel.port, cycles=1)
    five = _run_guardian(root_five, live_panel.port, cycles=5)

    assert one.returncode == 0 and five.returncode == 0, (one.stderr, five.stderr)
    assert live_panel.alive(), "живую панель снесли за пять оборотов"
    j1, j5 = _journal(root_one), _journal(root_five)
    assert j1, "за проход не записано ничего — вход в состояние прошёл молча"
    assert len(j5) == len(j1), (
        "журнал растёт с числом оборотов в состоянии «не могу измерить»: "
        "%d строк за 5 проходов против %d за 1.\n%s"
        % (len(j5), len(j1), "\n".join(j5)))
    assert not _refusal_lines(j5), (
        "«не могу измерить» превратилось в попытки старта: %s"
        % (_refusal_lines(j5),))


def test_the_pass_is_a_real_process_and_not_a_dot_source(tmp_path):
    """Предпосылка всего файла, названная вслух.

    Живой проход обязан оставлять следы, которых дот-сорс с `-NoLoop` не
    оставляет НИКОГДА: PID-лок и запись в журнале. Если их нет, значит
    `-MaxCycles` отработал мимо настоящей проводки, и все сторожа выше
    проверяют не то, что написано в их именах."""
    root = tmp_path / "root"
    port = _free_port()
    _stand(root, tailnet="127.0.0.1", bind="127.0.0.1", port=port)

    run = _run_guardian(root, port, cycles=1)

    assert run.returncode == 0, (run.stdout, run.stderr)
    lock = root / "state" / "locks" / ("panel_client_guardian_%s.pid" % SLUG)
    assert lock.exists(), (
        "PID-лок не взят — проход шёл не через настоящий старт гардиана")
    assert lock.read_text(encoding="utf-8", errors="replace").strip().isdigit()
    assert _journal(root), "журнал гардиана пуст после настоящего прохода"


def test_endless_stays_the_default_so_production_is_unchanged():
    """ГРАНИЦА: `-MaxCycles` заведён РАДИ ТЕСТОВ, и прод обязан остаться
    бесконечным. Умолчание, случайно ставшее единицей, превратило бы гардиана
    в одноразовый скрипт — присмотра не стало бы вовсе, и заметили бы это по
    мёртвой панели.

    Проверяется по объявлению параметра, а не по прогону: убедиться в
    бесконечности прогоном нельзя по определению."""
    assert SCRIPT.exists(), "нет скрипта %s" % SCRIPT
    text = SCRIPT.read_text(encoding="utf-8-sig")
    m = re.search(r"\$MaxCycles\s*=\s*(\d+)", text)
    assert m, (
        "параметра -MaxCycles нет: живой проход цикла невозможен, и вся "
        "проводка остаётся слепой зоной")
    assert m.group(1) == "0", (
        "умолчание -MaxCycles = %s: прод перестал быть бесконечным"
        % m.group(1))


def test_a_bounded_run_does_not_hang_on_the_last_sleep(tmp_path):
    """Мелочь, которая делает весь файл непригодным, если её нет: после
    последнего прохода спать нельзя. Иначе каждый живой сторож платит
    интервал сна, и их перестают гонять."""
    root = tmp_path / "root"
    port = _free_port()
    _stand(root, tailnet="127.0.0.1", bind="127.0.0.1", port=port)

    started = time.time()
    run = _run_guardian(root, port, cycles=1, interval=30, timeout=120)
    took = time.time() - started

    assert run.returncode == 0, (run.stdout, run.stderr)
    assert took < 30, (
        "проход с интервалом 30 с занял %.1f с — гардиан спит ПОСЛЕ "
        "последнего оборота" % took)


def test_an_unmeasurable_stretch_does_not_poison_the_refusal_counter(tmp_path):
    """🔴 Мутация «в ветке §2.3 позвать Step-RefusalState с 'refused'».

    Внутри самого состояния эта подмена МОЛЧИТ: счётчик растёт, но строку о
    входе в состояние ветка §2.3 не пишет — она пишется только там, где
    обрабатывается настоящий отказ. Поэтому увидеть её можно ТОЛЬКО после
    перехода «не могу измерить» -> «измеряю». Соседний сторож на длину
    журнала её пропускает по построению, и это стоит сказать вслух: один
    живой сторож не закрывает состояние целиком.

    Стенд делает переход: три первых обращения за адресом изображают
    пропавший тайнет, дальше адрес возвращается, а подставная панель
    отказывает по-настоящему. Правильный гардиан входит в состояние
    исчерпанных отказов на ТРЕТЬЕМ настоящем отказе — одна строка. Гардиан
    с подменой потратил счётчик на циклы, в которых не было ни одной
    попытки: к первому настоящему отказу он уже «объявлял», и строки не
    будет НИКОГДА.

    Сравнение дифференциальное: два одинаковых прогона, отличающиеся ТОЛЬКО
    порогом. Всё, что дал переход по адресу, в разнице сокращается."""
    port_a, port_b = _free_port(), _free_port()
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    _stand(root_a, tailnet="127.0.0.1", bind="127.0.0.1", port=port_a, flip=3)
    _stand(root_b, tailnet="127.0.0.1", bind="127.0.0.1", port=port_b, flip=3)

    entered = _run_guardian(root_a, port_a, cycles=6, backoff=0,
                            long_retry=0, max_refusals=3, timeout=300)
    never = _run_guardian(root_b, port_b, cycles=6, backoff=0,
                          long_retry=0, max_refusals=99, timeout=300)

    assert entered.returncode == 0, (entered.stdout, entered.stderr)
    assert never.returncode == 0, (never.stdout, never.stderr)

    j_entered, j_never = _journal(root_a), _journal(root_b)
    assert len(_refusal_lines(j_entered)) == 3, (
        "предпосылка сломана: настоящих отказов %d, а не три — переход по "
        "адресу не отработал" % len(_refusal_lines(j_entered)))
    assert len(_refusal_lines(j_never)) == 3, (
        "предпосылка сломана: во втором прогоне отказов %d"
        % len(_refusal_lines(j_never)))

    extra = len(_other_lines(j_entered)) - len(_other_lines(j_never))
    assert extra == 1, (
        "счётчик отказов потрачен циклами, в которых не было ни одной "
        "попытки старта: вход в состояние объявлен %d раз вместо одного"
        % extra)


# ══════ ЧИТАЕМОСТЬ ПРИЧИНЫ: найдено ЖИВОЙ ПРИЁМКОЙ, не сторожами ══════
#
# 🔴 ЧЕТВЁРТЫЙ РАЗ ЗА ДЕНЬ ОДИН И ТОТ ЖЕ КЛАСС. Транк 3c94ea93 задеплоен,
# гардиан живёт, и в его журнале:
#
#   16:33:29 | accept | ОТКАЗ СТАРТА (rc 1), попытка 1: JARVIS_PANELS_KEY
#   РЅРµ Р·Р°РґР°РЅ: РїР°РЅРµР»СЊ Р·Р°РєСЂС‹С‚Р° РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ...
#
# Панель напечатала «JARVIS_PANELS_KEY не задан: панель закрыта по умолчанию
# и без ключа не поднимается». В журнал приехали байты, прочитанные не той
# кодировкой: `Get-Content` без `-Encoding` в PS 5.1 читает СИСТЕМНОЙ кодовой
# страницей (cp1251), а панель пишет UTF-8 (PYTHONUTF8=1).
#
# ПОЧЕМУ ЭТО ПРОПУСТИЛИ 131 СТОРОЖ И 52 МУТАЦИИ. Сторож на журнал проверял,
# что ДВЕ РАЗНЫЕ причины дают ДВЕ РАЗНЫЕ строки. Две разные абракадабры —
# тоже разные. Проверялось РАЗЛИЧИЕ, а не ЧИТАЕМОСТЬ, и на этом дефекте
# сторож зелен ПО ПОСТРОЕНИЮ. Требование владельца («чтобы через час было
# видно, одна и та же это ошибка или разные») по букве выполнено, по смыслу
# нет: в три ночи по такой строке не понять, ЧТО сломалось, а строка заведена
# ровно за этим.
#
# Тот же класс, что S4U в комментарии, путь к базе в несуществующей форме и
# mtime в долях секунды: сторож охранял ФОРМУ признака, а не то, ради чего
# признак заведён.
#
# Утверждаем РАВЕНСТВО с тем, что панель напечатала, а не отсутствие
# вопросительных знаков: перечислять плохие символы значит засторожить один
# симптом из многих, а перекодировок много.

def test_the_reason_reaches_the_journal_readable(tmp_path):
    """Причина обязана доехать в журнал ТЕМИ ЖЕ СИМВОЛАМИ, что напечатала
    панель. Не «похожими», не «различимыми» — теми же."""
    root = tmp_path / "root"
    port = _free_port()
    _stand(root, tailnet="127.0.0.1", bind="127.0.0.1", port=port)

    run = _run_guardian(root, port, cycles=1)
    assert run.returncode == 0, (run.stdout, run.stderr)

    panel_out = (root / "logs" / ("panel_%s.stdout.log" % SLUG)).read_text(
        encoding="utf-8")
    assert CYRILLIC_MARK in panel_out, (
        "предпосылка сломана: подставная панель не напечатала кириллическую "
        "причину, сторожить нечего:\n%s" % panel_out[:400])

    raw = _journal_path(root).read_bytes()
    text = raw.decode("utf-8")

    if CYRILLIC_MARK not in text:
        mojibake = CYRILLIC_MARK.encode("utf-8").decode("cp1251", "replace")
        hint = ("похоже на чтение UTF-8 кодовой страницей cp1251 "
                "(Get-Content без -Encoding)" if mojibake[:20] in text
                else "текст пришёл в неизвестной перекодировке")
        raise AssertionError(
            "причина отказа доехала до журнала НЕЧИТАЕМОЙ — %s.\n"
            "панель напечатала: %r\nв журнале: %r"
            % (hint, CYRILLIC_MARK[:60],
               [ln for ln in text.splitlines() if REFUSAL_MARK in ln][:1]))


def test_the_journal_is_one_consistent_encoding_end_to_end(tmp_path):
    """Пункт 2: сторож не имеет права быть зелёным оттого, что читает файл
    тем же кривым способом, каким его пишут. Две ошибки, гасящие друг друга,
    дают зелёное на сломанном.

    Поэтому три независимых утверждения:
      * ВЕСЬ файл декодируется как UTF-8 СТРОГО, без `errors=`. Собственная
        кириллица гардиана пишется `Add-Content -Encoding utf8`; уберут
        `-Encoding` — байты станут cp1251, и строгий декодер упрётся в них;
      * в UTF-8-прочтении причина ЕСТЬ;
      * в cp1251-прочтении того же файла причины НЕТ. Если бы файл на самом
        деле был cp1251, всё было бы наоборот — и первое утверждение уже
        упало бы. Пара разводит «файл верный» и «читатель повторяет ошибку
        писателя»."""
    root = tmp_path / "root"
    port = _free_port()
    _stand(root, tailnet="127.0.0.1", bind="127.0.0.1", port=port)

    run = _run_guardian(root, port, cycles=1)
    assert run.returncode == 0, (run.stdout, run.stderr)

    raw = _journal_path(root).read_bytes()
    try:
        as_utf8 = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError(
            "журнал гардиана — НЕ UTF-8: строгое чтение упало на байте %d "
            "(%s). Собственные строки пишутся не той кодировкой, и разбирать "
            "аварию придётся по абракадабре" % (exc.start, exc.reason))

    assert CYRILLIC_MARK in as_utf8, (
        "в UTF-8-прочтении журнала причины нет — см. соседний сторож")
    as_cp1251 = raw.decode("cp1251", "replace")
    assert CYRILLIC_MARK not in as_cp1251, (
        "причина читается как cp1251 — значит файл записан не в UTF-8, и "
        "совпадение с ожидаемым текстом выше было бы случайным")
