"""Дрил-харнесс v1: суфлёр живого дрила (спека 2026-07-25).

Что делает: печатает ВЕСЬ план (все реплики) до старта, на каждом шаге суфлирует
реплику для отправки Ольге, ждёт, что ход случился, снимает факты из БД и лога,
считает вердикт по записанным ожиданиям и складывает отчёт. Отправку реплики
по-прежнему делает человек — автоматика лида это v2 (отдельный аккаунт).
Карточку в Telegram v1 НЕ шлёт: суфлёр живёт в консоли и в прогресс-файле.

Прогресс пишется в отчёт-файл ПОСЛЕ КАЖДОГО ШАГА, а печать идёт с flush: дрил
запускают фоном, а буферизованный stdout превращает честного суфлёра в
молчание (обкатка 2026-07-25 — 45 минут «тишины» на живом сценарии).

Код выхода — вердикт, а не счётчик красного: пропуск шага по тайм-ауту = прогон
НЕ состоялся (2), красные проверки на полном прогоне = 1, всё зелёное = 0.

🔴 ГЛАВНЫЙ ИНВАРИАНТ: харнесс НЕ открывает long-poll Telegram. Раннер УЖЕ
поллит контрол-бота, а второй потребитель на том же токене получает
409 Conflict — упал бы ЖИВОЙ пульт владельца, а не только дрил. Поэтому тап
кнопки принимает поллер раннера (пишет `runtime_flags`), а харнесс читает БД.
Сторож этого инварианта — `tests/test_drill_runner_script.py`.

Дрил идёт на ЖИВОМ раннере: ничего не рестартуем, в клиентскую БД не пишем
(всё чтение — `mode=ro`).

    python scripts/drill_runner.py docs/chatter/drills/d10.yaml --db .secrets/demo.db
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from chatter.core.drill import (  # noqa: E402
    CheckResult, Facts, StepOutcome, check_step, match_step, owner_action,
    parse_scenario, plan_lines, run_verdict, vacuous_expectations,
)

logger = logging.getLogger("jarvis.drill_runner")


def say(text: str) -> None:
    """Печать с немедленным сбросом буфера. Дрил запускают фоном, а stdout в
    фоне блочно буферизован: прошлый прогон 45 минут выглядел как молчание,
    хотя суфлёр исправно печатал каждый шаг."""
    print(text, flush=True)

# Две ставки, потому что ход бывает двух пород (замер 2026-07-25 по llm_usage):
#  · ТЁПЛЫЙ — оба префикса читаются из кэша: brain $0.0120 + классификатор
#    $0.0218 амортизированно = $0.0352 (эта ставка и стоит в тарифной модели);
#  · ХОЛОДНЫЙ — оба префикса пишутся заново по 1h-ставке $6/M: 16 482 токена
#    записи = $0.0989 (83% цены хода) + вход $0.0133 + выход $0.0069 = $0.1191.
# Плоская тёплая ставка занижала смету дрила в 3.4 раза, хотя первый ход
# сценария холодный ПО ДОГОВОРУ (`cache: miss` — плата за новый кэш).
COST_TURN_WARM = 0.0352
COST_TURN_COLD = 0.1191
CONFIRM_THRESHOLD_USD = 0.50
STEP_TIMEOUT_SEC = 15 * 60
# Два пропуска подряд = владельца нет у телефона. Досиживать остальные
# таймауты по 15 минут — это час ожидания ради известного вердикта.
MAX_SKIPS_IN_A_ROW = 2
# Отдельный (длинный) таймер ПЕРВОГО шага: он взводится приходом первой реплики,
# а не запуском скрипта. Прогон №5 сгорел ровно так — первый шаг истёк, пока
# владелец шёл к телефону, и весь дальнейший прогон разъехался на шаг.
FIRST_STEP_TIMEOUT_SEC = 60 * 60
POLL_SEC = 2.0

RATE_IN, RATE_OUT, RATE_CR, RATE_CW5, RATE_CW1H = 3.0, 15.0, 0.30, 3.75, 6.0


def _ro(db: str):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


# ── стенд v2: лид как отдельный процесс ─────────────────────────────────────


def drill_contacts() -> frozenset[str]:
    """Список дрил-контактов берём из `drill_reset.py`, а не заводим третий.

    Два списка уже держатся сторожем (`test_drill_contacts_list_matches_...`);
    третья копия однажды разойдётся с ними, и разойдётся молча — а цена ошибки
    здесь та же, что у сброса: автомат заговорит с живым клиентом."""
    path = Path(__file__).resolve().parent / "drill_reset.py"
    spec = importlib.util.spec_from_file_location("_drill_reset_contacts", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DRILL_CONTACTS


def turn_closed(lines: list[str]) -> bool:
    """Ход закрыт = в логе есть И `process END`, И хотя бы один `OUT`.

    Одного END мало: ход, который ничего не ответил, тоже кончается END'ом, и
    считать его закрытым значит слать следующую реплику в молчащего бота —
    ровно так 26.07 две реплики схлопнулись в один ход."""
    return (any("process END" in ln for ln in lines)
            and any(": OUT " in ln for ln in lines))


class LeadProcess:
    """Живой `drill_lead.py`: поднимается на прогон, гасится в `finally`.

    Своя сессия и свой peer-allowlist живут ТАМ — судья не импортирует
    Telethon вовсе (инвариант v2 §3: боевая и тестовая сессии никогда не
    оказываются в одном процессе)."""

    def __init__(self, *, session: str, peer: int, max_messages: int,
                 peer_username: str | None = None,
                 python: str | None = None, log=say):
        script = Path(__file__).resolve().parent / "drill_lead.py"
        cmd = [python or sys.executable, str(script),
               "--session", session, "--peer", str(peer),
               "--max-messages", str(max_messages)]
        if peer_username:
            cmd += ["--peer-username", peer_username]
        self._log = log
        self._p = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            bufsize=1, env={**os.environ, "PYTHONUTF8": "1"})
        ready = self._readline()
        if "готов" not in ready:
            # Дочитываем ВСЁ, что лид успел сказать. Первая строка падения —
            # это «Traceback (most recent call last):», то есть ровно ноль
            # сведений о причине: 17.08 отказ по TELEGRAM_API_ID выглядел как
            # пустой трейсбек, и причину пришлось искать отдельным запуском.
            rest = ""
            try:
                rest = self._p.stdout.read() or ""
            except Exception:                         # noqa: BLE001
                pass
            self.close()
            raise RuntimeError("лид не поднялся: "
                               + ((ready + rest).strip() or "(тишина)"))
        self._log(f"лид поднят: {ready.strip()}")

    def _readline(self) -> str:
        line = self._p.stdout.readline()
        if not line:
            raise RuntimeError("лид закрыл stdout (упал?)")
        return line

    def say(self, text: str) -> float:
        if "\n" in text or "\r" in text:
            # Протокол построчный: молча склеить строки значит отправить не то,
            # что записано в сценарии, и сверка текста перестанет что-то значить.
            raise RuntimeError("реплика сценария многострочная — стенд такое "
                               "не шлёт")
        self._p.stdin.write(f"SAY {text}\n")
        self._p.stdin.flush()
        while True:
            line = self._readline()
            if line.startswith("SENT "):
                return float(line.split()[1])
            if "FAIL" in line:
                raise RuntimeError(f"лид отказал: {line.strip()}")
            self._log(f"  [lead] {line.rstrip()}")

    def close(self) -> None:
        try:
            if self._p.poll() is None:
                self._p.stdin.write("QUIT\n")
                self._p.stdin.flush()
                self._p.wait(timeout=30)
        except Exception as exc:                      # noqa: BLE001 — DEV-18
            self._log(f"⚠️ лид не закрылся штатно ({exc}) — убиваю")
            self._p.kill()


def _has_table(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


# ── факты хода (только чтение) ───────────────────────────────────────────────


def collect_facts(db: str, *, contact: str, since_ts: float,
                  log_lines: list[str], before: dict,
                  before_bot: dict | None = None) -> Facts:
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        clf = conn.execute(
            "SELECT cache_read_input_tokens cr FROM llm_usage "
            "WHERE ts >= ? AND tag='classifier' ORDER BY ts DESC LIMIT 1",
            (since_ts,)).fetchone()
        # «н/д» ≠ «промах»: ход мог вообще не дойти до классификатора, и
        # объявлять это промахом кэша значило бы врать в отчёте.
        cache = "n/a" if clf is None else ("hit" if (clf["cr"] or 0) > 0 else "miss")

        rows = conn.execute(
            "SELECT okey, status, owed_by FROM contact_obligations WHERE contact_id=?",
            (contact,)).fetchall()
        obligations = {r["okey"]: r["status"] for r in rows}
        # Долги БОТА отдельно: клиентская заметка (owed_by=client, законна после
        # фикса P17) не является шевелением слота брейна.
        obligations_bot = {r["okey"]: r["status"] for r in rows if r["owed_by"] == "bot"}

        prof = conn.execute(
            "SELECT text FROM contact_profile WHERE contact_id=? ORDER BY rowid DESC LIMIT 1",
            (contact,)).fetchone()

        errors = conn.execute(
            "SELECT COUNT(*) n FROM control_events WHERE ts >= ? AND "
            "kind IN ('classifier_error','classifier_recovered')", (since_ts,)).fetchone()["n"]

        cards = conn.execute(
            "SELECT COUNT(*) n FROM console_cards WHERE ts >= ?", (since_ts,)).fetchone()["n"]
    finally:
        conn.close()

    return Facts(
        cache=cache, obligations=obligations, obligations_before=dict(before),
        profile=(prof["text"] if prof else ""), classifier_errors=int(errors),
        cards_delivered=int(cards),
        replies=sum(1 for ln in log_lines if ": OUT " in ln),
        process_ends=sum(1 for ln in log_lines if "process END" in ln),
        obligations_bot=obligations_bot,
        obligations_bot_before=dict(before_bot if before_bot is not None else before))


def snapshot_before(db: str, contact: str) -> tuple[dict, str]:
    """Снимок слота ДО прогона + причина, если прочитать не вышло.

    Ошибку НЕ глотаем (DEV-18): без снимка нельзя ни предупредить о проверках,
    которые уже зелены до старта, ни откатить состояние живого контакта руками
    после прогона."""
    try:
        return obligations_snapshot(db, contact), ""
    except sqlite3.Error as exc:
        logger.warning("снимок слота не прочитан: %s", exc)
        return {}, f"снимок слота ДО прогона НЕ прочитан ({exc}) — прогон вслепую"


def obligations_snapshot(db: str, contact: str, *, only_bot: bool = False) -> dict:
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "contact_obligations"):
            return {}
        rows = conn.execute(
            "SELECT okey, status, owed_by FROM contact_obligations WHERE contact_id=?",
            (contact,)).fetchall()
        return {r["okey"]: r["status"] for r in rows
                if not only_bot or r["owed_by"] == "bot"}
    finally:
        conn.close()


# ── сигнал «ход случился»: сообщение лида ИЛИ тап кнопки ─────────────────────


def new_lead_message(db: str, *, contact: str, since_ts: float) -> str | None:
    """Текст новой реплики лида (None — её ещё нет).

    Текст нужен, чтобы понять, КАКОЙ шаг сценария владелец реально отправил:
    прогон №5 разъехался на шаг, потому что харнесс ждал «любое новое
    сообщение» и молча приписывал ход текущему курсору."""
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT text FROM messages WHERE contact_id=? AND role='user' AND ts > ? "
            "ORDER BY ts LIMIT 1", (contact, since_ts)).fetchone()
        return None if row is None else (row["text"] or "")
    finally:
        conn.close()


def signal_in_other_drill_contact(db: str, *, contact: str, since_ts: float,
                                  contacts) -> str | None:
    """Куда РЕАЛЬНО прилетела реплика лида, если не туда, куда смотрит судья.

    Ровно этот случай 06.08 отчитался как «сигнала не было 10 мин»: сигнал
    был, просто в другом чате — сценарий остался прибит к контакту ручной
    эпохи, а автолид уже писал с тестового аккаунта. Молчащий бот и
    разъехавшаяся проводка стенда обязаны выглядеть по-разному.

    Смотрим ТОЛЬКО на дрил-контакты: живой клиент, написавший боту во время
    прогона, — не расхождение стенда, и объявлять его таковым значит врать в
    обратную сторону."""
    others = [c for c in contacts if c != contact]
    if not others:
        return None
    conn = _ro(db)
    try:
        holes = ",".join("?" * len(others))
        row = conn.execute(
            f"SELECT contact_id FROM messages WHERE role='user' AND ts > ? "
            f"AND contact_id IN ({holes}) ORDER BY ts LIMIT 1",
            (since_ts, *others)).fetchone()
        return None if row is None else str(row[0])
    finally:
        conn.close()


def step_signal_seen(db: str, *, contact: str, since_ts: float, flag_key: str) -> bool:
    """Настоящий сигнал — входящее лида; тап кнопки — запасной путь, если
    сообщение не долетело. Ни один не блокирует другой: забыл тапнуть — прогон
    всё равно едет."""
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE contact_id=? AND role='user' AND ts > ? LIMIT 1",
            (contact, since_ts)).fetchone()
        if row:
            return True
        if _has_table(conn, "runtime_flags"):
            f = conn.execute("SELECT value FROM runtime_flags WHERE key=?",
                             (flag_key,)).fetchone()
            if f and str(f["value"]).strip() not in ("", "0"):
                return True
        return False
    finally:
        conn.close()


# ── деньги ───────────────────────────────────────────────────────────────────


def is_cold(step, *, first: bool) -> bool:
    """Холодным считаем ход, который по сценарию платит за новый кэш. Молчание
    сценария на ПЕРВОМ ходу трактуем как холод: смета не имеет права занижать —
    владелец узнаёт цену до списания, а не после."""
    want = (step.expect or {}).get("cache")
    if want == "miss":
        return True
    if want == "hit":
        return False
    return first


def estimate_cost(steps) -> float:
    return sum(COST_TURN_COLD if is_cold(s, first=(i == 0)) else COST_TURN_WARM
               for i, s in enumerate(steps))


def format_estimate(steps, est: float) -> str:
    cold = sum(1 for i, s in enumerate(steps) if is_cold(s, first=(i == 0)))
    warm = len(steps) - cold
    return (f"смета прогона: {cold}×холодный ${COST_TURN_COLD:.4f} + "
            f"{warm}×тёплый ${COST_TURN_WARM:.4f} ≈ ${est:.2f} "
            f"(факт посчитаю после прогона по ходам дрила)\n"
            f"тёплая ставка держится, пока шаги идут ВНУТРИ часа: TTL кэша 1h, "
            f"пауза длиннее — и каждый следующий ход снова холодный "
            f"(${COST_TURN_COLD:.4f})")


def _row_cost(r) -> float:
    keys = set(r.keys())
    m5 = (r["cache_creation_5m"] if "cache_creation_5m" in keys else 0) or 0
    h1 = (r["cache_creation_1h"] if "cache_creation_1h" in keys else 0) or 0
    write = (m5 * RATE_CW5 + h1 * RATE_CW1H) if (m5 or h1) else \
        (r["cache_creation_input_tokens"] or 0) * RATE_CW5
    return (r["input_tokens"] * RATE_IN + r["output_tokens"] * RATE_OUT
            + r["cache_read_input_tokens"] * RATE_CR + write) / 1_000_000


def measure_spend(db: str, *, windows) -> float:
    """Факт по llm_usage со ставкой записи ПОСТРОЧНО (5m $3.75/M против
    1h $6/M) — методика obligations_cost_delta.py.

    Считаем ТОЛЬКО окна выполненных шагов, а не «от старта до конца прогона».
    Дрил из четырёх ходов живёт часами (шаг ждёт человека до 15 минут), и любой
    чужой ход, легший в паузу, дорожал бы дрилу. Пропущенный шаг окна не даёт
    вовсе: ходов он не делал.
    """
    if not windows:
        return 0.0
    conn = _ro(db)
    conn.row_factory = sqlite3.Row
    try:
        total = 0.0
        for start, end in windows:
            for r in conn.execute("SELECT * FROM llm_usage WHERE ts >= ? AND ts <= ?",
                                  (start, end)):
                total += _row_cost(r)
        return total
    finally:
        conn.close()


# ── отчёт ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Money:
    estimate: float   # смета ДО прогона
    drill: float      # факт по окнам выполненных шагов
    window: float     # всё, что легло в окно прогона целиком

    @property
    def foreign(self) -> float:
        return max(0.0, self.window - self.drill)


def _mark(o, running: str) -> str:
    if o.skipped:
        return "⛔"          # шага не было — это не «нейтрально», это провал прогона
    if not o.checks:
        return "⏳" if running else "➖"   # ⏳ ещё не дошли · ➖ шаг без проверок
    return "✅" if o.failed == 0 else "🔴"


def format_report(name: str, outcomes, *, money: Money, running: str = "") -> str:
    v = run_verdict(outcomes)
    lines = [f"# Дрил: {name}", ""]
    lines.append(running or f"{v.headline} — {v.detail}")
    lines.append("")
    lines.append("## План (все реплики)")
    for i, o in enumerate(outcomes, 1):
        lines.append(f"{i}. «{o.say}»")
    lines.append("")
    for i, o in enumerate(outcomes, 1):
        lines.append(f"## {_mark(o, running)} Шаг {i}: «{o.say}»")
        for c in o.checks:
            lines.append(f"- {'✅' if c.ok else '🔴'} `{c.key}` — {c.detail}")
        if o.note:
            lines.append(f"- ⚠️ {o.note}")
        lines.append("")
    lines.append(f"**Деньги:** смета ${money.estimate:.4f} · "
                 f"факт по ходам дрила ${money.drill:.4f} · "
                 f"отклонение ×{(money.drill / money.estimate if money.estimate else 0):.2f}")
    lines.append(f"- в окне прогона всего ${money.window:.4f}; "
                 + (f"⚠️ посторонний трафик ${money.foreign:.4f} — в счёт дрила НЕ включён"
                    if money.foreign > 1e-9 else "постороннего трафика нет"))
    return "\n".join(lines)


# ── живой шов ────────────────────────────────────────────────────────────────


def backup_log(log_path: Path) -> Path | None:
    """Раннер УСЕКАЕТ свой лог при каждом старте — без .bak форензика прогона
    исчезнет при первом же респавне гардианом."""
    if not log_path.is_file():
        return None
    dest = log_path.with_name(f"{log_path.name}.drill-{int(time.time())}.bak")
    shutil.copy2(log_path, dest)
    return dest


def _read_log_since(log_path: Path, offset: int) -> tuple[list[str], int]:
    if not log_path.is_file():
        return [], offset
    data = log_path.read_text(encoding="utf-8", errors="replace")
    return data[offset:].splitlines(), len(data)


def main(argv=None, *, lead_factory=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--db", default=str(_ROOT / ".secrets" / "demo.db"))
    ap.add_argument("--contact", default=None,
                    help="кого судить: contact_id вида <peer>:<persona>. По "
                         "умолчанию — контакт из сценария (это дефолт РУЧНОГО "
                         "прогона). Ночной регресс передаёт свой: сценарий и "
                         "отправитель разъехались 06.08, и судья 20 минут ждал "
                         "сигнал в чужом чате при живом боте и живом лиде")
    ap.add_argument("--log", default=str(_ROOT / "logs" / "chatter_volska.log"))
    ap.add_argument("--out", default=str(_ROOT / "state" / "drills"))
    ap.add_argument("--yes", action="store_true",
                    help="ЗАПУСТИТЬ прогон (без флага печатается только план)")
    ap.add_argument("--dry-run", action="store_true",
                    help="напечатать план и смету, ничего не делать")
    ap.add_argument("--step-timeout", type=float, default=STEP_TIMEOUT_SEC)
    ap.add_argument("--first-step-timeout", type=float, default=None,
                    help="ожидание ПЕРВОЙ реплики (таймер шагов взводится ею); "
                         "по умолчанию час, но при явно укороченном --step-timeout "
                         "равен ему — это прогон-проверка, а не живой дрил")
    ap.add_argument("--auto-lead", action="store_true",
                    help="стенд v2: реплики шлёт процесс лида, человек не нужен")
    ap.add_argument("--lead-session",
                    default=str(_ROOT / ".secrets" / "drill_lead.session"))
    ap.add_argument("--lead-peer", type=int, default=None,
                    help="кому пишет лид (аккаунт клиента); сверяется с "
                         "allowlist'ом внутри drill_lead")
    ap.add_argument("--lead-peer-username", default=None,
                    help="username того же аккаунта — нужен, пока у лида нет "
                         "диалога с ним (Telethon не резолвит голый id). "
                         "Разрешение всё равно по --lead-peer")
    ap.add_argument("--lead-python", default=None)
    a = ap.parse_args(argv)
    if a.auto_lead:
        # Часовой таймер первого шага существует ради человека, идущего к
        # телефону. В авторежиме человека нет — час ожидания лишь маскирует
        # поломку стенда.
        a.first_step_timeout = a.step_timeout
    if a.first_step_timeout is None:
        # Живой дрил: первую реплику ждём час (человек идёт к телефону).
        # Явно укороченный --step-timeout = не живой прогон, а проверка самого
        # харнесса: тогда и первый шаг не должен висеть час.
        a.first_step_timeout = (FIRST_STEP_TIMEOUT_SEC
                                if a.step_timeout == STEP_TIMEOUT_SEC
                                else a.step_timeout)

    sc = parse_scenario(Path(a.scenario).read_text(encoding="utf-8"))
    # Контакт у прогона ОДИН, и выбирает его вызывающий. Два молча
    # разъезжающихся дефолта (сценарий ручной эпохи против отправителя
    # авторежима) уже стоили одного ночного прогона: судья опрашивал контакт
    # из yaml, реплики приходили в тестовый аккаунт.
    contact = a.contact or sc.contact

    known_contacts: frozenset[str] = frozenset()
    if a.auto_lead:
        # Автолид САМ отправляет сообщения. Направить его на контакт живого
        # клиента — это разговор с клиентом от имени стенда, и он необратим:
        # сообщение уже увидели. Тот же предохранитель, что на сбросе.
        known_contacts = drill_contacts()
        if contact not in known_contacts:
            say(f"⛔ ОТКАЗ: контакт прогона «{contact}» не дрил-контакт. "
                f"--auto-lead работает только на {', '.join(sorted(known_contacts))}")
            return 2
        if a.lead_peer is None:
            say("⛔ ОТКАЗ: --auto-lead без --lead-peer. Получатель задаётся "
                "явно и сверяется с allowlist'ом лида")
            return 2

    est = estimate_cost(sc.steps)
    say(format_estimate(sc.steps, est))

    before0, snap_note = snapshot_before(a.db, contact)
    say(f"\n⚠️ {snap_note}" if snap_note
        else f"\nснимок слота ДО прогона: {before0 or '(пусто)'}")
    for w in vacuous_expectations(sc, before0):
        say(f"⚠️ {w}")

    say("\nПЛАН ПРОГОНА — все реплики целиком, читать сверху вниз:")
    for line in plan_lines(sc):
        say(f"  {line}")

    # Запуск ТОЛЬКО по явному --yes. Дрил стоит живых денег и требует действий
    # человека у телефона: прогон «просто посмотреть, что за скрипт» не должен
    # молча уходить в ожидание сигналов по 15 минут на шаг (поймано на себе при
    # первой же обкатке 2026-07-25).
    if a.dry_run or not a.yes:
        say("\nэто ПЛАН. Запуск: добавь --yes (и будь у телефона — "
            "каждый шаг ждёт твоей реплики Ольге)")
        return 0

    log_path = Path(a.log)
    bak = backup_log(log_path)
    if bak:
        say(f"лог сохранён: {bak.name}")

    run_start = time.time()
    before = before0
    # Долги бота отдельным снимком: «слот не шевельнулся» судит по ним, а не по
    # клиентским заметкам (P17). Снимок не прочитан — сравнивать не с чем.
    before_bot = {} if snap_note else obligations_snapshot(
        a.db, contact, only_bot=True)
    outcomes: list[StepOutcome] = [StepOutcome(say=s.say) for s in sc.steps]
    skips_in_a_row = 0
    windows: list[tuple[float, float]] = []
    _, offset = _read_log_since(log_path, 0)

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{int(run_start)}.md"

    def flush_progress(running: str) -> None:
        """Прогресс живёт в ФАЙЛЕ, а не в чьём-то терминале: фоновый stdout
        буферизован, и прошлый прогон 45 минут выглядел как молчание."""
        out_file.write_text(
            format_report(sc.name, outcomes,
                          money=Money(estimate=est,
                                      drill=measure_spend(a.db, windows=windows),
                                      window=measure_spend(
                                          a.db, windows=[(run_start, time.time())])),
                          running=running),
            encoding="utf-8")

    flush_progress(f"⏳ ПРОГОН ИДЁТ — шаг 1/{len(sc.steps)}, ждём реплику")
    say(f"прогресс пишется в {out_file} (читается на любой стадии)")

    lead = None
    if a.auto_lead:
        factory = lead_factory or LeadProcess
        try:
            lead = factory(session=a.lead_session, peer=a.lead_peer,
                           peer_username=a.lead_peer_username,
                           # Потолок = ровно длина сценария: зацикленный
                           # оркестратор упрётся в него раньше, чем в дефолт.
                           max_messages=len(sc.steps), python=a.lead_python)
        except Exception as exc:                      # noqa: BLE001 — DEV-18
            say(f"⛔ ОТКАЗ: лид не поднялся: {exc}")
            return 2

    cursor = 0
    armed = False   # первая реплика ещё не пришла — таймер шага не взведён
    try:
        while cursor < len(sc.steps):
            i = cursor + 1
            step = sc.steps[cursor]
            hint = owner_action(step)
            say(f"\n=== ШАГ {i}/{len(sc.steps)} — "
                + ("стенд отправляет сам: ===" if a.auto_lead else "отправь Ольге: ===")
                + f"\n{step.say}\n" + (f"    ⚠️ {hint}\n" if hint else ""))
            flush_progress(f"⏳ ПРОГОН ИДЁТ — шаг {i}/{len(sc.steps)}, ждём реплику")
            step_start = time.time()
            flag_key = f"drill:{i}"
            note = None
            if a.auto_lead:
                try:
                    lead.say(step.say)
                except Exception as exc:                  # noqa: BLE001 — DEV-18
                    # Реплика не ушла — шага не было. Красить это в зелёное нельзя,
                    # и досиживать таймаут бессмысленно.
                    note = f"стенд не смог отправить реплику: {exc}"
                    say(f"⛔ {note}")
                    for j in range(cursor, len(sc.steps)):
                        outcomes[j] = StepOutcome(say=sc.steps[j].say, skipped=True,
                                                  note=note)
                    break
            # Пока не пришла ПЕРВАЯ реплика, действует свой (длинный) таймер:
            # прогон не должен умирать, пока человек идёт к телефону (прогон №5
            # сгорел ровно так — первый шаг истёк до того, как владелец начал).
            limit = a.step_timeout if armed else a.first_step_timeout
            deadline = step_start + limit
            seen_text = None
            while time.time() < deadline:
                seen_text = new_lead_message(a.db, contact=contact, since_ts=step_start)
                if seen_text is not None:
                    break
                if step_signal_seen(a.db, contact=contact, since_ts=step_start,
                                    flag_key=flag_key):
                    break
                time.sleep(POLL_SEC)
            else:
                # Сигнала нет — но ПОЧЕМУ? «Бот молчит» и «стенд смотрит не в
                # тот чат» требуют разных действий, и путать их дорого: 06.08
                # разъехавшаяся проводка 20 минут выглядела как молчание.
                misrouted = (signal_in_other_drill_contact(
                    a.db, contact=contact, since_ts=step_start,
                    contacts=known_contacts) if a.auto_lead else None)
                if misrouted:
                    note = (f"стенд ждал реплику в «{contact}», а она пришла в "
                            f"«{misrouted}» — сценарий и отправитель смотрят на "
                            f"разные контакты")
                    say(f"⛔ {note}")
                    # Досиживать остальные шаги нечего: следующий упрётся в то
                    # же расхождение, потратив ещё таймаут и ещё живых денег.
                    for j in range(cursor, len(sc.steps)):
                        outcomes[j] = StepOutcome(say=sc.steps[j].say, skipped=True,
                                                  note=note)
                    break
                note = f"шаг пропущен: сигнала не было {limit / 60:.1f} мин"
                say(f"⛔ {note}")
                outcomes[i - 1] = StepOutcome(say=step.say, skipped=True, note=note)
                cursor += 1
                skips_in_a_row += 1
                # Fail-fast. Первый прогон досиживал КАЖДЫЙ таймаут: четыре шага без
                # человека = час ожидания ради вердикта, известного после второго
                # пропуска. Владелец вышел — прогон закрываем, а не досиживаем.
                if skips_in_a_row >= MAX_SKIPS_IN_A_ROW and i < len(sc.steps):
                    closed = (f"прогон закрыт досрочно: {skips_in_a_row} пропуска "
                              f"подряд — шаг не выполнялся")
                    for j in range(i, len(sc.steps)):
                        outcomes[j] = StepOutcome(say=sc.steps[j].say, skipped=True,
                                                  note=closed)
                    say(f"\n🛑 два пропуска подряд — закрываю прогон досрочно "
                        f"(осталось невыполненных шагов: {len(sc.steps) - i})")
                    break
                flush_progress(f"⏳ ПРОГОН ИДЁТ — шаг {i} пропущен")
                continue

            armed = True
            # Курсор идёт за РЕАЛЬНОЙ репликой, а не за счётчиком шагов: владелец
            # мог опоздать или перескочить, и тогда проверки шага N применялись бы
            # к чужому ходу (прогон №5 разъехался ровно так, весь отчёт стал
            # нечитаемым). Сопоставление нестрогое — опечатка прогон не рвёт.
            bench_fail = None
            if seen_text:
                idx = match_step(seen_text, sc.steps)
                if idx is None:
                    note = (f"текст не совпал ни с одним шагом сценария: "
                            f"«{seen_text[:60]}» — считаю ходом шага {i}")
                    say(f"⚠️ {note}")
                    bench_fail = note
                elif idx != cursor:
                    jumped = [j for j in range(cursor, idx)]
                    for j in jumped:
                        outcomes[j] = StepOutcome(
                            say=sc.steps[j].say, skipped=True,
                            note="не выполнялся: владелец отправил реплику другого шага")
                    note = (f"курсор переставлен: пришла реплика шага {idx + 1}, "
                            f"а ждали шаг {i}")
                    say(f"⚠️ {note}")
                    bench_fail = note
                    cursor, i = idx, idx + 1
                    step = sc.steps[cursor]

            # Ход мог ещё договаривать баббл — дадим ему закрыться по process END.
            # В авторежиме закрытие хода — ИНВАРИАНТ, а не вежливость: следующая
            # реплика не уходит, пока ход не закрылся (26.07 две реплики за 46 с
            # схлопнулись в один ход и ответ на первую отменился).
            if a.auto_lead:
                closed_ok = False
                while time.time() < deadline:
                    lines, _ = _read_log_since(log_path, offset)
                    if turn_closed(lines):
                        closed_ok = True
                        break
                    time.sleep(POLL_SEC)
                if not closed_ok:
                    note = ("ход не закрылся (нет пары `process END` + `OUT`) за "
                            f"{limit / 60:.1f} мин")
                    say(f"⛔ {note}")
                    _, offset = _read_log_since(log_path, offset)
                    outcomes[i - 1] = StepOutcome(say=step.say, skipped=True, note=note)
                    cursor += 1
                    skips_in_a_row += 1
                    if skips_in_a_row >= MAX_SKIPS_IN_A_ROW and i < len(sc.steps):
                        closed = (f"прогон закрыт досрочно: {skips_in_a_row} пропуска "
                                  f"подряд — шаг не выполнялся")
                        for j in range(i, len(sc.steps)):
                            outcomes[j] = StepOutcome(say=sc.steps[j].say, skipped=True,
                                                      note=closed)
                        say("\n🛑 два пропуска подряд — закрываю прогон досрочно")
                        break
                    flush_progress(f"⏳ ПРОГОН ИДЁТ — шаг {i} пропущен")
                    continue
            else:
                for _ in range(60):
                    lines, _ = _read_log_since(log_path, offset)
                    if any("process END" in ln for ln in lines):
                        break
                    time.sleep(POLL_SEC)
            lines, offset = _read_log_since(log_path, offset)
            facts = collect_facts(a.db, contact=contact, since_ts=step_start,
                                  log_lines=lines, before=before,
                                  before_bot=before_bot)
            checks = check_step(step.expect, facts)
            if a.auto_lead and bench_fail:
                # Человек мог опечататься — стенд шлёт РОВНО сценарную строку.
                # Значит расхождение это не «владелец перескочил», а баг стенда, и
                # предупреждением его прятать нельзя.
                checks = [CheckResult(key="bench", ok=False, detail=bench_fail)] + list(checks)
            for c in checks:
                say(f"  {'✅' if c.ok else '🔴'} {c.key}: {c.detail}")
            # Окно шага закрывается ЗДЕСЬ: в счёт дрила идут только ходы дрила,
            # а не всё, что случилось, пока владелец шёл к телефону.
            windows.append((step_start, time.time()))
            outcomes[i - 1] = StepOutcome(say=step.say, checks=tuple(checks),
                                          note=note or "")
            skips_in_a_row = 0     # шаг состоялся — считаем подряд идущие заново
            cursor += 1
            before = facts.obligations
            before_bot = facts.obligations_bot
            flush_progress("")
    finally:
        # Долгоживущего демона нет: лид гаснет вместе с прогоном, чем бы тот
        # ни кончился.
        if lead is not None:
            lead.close()

    money = Money(estimate=est,
                  drill=measure_spend(a.db, windows=windows),
                  window=measure_spend(a.db, windows=[(run_start, time.time())]))
    report = format_report(sc.name, outcomes, money=money)
    out_file.write_text(report, encoding="utf-8")
    say(f"\n{report}\n\nотчёт: {out_file}")
    # Код выхода — это ВЕРДИКТ, а не «сколько красного увидели»: прогон, где
    # шаги пропущены по таймауту, не состоялся и не может быть принят молча.
    v = run_verdict(outcomes)
    say(f"\n{v.headline} — {v.detail} (exit {v.code})")
    return v.code


if __name__ == "__main__":
    raise SystemExit(main())
