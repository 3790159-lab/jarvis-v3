# -*- coding: utf-8 -*-
"""Ремень по памяти для ручного `pytest tests/`.

Спека: docs/superpowers/specs/2026-08-21-suite-ram-belt.md

Июльский `devtask/regress_watch.py::run_guarded` бережёт только батчевый
regress-раннер. Обычный `pytest tests/` — тот, которым снимают базлайн и
который прописан шагом мерж-гейта, — идёт голым; 21.08 это кончилось BSOD.

Здесь живёт РЕШАЮЩАЯ логика и ничего больше: чистые функции от чисел и
времени, без потоков и без psutil. Ремень, который нельзя проверить, не
воспроизводя аварию, — это не сторож, а надежда. Съём величин и поток —
в `install()`, и он специально тонкий.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Tuple

BELT_LOG_PATH = Path("state") / "logs" / "suite_ram_belt.log"

# Пол по свободной памяти — ТОТ ЖЕ, что у июльского батчевого ремня
# (`regress_batches.DEFAULT_BATCH_KILL_FREE_GB`). Два ремня на одну машину
# обязаны иметь один пол; расхождение сторожит отдельный тест.
DEFAULT_KILL_FREE_GB = 2.5

# Потолок по СВОЕМУ RSS. Замер полной суиты 21.08 дал пик 801 МБ, то есть
# здесь 3.7x запаса. Нужен потому, что пол по свободной памяти срабатывает
# поздно: при скорости течи 21.08 (+190 МБ/с) RSS ловит её на ~16-й секунде,
# а свободная память — на ~110-й.
DEFAULT_KILL_RSS_GB = 3.0

DEFAULT_SAMPLE_S = 2.0
DEFAULT_HARD_GRACE_S = 20.0
DEFAULT_EXIT_CODE = 77

# Линии, при пересечении которых ждать отсрочку НЕЛЬЗЯ.
#
# Найдено живым дрилом 21.08: распаковка нулей росла ~2 ГБ/с, и за отсрочку в
# 3 секунды процесс ушёл с 2.1 до 4.7 ГБ. Отсрочка меряет ВРЕМЯ, а опасность
# определяется СКОРОСТЬЮ: на течи 21.08 (+190 МБ/с) двадцать секунд стоят
# +3.8 ГБ и это терпимо, при 2 ГБ/с — +40 ГБ, и машина умрёт раньше backstop'а.
#
# 6.0 ГБ своего RSS: полная суита живёт на 801 МБ, ферма занимает ~6.6 ГБ из
# 16 — дальше начинается территория, с которой 21.08 не вернулись.
DEFAULT_HARD_RSS_GB = 6.0
DEFAULT_HARD_FREE_GB = 1.0

ACTION_OK = "ok"
ACTION_INTERRUPT = "interrupt"
ACTION_WAIT = "wait"
ACTION_EXIT = "exit"

REASON_FREE = "free"
REASON_RSS = "rss"

_NO_TEST = "тест не выполнялся (сбор, фикстура сессии или разматывание)"

# Сколько процессов называем (DEV-52 §5). Три, а не пять и не десять: отчёт
# читают В АВАРИИ, и он обязан помещаться в экран целиком. Больше трёх — это
# уже разбор, а разбор идёт по `tasklist` руками.
TOP_RSS_COUNT = 3

# Потолок времени на весь перебор (DEV-52 §4 Г3). Вчетверо меньше интервала
# сэмплирования (`DEFAULT_SAMPLE_S`), поэтому подсказка физически не может
# сдвинуть следующий замер. На жёсткой линии ждать нечего: живой дрил 21.08
# дал 2 ГБ/с, и лишняя секунда стоит двух гигабайт.
TOP_RSS_BUDGET_S = 0.5


@dataclass(frozen=True)
class ProcRow:
    """Одна строка подсказки «кто съел память» (DEV-52 §3).

    `is_self` — это САМ процесс pytest. Пометка нужна, чтобы читающий не принял
    собственный прогон за пожирателя: в ветке `REASON_RSS` он В СПИСКЕ ОБЯЗАН
    БЫТЬ и это норма, а в `REASON_FREE` его присутствие наверху — сигнал, что
    причина всё-таки своя.
    """
    name: str
    pid: int
    rss_gb: float
    is_self: bool = False


@dataclass(frozen=True)
class Limits:
    free_gb: float = DEFAULT_KILL_FREE_GB
    rss_gb: float = DEFAULT_KILL_RSS_GB
    sample_s: float = DEFAULT_SAMPLE_S
    grace_s: float = DEFAULT_HARD_GRACE_S
    hard_rss_gb: float = DEFAULT_HARD_RSS_GB
    hard_free_gb: float = DEFAULT_HARD_FREE_GB
    enabled: bool = True
    exit_code: int = DEFAULT_EXIT_CODE


def limits_from_env(env: Mapping[str, str]) -> Limits:
    """Собрать пороги из окружения.

    Мусор в переменной — громкий ValueError, а не тихое умолчание: проглоченная
    опечатка даёт ремень с другим порогом, о котором никто не знает (DEV-18).
    """
    def _f(name: str, default: float) -> float:
        raw = env.get(name)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "%s=%r — не число; ремень не запускается с непонятным порогом"
                % (name, raw)) from exc

    def _i(name: str, default: int) -> int:
        raw = env.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("%s=%r — не целое" % (name, raw)) from exc

    return Limits(
        free_gb=_f("SUITE_KILL_FREE_GB", DEFAULT_KILL_FREE_GB),
        rss_gb=_f("SUITE_KILL_RSS_GB", DEFAULT_KILL_RSS_GB),
        sample_s=_f("SUITE_SAMPLE_S", DEFAULT_SAMPLE_S),
        grace_s=_f("SUITE_HARD_GRACE_S", DEFAULT_HARD_GRACE_S),
        hard_rss_gb=_f("SUITE_HARD_RSS_GB", DEFAULT_HARD_RSS_GB),
        hard_free_gb=_f("SUITE_HARD_FREE_GB", DEFAULT_HARD_FREE_GB),
        # Выключается ТОЛЬКО явным нулём: умолчание «выключено» превратило бы
        # ремень в декорацию.
        enabled=env.get("SUITE_RAM_BELT", "1") != "0",
        exit_code=_i("SUITE_RAM_EXIT_CODE", DEFAULT_EXIT_CODE),
    )


class Belt:
    """Два зуба и защита от дребезга.

    Зуб 1 (`ACTION_INTERRUPT`) — прерывание главного потока: pytest
    разматывается штатно, печатает сводку и НАЗЫВАЕТ тест.
    Зуб 2 (`ACTION_EXIT`) — аварийный выход, если через отсрочку память всё
    ещё за линией: значит прерывание не взялось, главный поток внутри C-кода
    и байт-код не исполняет, а там сигналы не проверяются.

    Пересечение, исчезнувшее само, сбрасывает отсчёт: иначе всплеск в начале
    прогона и настоящая течь через двадцать минут сложатся, и зуб 2 сработает
    мгновенно, отняв шанс на штатное разматывание.
    """

    def __init__(self, limits: Limits) -> None:
        self._limits = limits
        self._first_breach_at: Optional[float] = None
        self._reason: Optional[str] = None
        self._hard = False

    @property
    def limits(self) -> Limits:
        return self._limits

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    @property
    def hard(self) -> bool:
        """Выход случился по жёсткой линии, а не по отсрочке.

        Отчёт обязан называть ТОТ порог, который сработал: живой дрил показал
        два отчёта подряд, и оба называли мягкий потолок, хотя второй пришёл
        по жёсткой линии. Читающий крутил бы не ту ручку.
        """
        return self._hard

    def _breach(self, free_gb: float, rss_gb: float) -> Optional[str]:
        # Границы строгие: сторож, загорающийся ровно на пороге, приучает к
        # тому, что он слегка врёт.
        if free_gb < self._limits.free_gb:
            return REASON_FREE
        if rss_gb > self._limits.rss_gb:
            return REASON_RSS
        return None

    def observe(self, *, now: float, free_gb: float, rss_gb: float) -> str:
        if not self._limits.enabled:
            return ACTION_OK

        # Третий зуб. Отсрочка меряет ВРЕМЯ, а опасность определяется
        # СКОРОСТЬЮ: за неё можно уйти на десятки гигабайт. За этими линиями
        # ждать нечего — штатное разматывание уже не успеет.
        if rss_gb > self._limits.hard_rss_gb:
            self._reason = REASON_RSS
            self._hard = True
            return ACTION_EXIT
        if free_gb < self._limits.hard_free_gb:
            self._reason = REASON_FREE
            self._hard = True
            return ACTION_EXIT

        reason = self._breach(free_gb, rss_gb)
        if reason is None:
            self._first_breach_at = None
            self._reason = None
            self._hard = False
            return ACTION_OK

        if self._first_breach_at is None:
            self._first_breach_at = now
            self._reason = reason
            return ACTION_INTERRUPT

        if now - self._first_breach_at >= self._limits.grace_s:
            return ACTION_EXIT

        return ACTION_WAIT


def render_report(*, current_test: Optional[str], free_gb: float, rss_gb: float,
                  elapsed_s: float, limits: Limits, reason: str,
                  hard: bool = False,
                  top_rss: Optional[list] = None,
                  top_rss_error: Optional[str] = None,
                  top_rss_truncated: bool = False) -> str:
    """Отчёт, который обязан пережить аварийный выход.

    `os._exit` не исполняет обработчики и не сбрасывает буферы, поэтому текст
    пишется и в файл, и на stderr, и оба сбрасываются ДО выхода. Отчёт, видный
    только в буфере, — это ровно та тишина, из-за которой течь 21.08 искали
    два часа.
    """
    if hard:
        # Жёсткая линия: ждать отсрочку нельзя, штатное разматывание не успеет.
        if reason == REASON_FREE:
            crossed = ("свободная память ниже ЖЁСТКОЙ линии %.2f ГБ "
                       "— немедленный выход" % limits.hard_free_gb)
        else:
            crossed = ("свой RSS выше ЖЁСТКОЙ линии %.2f ГБ "
                       "— немедленный выход" % limits.hard_rss_gb)
    elif reason == REASON_FREE:
        crossed = "свободная память системы ниже пола %.2f ГБ" % limits.free_gb
    else:
        crossed = "свой RSS выше потолка %.2f ГБ" % limits.rss_gb

    # Кого винить. Для `REASON_RSS` виноват СВОЙ процесс, и прежняя строка
    # верна дословно. Для `REASON_FREE` оснований винить тест нет НИКОГДА:
    # свободную память мог съесть кто угодно снаружи, а названный тест просто
    # оказался текущим. Поэтому «смотреть надо тест» на этой ветке не
    # печатается ни при каком состоянии подсказки (DEV-52 §2, §6).
    if reason != REASON_FREE:
        blame = "  Смотреть надо тест, названный выше."
    elif top_rss:
        blame = "  Память съели процессы, названные выше, — тест тут ни при чём."
    else:
        # ПОЧЕМУ списка нет — уже сказано строкой выше, и там три РАЗНЫХ
        # причины (§3). Повторять её здесь значит однажды соврать: состояние
        # «перебор ничего не вернул» — это не «снять не удалось».
        blame = "  Память мог съесть процесс СНАРУЖИ — тест тут ни при чём."

    return "\n".join([
        "",
        "=" * 78,
        "РЕМЕНЬ ПО ПАМЯТИ ОСТАНОВИЛ ПРОГОН",
        "  пробито      : %s (%s)" % (crossed, reason),
        "  тест         : %s" % (current_test or _NO_TEST),
        "  свободно RAM : %.2f ГБ   (пол %.2f)" % (free_gb, limits.free_gb),
        "  свой RSS     : %.2f ГБ   (потолок %.2f)" % (rss_gb, limits.rss_gb),
        "  от старта    : %.1f с" % elapsed_s,
    ] + _render_top_rss(top_rss, top_rss_error, top_rss_truncated) + [
        "",
        "  Это НЕ падение теста. Прогон остановлен, чтобы машина не дошла до",
        "  потолка commit: 21.08 такой прогон кончился BSOD и перезагрузкой.",
        blame,
        "=" * 78,
        "",
    ])


class CurrentTest:
    """Кто выполняется прямо сейчас. Заполняется крючками pytest.

    Это и есть причина, по которой ремень живёт ВНУТРИ процесса: снаружи имя
    теста недоступно, и 21.08 его добывали час — счётом точек в файле
    прогресса и отображением 8280-го символа на `--co -q`.
    """

    def __init__(self) -> None:
        self._nodeid: Optional[str] = None
        self._lock = threading.Lock()

    def set(self, nodeid: str) -> None:
        with self._lock:
            self._nodeid = nodeid

    def clear(self) -> None:
        with self._lock:
            self._nodeid = None

    def get(self) -> Optional[str]:
        with self._lock:
            return self._nodeid


def make_emitter(path: Any, stream: Any) -> Callable[[str], None]:
    """Отчёт идёт в ДВА места, и оба сбрасываются сразу.

    Поток — чтобы человек увидел; файл — чтобы отчёт пережил `os._exit`,
    который не исполняет обработчики выхода и не сбрасывает буферы.
    Отказ одного из двух не должен уносить второй: ремень, промолчавший
    из-за прав на каталог, хуже отсутствующего.
    """
    def _emit(text: str) -> None:
        try:
            stream.write(text)
            stream.flush()
        except Exception as exc:              # noqa: BLE001
            print("suite_ram_belt: не удалось написать в поток: %s" % exc,
                  file=sys.__stderr__)
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception as exc:              # noqa: BLE001
            try:
                stream.write("suite_ram_belt: отчёт не лёг в %s: %s\n" % (path, exc))
                stream.flush()
            except Exception:                 # noqa: BLE001
                pass
    return _emit


def collect_top_rss(*, process_iter: Callable[[], Any],
                    self_pid: int,
                    clock: Callable[[], float],
                    count: int = TOP_RSS_COUNT,
                    budget_s: float = TOP_RSS_BUDGET_S,
                    ) -> Tuple[Optional[list], Optional[str], bool]:
    """Кто съел память НА САМОМ ДЕЛЕ (DEV-52 §5).

    Источник процессов ВНЕДРЯЕТСЯ (`process_iter`) — psutil здесь не
    импортируется. Иначе сторожа на эту функцию требовали бы воспроизводить
    аварию, а такой сторож — не сторож, а надежда (Г1).

    Возвращает `(rows, error, truncated)`:

      `rows is None`     перебор ОТКАЗАЛ целиком, причина в `error`;
      `rows == []`       перебор состоялся и не увидел никого;
      `rows == [...]`    вот они, не больше `count`, по убыванию RSS.

    Первое и второе НЕ склеиваются: «не знаем» и «знаем, что пусто» — разные
    положения дел с разными следующими действиями (§3, тот же приём, что
    `drill_never` против `drill_stale`).

    `truncated` — бюджет времени исчерпан, отдано то, что успели (Г3).

    Отказ по ОДНОМУ процессу (исчез между перебором и чтением полей, или не дал
    доступа) пропускает ЭТОТ процесс и не отменяет остальных: подсказка не
    имеет права стоить отчёта (Г2).
    """
    started = clock()
    rows: list = []
    truncated = False
    try:
        for proc in process_iter():
            if clock() - started >= budget_s:
                truncated = True
                break
            try:
                info = getattr(proc, "info", None) or {}
                mem = info.get("memory_info")
                if mem is None:
                    continue
                pid = int(info.get("pid"))
                rows.append(ProcRow(
                    name=str(info.get("name") or "?"),
                    pid=pid,
                    rss_gb=float(mem.rss) / float(2 ** 30),
                    is_self=(pid == self_pid),
                ))
            except Exception:                 # noqa: BLE001
                # Один процесс — не весь перебор (Г2).
                continue
    except Exception as exc:                  # noqa: BLE001
        return None, "%r" % (exc,), truncated

    rows.sort(key=lambda r: r.rss_gb, reverse=True)
    return rows[:count], None, truncated


def _render_top_rss(top_rss: Optional[list], error: Optional[str],
                    truncated: bool) -> list:
    """Блок подсказки. Отдельной функцией — чтобы три состояния §3 были видны
    одним взглядом и ни одно не потерялось в ветвлении отчёта."""
    if error is not None:
        return ["  список процессов не снялся: %s" % error]
    if top_rss is None:
        return ["  список процессов не снимался"]
    if not top_rss:
        return ["  перебор процессов ничего не вернул"]

    lines = ["  съели больше всех:"]
    for n, row in enumerate(top_rss, 1):
        mark = "  <- ЭТОТ ПРОГОН" if row.is_self else ""
        lines.append("    %d. %-22s pid %-7d %.2f ГБ%s"
                     % (n, row.name, row.pid, row.rss_gb, mark))
    if truncated:
        lines.append("       (перебор не уложился в бюджет — список неполный)")
    return lines


class Sampler:
    """Один замер и решение по нему. Всё внешнее внедряется.

    Порядок действий обязателен: сначала отчёт, потом прерывание или выход.
    `os._exit` не сбрасывает буферы, поэтому «отчёт после выхода» = отчёта нет.
    """

    def __init__(self, belt: Belt, *,
                 read: Callable[[], Tuple[float, float]],
                 clock: Callable[[], float],
                 emit: Callable[[str], None],
                 interrupt: Callable[[], None],
                 die: Callable[[int], None],
                 current_test: Callable[[], Optional[str]],
                 started_at: float = 0.0,
                 collect_top: Optional[Callable[[], Tuple[Optional[list],
                                                          Optional[str],
                                                          bool]]] = None) -> None:
        self._belt = belt
        self._read = read
        self._clock = clock
        self._emit = emit
        self._interrupt = interrupt
        self._die = die
        self._current_test = current_test
        self._started_at = started_at
        # Без сборщика (консоль, старые тесты) отчёт печатает «не снимался» —
        # это ЧЕСТНОЕ третье состояние, а не пустой список (§3).
        self._collect_top = collect_top
        # Запомненный отчёт печатает крючок `pytest_keyboard_interrupt`.
        # Живой дрил показал: `sys.stderr` до экрана НЕ доходит — pytest
        # перехватывает поток на уровне дескриптора, а при KeyboardInterrupt
        # перехваченное отбрасывается. Файл остаётся, экран — нет.
        self.last_report: Optional[str] = None

    def tick(self) -> str:
        now = self._clock()
        free_gb, rss_gb = self._read()
        action = self._belt.observe(now=now, free_gb=free_gb, rss_gb=rss_gb)

        if action in (ACTION_INTERRUPT, ACTION_EXIT):
            # Подсказка снимается ЗДЕСЬ, в слое I/O, и уходит в отчёт данными:
            # `render_report` обязана остаться чистой (Г1). И она не имеет
            # права стоить отчёта — отказ сборщика становится ОДНОЙ строкой,
            # а не потерей всего текста (Г2).
            top_rss, top_err, top_cut = None, None, False
            if self._collect_top is not None:
                try:
                    top_rss, top_err, top_cut = self._collect_top()
                except Exception as exc:      # noqa: BLE001
                    top_rss, top_err, top_cut = None, "%r" % (exc,), False

            self.last_report = render_report(
                current_test=self._current_test(),
                free_gb=free_gb, rss_gb=rss_gb,
                elapsed_s=now - self._started_at,
                limits=self._belt.limits,
                reason=self._belt.reason or REASON_RSS,
                hard=self._belt.hard,
                top_rss=top_rss,
                top_rss_error=top_err,
                top_rss_truncated=top_cut,
            )
            self._emit(self.last_report)

        if action == ACTION_INTERRUPT:
            self._interrupt()
        elif action == ACTION_EXIT:
            self._die(self._belt.limits.exit_code)

        return action


@dataclass
class Installed:
    """Ручки для крючков conftest: трекер имени и сэмплер с последним отчётом."""
    tracker: CurrentTest
    sampler: "Sampler"


def install(*, env: Optional[Mapping[str, str]] = None,
            stream: Any = None,
            log_path: Any = None) -> Optional["Installed"]:
    """Завести ремень на текущий процесс pytest.

    Возвращает None, если ремень выключен явным `SUITE_RAM_BELT=0`.
    """
    import _thread
    import time

    limits = limits_from_env(env if env is not None else os.environ)
    if not limits.enabled:
        return None

    try:
        import psutil
    except ImportError:                       # noqa: BLE001
        # Громко, а не молча: «ремня нет» обязано быть видно.
        print("suite_ram_belt: psutil недоступен — РЕМНЯ НЕТ", file=sys.stderr)
        return None

    tracker = CurrentTest()
    proc = psutil.Process()
    started = time.monotonic()
    out = stream if stream is not None else sys.stderr
    target = Path(log_path or BELT_LOG_PATH)
    # Каталог готовит install(), а не make_emitter: тот обязан остаться тупым и
    # проверяемым, включая случай «путь не открылся».
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:                  # noqa: BLE001
        print("suite_ram_belt: каталог под отчёт не создан (%s) — "
              "отчёт пойдёт только в поток" % exc, file=sys.stderr)
    emit = make_emitter(target, out)

    def _die(code: int) -> None:
        try:
            out.flush()
            sys.stdout.flush()
        except Exception:                     # noqa: BLE001
            pass
        os._exit(code)

    # Единственное место, где подсказка встречается с psutil (Г1). Поля берём
    # СПИСКОМ — тогда psutil снимает их одним проходом, а не по одному на
    # процесс, и бюджет Г3 перестаёт быть надеждой.
    def _collect_top() -> Tuple[Optional[list], Optional[str], bool]:
        return collect_top_rss(
            process_iter=lambda: psutil.process_iter(
                ["name", "pid", "memory_info"]),
            self_pid=os.getpid(),
            clock=time.monotonic,
        )

    sampler = Sampler(
        Belt(limits),
        read=lambda: (psutil.virtual_memory().available / float(2 ** 30),
                      proc.memory_info().rss / float(2 ** 30)),
        clock=time.monotonic,
        emit=emit,
        interrupt=_thread.interrupt_main,
        die=_die,
        current_test=tracker.get,
        started_at=started,
        collect_top=_collect_top,
    )

    def _loop() -> None:
        while True:
            time.sleep(limits.sample_s)
            try:
                sampler.tick()
            except Exception as exc:          # noqa: BLE001
                # Ремень не имеет права уронить прогон собой — но и молчать
                # он не имеет права (DEV-18).
                print("suite_ram_belt: замер сорвался: %r" % (exc,), file=sys.stderr)

    threading.Thread(target=_loop, name="suite-ram-belt", daemon=True).start()
    return Installed(tracker=tracker, sampler=sampler)
