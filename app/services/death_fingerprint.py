"""Отпечаток смерти процесса: три версии, три разных следа, один файл.

17.08 бот умер 16 раз за сутки, и после каждой смерти вопрос «от чего» не имел
ответа. Замер в гардиане (`DIAGNOSIS`) уже показал, чем это НЕ является:
процесс был МЁРТВ при 12% CPU и 8.7 ГБ свободной памяти, то есть ни голодания,
ни OOM. Трассировки при этом нет ни в одном архиве `bot_boot.stderr.*`.

Осталось три версии, и различить их можно только по следу, который процесс
оставляет (или не оставляет) сам:

| что нашли в файле после смерти | вывод |
|---|---|
| трассировка от faulthandler | жёсткий крах интерпретатора (SIGSEGV, access violation) |
| строка `EXIT-CLEAN`           | процесс вышел сам, по своему коду (`os._exit` сюда НЕ попадёт) |
| только `BOOT` и больше ничего | процесс убили снаружи (`TerminateProcess`, taskkill /F) |

Третий случай — это отсутствие следа, и именно поэтому файл ДОПИСЫВАЕТСЯ, а не
перезаписывается: «ничего нет» можно прочитать только рядом с «BOOT есть».
Перезапись превратила бы внешнее убийство в пустой файл, неотличимый от
«ничего не случилось» — той же ошибкой, что затирание `bot_boot.stderr`.

Что этот модуль НЕ делает:
* не мешает старту — любая ошибка вооружения гасится и печатается;
* не пишет в лог приложения (у того своя ротация и свои уровни);
* не ловит `os._exit()`: он обрывает процесс без atexit ПО ОПРЕДЕЛЕНИЮ, и это
  полезно — оба os._exit-пути бота требуют команды человека, значит их след
  выглядит как внешнее убийство и не спутается с крахом.
"""
from __future__ import annotations

import atexit
import faulthandler
import os
import time
from pathlib import Path

DEFAULT_LOG = Path("state") / "logs" / "bot_death.log"

BOOT = "BOOT"
EXIT_CLEAN = "EXIT-CLEAN"


def _line(kind: str, pid: int, now: float, extra: str = "") -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    tail = f" {extra}" if extra else ""
    return f"{kind} pid={pid} ts={int(now)} {stamp}{tail}\n"


def arm(log_path=DEFAULT_LOG, *, pid: int | None = None, now: float | None = None):
    """Вооружить отпечаток. Возвращает открытый дескриптор (или None).

    Дескриптор обязан жить всё время процесса: `faulthandler` пишет прямо в
    fd, и закрытый файл превратил бы крах в тишину. Поэтому ссылка на него
    возвращается наружу — вызывающий держит её в модульной переменной.
    """
    pid = os.getpid() if pid is None else pid
    now = time.time() if now is None else now
    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
    except Exception as exc:  # noqa: BLE001 — диагностика не имеет права ронять бота
        print(f"[death-fingerprint] не смог открыть {path}: {exc!r}", flush=True)
        return None

    try:
        handle.write(_line(BOOT, pid, now))
        # all_threads: бот живёт в потоках (heartbeat, cowork-watcher, poller), и
        # крах в НЕ главном потоке без этого флага не показал бы виновника.
        faulthandler.enable(file=handle, all_threads=True)
        atexit.register(_say_clean_exit, handle, pid)
    except Exception as exc:  # noqa: BLE001
        print(f"[death-fingerprint] не смог вооружиться: {exc!r}", flush=True)
        return handle
    return handle


def _say_clean_exit(handle, pid: int) -> None:
    """Метка чистого выхода. Пишется ТОЛЬКО при штатном завершении
    интерпретатора — ни `os._exit`, ни `TerminateProcess` сюда не приходят, и
    в этом весь смысл: отсутствие метки — тоже показание."""
    try:
        handle.write(_line(EXIT_CLEAN, pid, time.time()))
        handle.flush()
    except Exception:  # noqa: BLE001 — на выходе жаловаться уже некуда
        pass


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с таким номером ПРЯМО СЕЙЧАС.

    `psutil` уже стоит и используется сторожем `ops_watchdog`, поэтому второй
    правды о живости процесса тут не заводим. Отказ библиотеки трактуется как
    «не знаем» = не жив: вердикт `killed` в этом случае честнее, чем `alive`,
    потому что он не выдаёт догадку за наблюдение.
    """
    try:
        import psutil

        return bool(psutil.pid_exists(int(pid)))
    except Exception:  # noqa: BLE001 — читатель журнала не имеет права падать
        return False


def read_fingerprint(log_path=DEFAULT_LOG, *, pid: int, alive_fn=None) -> str:
    """Чем кончился процесс `pid` по записям файла: 'crash' | 'clean' |
    'killed' | 'alive' | 'unknown'. Разбор ЗДЕСЬ, чтобы разбирающий человек не
    собирал правило заново в голове (и чтобы у правила был сторож).

    🔴 `alive` появился 17.08 по факту: читатель отвечал `killed` про ЖИВОЙ
    процесс — у последнего `BOOT` следующего просто нет, а «нет следа» до этого
    значило «убили». Инструмент, который врёт на живом, обесценит вердикт ровно
    в тот момент, когда он понадобится.

    Живость спрашивается ТОЛЬКО когда окно не закрыто следующим `BOOT`: если
    процесс уже сменился, номер мог достаться кому угодно, и «жив» сказало бы
    о ЧУЖОМ процессе.
    """
    path = Path(log_path)
    if not path.is_file():
        return "unknown"
    text = path.read_text(encoding="utf-8", errors="replace")
    marker = f"pid={pid} "
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith(BOOT) and marker in ln), None)
    if start is None:
        return "unknown"
    tail = lines[start + 1:]
    # Следующий BOOT закрывает окно этого процесса: всё после него — чужое.
    closed = False
    for i, ln in enumerate(tail):
        if ln.startswith(BOOT):
            tail = tail[:i]
            closed = True
            break
    if any(ln.startswith(EXIT_CLEAN) and marker in ln for ln in tail):
        return "clean"
    if any(ln.strip() for ln in tail):
        return "crash"
    if not closed and (alive_fn or _pid_alive)(pid):
        # Следа нет, потому что процесс ещё НЕ УМЕР. Это не улика, а текущее
        # состояние, и путать их нельзя.
        return "alive"
    return "killed"
