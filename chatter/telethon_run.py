from __future__ import annotations
import argparse
import asyncio
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Mapping

from telethon import events
from telethon.errors import AuthKeyError, UnauthorizedError

from chatter.config.active import ActiveClientsError, resolve_personas
from chatter.config.loader import Config, ConfigError, ControlConfig, load_config
from chatter.config.yaml_edit import YamlEditError, set_funnel_gate
from chatter.core import humanizer as H
from chatter.core.admission import admission_decision
from chatter.core.brain import Brain
from chatter.core.config_versions import (
    CONFIG_FILES, latest_version, previous_version, restore, snapshot,
)
from chatter.core.classifier import ClassifierResult, classify as _classify
from chatter.core.console import (
    GLOBAL_COMMANDS, TARGETED_COMMANDS, PauseView, _humanize_gap, cfg_text,
    console_text, contact_link, display_name, escalation_buttons, format_config,
    format_escalation_card, format_status, html_link, parse_command,
    parse_config_command, pause_buttons,
    safe_snippet,
)

# Префиксы команд пульта для одноразовой чистки истории от «/resume» и т.п.,
# которые владелец мог набрать в диалоге лида ДО Fix 3 (см. _on_connected).
_COMMAND_PREFIXES = sorted("/" + c for c in (GLOBAL_COMMANDS | TARGETED_COMMANDS))
from chatter.core.escalation import parse_escalation_keywords
from chatter.core.llm import AnthropicLLM, FakeLLM, LLMClient
from chatter.core.pause import should_auto_resume
from chatter.notify.base import Card, Notifier
from chatter.notify.control_bot import ControlBotNotifier, ControlBotPoller
from chatter.notify.saved_messages import SavedMessagesNotifier
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.telethon_tg import SentRegistry, TelethonTransport, send_alert
from chatter.telethon_login import (
    DEFAULT_ENV_FILE, CredentialsError, _parse_env_file, load_api_credentials,
)

# Session-loss errors (spec S2/S8): the account got logged out / the saved
# session is no longer valid. AuthKeyError is the base of the
# AuthKey*Error family (Duplicated/Invalid/NotFound/PermEmpty/Unregistered);
# UnauthorizedError is Telethon's separate "you are not authorized" RPC
# error. Catching both, rather than a bare `except Exception`, keeps this
# from swallowing unrelated bugs -- only genuine session loss maps to the
# graceful "re-run telethon_login" path.
SESSION_LOST_ERRORS = (UnauthorizedError, AuthKeyError)

log = logging.getLogger("chatter.telethon_run")

# --------------------------------------------------------------------------
# Онбординг-дырка №4: рантайм-файлы выводятся из slug клиента.
#
# Раньше --session/--db имели ОДИН дефолт на всех, поэтому второй клиент,
# запущенный без флагов, молча садился на сессию и БД первого. Отказ был не
# на первом клиенте, а ровно в момент масштабирования — худший момент.
# Теперь путь выводится из первичного slug'а: забыть флаг НЕЛЬЗЯ, потому
# что флага больше не нужно.
# --------------------------------------------------------------------------
SECRETS_DIR = Path(".secrets")
LEGACY_SESSION_NAME = "chatter_telethon.session"
LEGACY_DB_NAME = "chatter_telethon.db"
# Первичный slug боевого деплоя, который УЖЕ живёт на общем дефолте: только
# его файлы имеет право забрать миграция (иначе новый клиент подхватил бы
# чужую сессию — ту самую аварию, от которой мы и уходим).
LEGACY_OWNER_SLUG = "demo"


def derive_session_path(primary_slug: str, secrets_dir: Path | None = None) -> str:
    return str((secrets_dir or SECRETS_DIR) / f"{primary_slug}.session")


def derive_db_path(primary_slug: str, secrets_dir: Path | None = None) -> str:
    return str((secrets_dir or SECRETS_DIR) / f"{primary_slug}.db")


def resolve_runtime_paths(
    *, primary_slug: str, session_arg: str | None = None, db_arg: str | None = None,
    env: Mapping[str, str] | None = None, secrets_dir: Path | None = None,
) -> tuple[str, str]:
    """(session, db). Приоритет: явный флаг > env > вывод из slug'а.

    Дефолта «общий на всех» больше нет — отсутствие флага даёт РАЗНЫЕ пути
    для разных клиентов, а не одинаковые."""
    env = {} if env is None else env
    session = session_arg or env.get("TELETHON_SESSION") or derive_session_path(
        primary_slug, secrets_dir)
    db = db_arg or env.get("CHATTER_DB") or derive_db_path(primary_slug, secrets_dir)
    return session, db


def migrate_legacy_runtime_files(
    *, session_path: str, db_path: str, secrets_dir: Path | None = None,
    legacy_owner_slug: str = LEGACY_OWNER_SLUG,
) -> list[str]:
    """Одноразовый перенос боевых файлов со старого общего дефолта на новый
    per-slug путь. Без него смена дефолта = РАЗЛОГИН живой Ани (сессия лежит
    в .secrets/chatter_telethon.session и правится каждый рестарт).

    Переносим, только если цель ещё не существует (чужое не затираем) и только
    в путь первичного legacy-деплоя (`legacy_owner_slug`), иначе второй клиент
    забрал бы сессию первого. Возвращает список выполненных переносов."""
    root = secrets_dir or SECRETS_DIR
    moved: list[str] = []
    pairs = (
        (root / LEGACY_SESSION_NAME, Path(session_path), f"{legacy_owner_slug}.session"),
        (root / LEGACY_DB_NAME, Path(db_path), f"{legacy_owner_slug}.db"),
    )
    for legacy, target, expected_name in pairs:
        if target.name != expected_name:
            continue                       # не путь legacy-владельца — не трогаем
        if not legacy.exists() or target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(legacy, target)
        except OSError:
            # DEV-18: не молча. Не мигрировали — не фатально (Telethon создаст
            # новую сессию и попросит логин), но оператор обязан это увидеть.
            log.exception("миграция %s -> %s не удалась", legacy, target)
            continue
        log.warning("онбординг-№4: перенёс %s -> %s (одноразовая миграция)", legacy, target)
        moved.append(str(target))
    return moved

# Catch-up age cap: on start, don't answer anything older than this. A message
# from last week doesn't need a live reply -- answering ancient backlog reads as
# broken, not attentive. 24h is generous enough to cover any realistic downtime
# (reboot, deploy, crash) while never resurrecting stale conversations.
CATCHUP_MAX_AGE_SECONDS = 24 * 3600

# Own liveness stamp for the guardian (mirrors the main bot's bot_heartbeat.txt):
# the runner rewrites this every HEARTBEAT_INTERVAL_SECONDS so a HUNG runner
# (process alive but event loop wedged) is detected, not just a dead PID.
HEARTBEAT_PATH = Path("state") / "chatter_heartbeat.txt"
HEARTBEAT_INTERVAL_SECONDS = 30

# Период авто-возврата (спека §8). Объявлена ЗДЕСЬ, на уровне модуля, ДО
# любых def -- её использует и render_status() (Task 12, ниже) как дефолт
# отображения, и autoresume_loop() (Task 13) КАК ЗНАЧЕНИЕ ПО УМОЛЧАНИЮ
# параметра `interval`. Дефолтные значения параметров вычисляются в момент
# ВЫПОЛНЕНИЯ `def`, а не вызова функции -- если бы эта константа была
# объявлена НИЖЕ функции, которая её использует как дефолт, импорт модуля
# упал бы с NameError ещё до того, как что-либо успело выполниться.
AUTORESUME_INTERVAL_SECONDS = 60.0

# config-арка §5: как часто опрашивать mtime конфига при auto_reload.
CONFIG_WATCH_INTERVAL_SECONDS = 5.0


# --- 0. catch-up: pick up messages that arrived while OFFLINE ---------------
@dataclass
class MissedMessage:
    sender_id: int
    texts: list[str]              # chronological (oldest first)
    oldest_age_seconds: float


def select_missed(
    dialogs: list[dict], *, allowlist: frozenset[int], now: float, max_age_seconds: float,
    denylist: frozenset[int] = frozenset(), funnel_gate: bool = False,
) -> list[MissedMessage]:
    """Pure core of catch-up. Given a snapshot of private dialogs with unread
    messages, decide which to answer after a restart.

    `dialogs` is transport-agnostic: each item is
        {sender_id: int, is_user: bool, is_bot: bool,
         messages: [{text: str, out: bool, date_ts: float}, ...]}

    Keeps, per allowlisted human DM: inbound (`out` False), non-blank messages
    within `max_age_seconds`, in chronological order. Drops groups/channels
    (`is_user` False), bots, non-allowlisted senders, our own outgoing, blanks,
    and anything older than the age cap. Empty result for a dialog with nothing
    left to answer."""
    out: list[MissedMessage] = []
    for d in dialogs:
        if not d.get("is_user") or d.get("is_bot"):
            continue
        # Арка 3C: тот же гейт, что live (handle_event). Оффлайн-незнакомец —
        # тоже лид; знакомый/denylist — не отвечаем. funnel_gate off → старое
        # поведение (только allowlist).
        if admission_decision(
            sender_id=d.get("sender_id"), is_contact=bool(d.get("is_contact")),
            allowlist=allowlist, denylist=denylist, funnel_gate=funnel_gate,
        ) != "answer":
            continue
        picked = [
            m for m in sorted(d.get("messages", []), key=lambda m: m["date_ts"])
            if not m.get("out")
            and (m.get("text") or "").strip()
            and (now - m["date_ts"]) <= max_age_seconds
        ]
        if picked:
            out.append(MissedMessage(
                sender_id=d["sender_id"],
                texts=[m["text"] for m in picked],
                oldest_age_seconds=now - picked[0]["date_ts"],
            ))
    return out


def write_heartbeat(path: Path = HEARTBEAT_PATH, *, now: float | None = None) -> None:
    """Stamp the runner's liveness file with the current unix time (seconds).
    Best-effort: a failure here must never take down the runner."""
    ts = int(time.time() if now is None else now)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(ts), encoding="ascii")
    except Exception:
        log.warning("failed to write heartbeat to %s", path, exc_info=True)


# --- 1. eligibility filter -------------------------------------------------
# Pure, sync-testable (spec S3). The event handler extracts booleans from the
# real Telethon event and calls this; no Telethon object is inspected here.
def should_handle(
    *, is_private: bool, is_outgoing: bool, sender_is_bot: bool, is_service: bool,
) -> bool:
    """Only handle private, inbound, human, non-service messages. Groups,
    channels, our own outgoing messages, bot senders and service messages
    (pins, member-joins, etc.) are ignored -- silently, per spec S3."""
    return is_private and not is_outgoing and not sender_is_bot and not is_service


# --- 1b. outgoing / human-takeover detection (arc 3A, spec §3) -------------
async def decide_outgoing(msg_id: int, *, registry: SentRegistry, grace_seconds: float) -> str:
    """'ours' | 'human' — кто отправил это исходящее.

    ГРЕЙС-ОКНО — НЕ ПАРАНОЙЯ И НЕ КОСТЫЛЬ. Гонка здесь СТРУКТУРНА, она обязана
    случаться, и вот почему: TelethonTransport.send() крутится в worker-потоке
    (process_batch синхронный) и маршалит send_message на event loop через
    run_coroutine_threadsafe, а ЭТОТ обработчик живёт НА том же loop. Значит
    loop физически может раздать апдейт о нашем сообщении раньше, чем
    worker-поток проснётся и запишет id в реестр. Порядок не зависит от нашего
    кода — он зависит от планировщика.

    Цена проигранной гонки: Аня опознаёт СВОЁ сообщение как чужое → глушит сама
    себя → молчит навсегда, тихо, и это худший отказ продукта.

    Поэтому неопознанный id НЕ решается мгновенно: ждём грейс, перепроверяем
    реестр. 2 секунды невидимы на фоне её ритма в 30-50с.

    ⚠️ НЕ УДАЛЯТЬ как «лишнюю задержку»: без этого окна Аня начнёт глушить себя
    ровно тогда, когда планировщик окажется быстрее, — то есть под нагрузкой и
    не воспроизводимо на тестовой машине. Тесты
    test_id_registered_during_the_grace_window_is_ours_not_a_takeover и
    test_our_own_message_never_triggers_a_takeover стерегут это."""
    if registry.is_ours(msg_id):
        return "ours"
    await asyncio.sleep(grace_seconds)
    return "ours" if registry.is_ours(msg_id) else "human"


def resolve_numbered_target(
    store: Store, target: str | None, *, language: str = "ru",
) -> tuple[str | None, str | None]:
    """Различить номер из /status и id/юзернейм/ссылку (спека 3A-UX §3).

    ПОЧЕМУ пробуем номер СНАЧАЛА, а не по величине строки: "номера
    маленькие, id большие" -- хрупкая эвристика (ничто не гарантирует, что
    Telegram не выдаст маленький id), а `status_index` -- это ФАКТ: если
    запись под этим n существует, её только что выдал последний /status,
    значит владелец скопировал её оттуда глазами (единственный способ
    вообще узнать номер).

    Три исхода:
    - target НЕ чисто цифровой -- это не номер вообще, (None, None), пусть
      вызывающий код (TelethonRunner.resolve_target) резолвит по-старому
      (id/юзернейм/ссылка).
    - target цифровой, но номера с таким n НЕТ в таблице -- тоже (None,
      None), а НЕ "нет такого номера": чисто цифровая строка ВСЁ РАВНО
      "похожа на id" (задание прямо требует в этом случае резолвить по
      старому пути), так что здесь мы не утверждаем ошибку, а отступаем.
    - target цифровой И номер НАЙДЕН -- владелец точно целился по номеру
      (иначе такого совпадения взяться неоткуда). Здесь и ТОЛЬКО здесь
      имеет смысл проверка "тот ли это список, который он видел" (спека
      §3): промах в чужой диалог -- катастрофа доверия, поэтому
      рассогласованный набор блокирует действие текстом об устаревании,
      а не тихо резолвит не в того адресата."""
    if target is None or not target.isdigit():
        return None, None
    contact_id = store.status_index_contact(int(target))
    if contact_id is None:
        return None, None
    muted_ids = [row["contact_id"] for row in store.muted_contacts()]
    if not store.status_index_is_current(muted_ids):
        return None, console_text("list_is_stale", language)
    return contact_id, None


# --- 1c. console: Saved Messages pult (arc 3A, spec §7) ---------------------
def execute_command(cmd, *, store: Store, contact_id: str | None, now: float,
                     status_text: str | None = None, target_error: str | None = None,
                     language: str = "ru") -> str:
    """Исполнить команду пульта. ЧИСТАЯ относительно Telethon: трогает только
    `store`, поэтому тестируется юнитами без сети (см. test_console_wiring.py).
    `contact_id` уже разрешён раннером (из реплая на карточку, номера или
    явного аргумента) -- эта функция про адресацию по Telethon не знает.
    `target_error` -- готовое человеческое объяснение, ЕСЛИ адресация
    провалилась осмысленно (например «список устарел» -- спека §3): когда
    оно задано, действие НЕ выполняется, даже если contact_id почему-то не
    None -- сообщение об ошибке всегда сильнее попытки исполнения."""
    if cmd.error:
        # Парсер уже сформулировал жалобу человеческим языком (DEV-18: молча
        # проглотить кривой аргумент значило бы, что владелец думает, что
        # пауза встала).
        return f"⚠️ {cmd.error}"

    if cmd.name == "status":
        return status_text or "статус недоступен"
    if cmd.name == "stop":
        store.set_runtime_flag("kill_switch", "1", ts=now)
        store.add_event("kill_on", ts=now)
        return "🔴 Аня ЗАГЛУШЕНА во всех диалогах. Вернуть: /start"
    if cmd.name == "start":
        store.set_runtime_flag("kill_switch", "0", ts=now)
        store.add_event("kill_off", ts=now)
        return "✅ Аня снова работает во всех диалогах."
    if cmd.name == "help":
        return console_text("help_text", language)

    if target_error:
        # Адресация провалилась ОСМЫСЛЕННО (устаревший список и т.п.) --
        # объяснение важнее generic-сообщения про "не понял, какой диалог"
        # ниже, и действие НЕ выполняется (спека §3: промах = катастрофа).
        return target_error
    if contact_id is None:
        # «Хотел притормозить один диалог, а заглушил всю воронку» -- слишком
        # дорогая опечатка. Глобальное глушение называется /stop намеренно
        # другим словом, поэтому targeted-команда без адресата НЕ падает
        # обратно на глобальное действие -- она объясняется (спека §7).
        return ("Не понял, какой диалог. Ответьте этой командой реплаем на карточку "
                f"или укажите адресата: /{cmd.name} <номер|ссылка|id>. "
                "Заглушить ВСЕ диалоги -- это /stop.")

    if cmd.name == "pause":
        until = now + cmd.duration_seconds if cmd.duration_seconds else None
        # ОБЯЗАТЕЛЬНО до mute(): Store.mute() кидает KeyError на неизвестном
        # contact_id (db.py) -- это верно и полезно как защита от порчи, НО
        # владелец имеет право упредить Аню и заглушить диалог, которого она
        # ещё не касалась (например через /pause <ссылка> на лида, которому
        # только собирается написать). get_or_create_contact создаёт строку,
        # если её нет, и no-op, если есть -- поэтому этот вызов безопасен и
        # для уже управляемых контактов.
        store.get_or_create_contact(contact_id)
        store.mute(contact_id, source="command", until=until, now=now)
        when = f" на {int(cmd.duration_seconds // 60)} мин" if cmd.duration_seconds else " бессрочно"
        return f"⏸ Диалог заглушён{when}. Вернуть: /resume реплаем."
    if cmd.name == "resume":
        store.unmute(contact_id)
        store.add_event("resume", contact_id=contact_id, ts=now)
        return "▶️ Аня снова отвечает в этом диалоге."
    return f"неизвестная команда: {cmd.name}"


# --- 2. per-persona bundle ---------------------------------------------------
@dataclass
class PersonaBundle:
    cfg: Config
    deps: Deps


def _switch_ack(cfg: Config) -> str:
    """A fixed short control line, NOT via the LLM (spec S7)."""
    if cfg.settings.language == "ru":
        return f"— теперь отвечает {cfg.settings.persona_name} ({cfg.settings.language}) —"
    return f"— now answering: {cfg.settings.persona_name} ({cfg.settings.language}) —"


# --- 3. per-chat debounce/coalescing ----------------------------------------
class ChatDebouncer:
    """Per-chat inbound accumulation. Buffers text, extends the window on
    every new message, fires once `humanizer.debounce_ready` says so (quiet
    gap elapsed OR hard ceiling reached), then dispatches
    `humanizer.coalesce(buffer)`-equivalent (the raw list; `on_ready` decides
    how to join) via `on_ready`.

    The DECISION is the pure, already-tested `debounce_ready` from
    core.humanizer -- reused verbatim, not reimplemented (spec S6). This
    class only supplies the async accumulation loop around it, which spec S6
    explicitly allows to live on the transport/runner side.

    `clock` and `async_sleep` are injected (default to real time/asyncio.sleep)
    so tests can drive this deterministically without waiting real
    wall-clock seconds -- see tests/chatter/test_telethon_run.py's
    ManualSleeper.
    """

    def __init__(
        self, *, window: float, max_window: float,
        on_ready: Callable[[list[str]], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
        async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._window = window
        self._max_window = max_window
        self._clock = clock
        self._async_sleep = async_sleep
        self._on_ready = on_ready
        self._buffer: list[str] = []
        self._first_at = 0.0
        self._last_at = 0.0
        self.task: "asyncio.Task | None" = None

    def add(self, text: str) -> None:
        now = self._clock()
        if not self._buffer:
            self._first_at = now
        self._buffer.append(text)
        self._last_at = now
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        while True:
            now = self._clock()
            if H.debounce_ready(
                first_received_at=self._first_at, last_received_at=self._last_at,
                now=now, window=self._window, max_window=self._max_window,
            ):
                break
            remaining_quiet = self._window - (now - self._last_at)
            remaining_ceiling = self._max_window - (now - self._first_at)
            wait = max(0.0, min(remaining_quiet, remaining_ceiling))
            await self._async_sleep(wait)
        batch, self._buffer = self._buffer, []
        await self._on_ready(batch)


# --- 4/5/7. runner: never-writes-first, allowlist, /switch -----------------
class TelethonRunner:
    """Owns the loaded personas, the allowlist, the in-memory
    {sender_id: persona} switch map, and one ChatDebouncer per chat.

    `handle_event` is the ONLY path that ever constructs a TelethonTransport
    and calls .send/.send_typing -- there is no other code path to
    `client.send_message`/`client.action` anywhere in this module, which is
    what makes "never writes first" (spec S4) structurally true: with zero
    incoming events, handle_event is never called, so send is never called.
    """

    def __init__(
        self, *, client, personas: dict[str, PersonaBundle], primary_slug: str,
        allowlist: frozenset[int], loop: asyncio.AbstractEventLoop,
        denylist: frozenset[int] = frozenset(), funnel_gate: bool = False,
        clients_dir: Path | None = None, persona_slugs: list[str] | None = None,
        llm_mode: str = "auto",
    ):
        if primary_slug not in personas:
            raise ValueError(f"primary persona '{primary_slug}' not among loaded personas")
        self.client = client
        self.personas = personas
        self.primary_slug = primary_slug
        # Для reload без рестарта (config-арка): откуда перечитывать конфиг.
        self._clients_dir = clients_dir
        self._persona_slugs = persona_slugs or list(personas.keys())
        self._llm_mode = llm_mode
        self.allowlist = allowlist
        # Арка 3C: перевёрнутый гейт допуска (по умолчанию off = старое поведение).
        self.denylist = denylist
        self.funnel_gate = funnel_gate
        self.loop = loop
        self._sender_persona: dict[int, str] = {}
        self._debouncers: dict[int, ChatDebouncer] = {}
        # Арка 3A: реестр СВОИХ исходящих (см. decide_outgoing) и id аккаунта
        # владельца. me_id стартует None и заполняется в main()'s
        # _on_connected ПОСЛЕ client.start() -- до этого он живой объект
        # клиента ещё не знает свой собственный id.
        self.sent_registry = SentRegistry()
        self.me_id: int | None = None
        # Арка 3B: Notifier (Saved Messages ЛИБО контрол-бот) + его поллер.
        # Ставятся build_runner'ом после конструктора (нужен client/loop/store).
        self.notifier: Notifier | None = None
        self.poller: ControlBotPoller | None = None
        # config-арка §2b: непусто, если стартовали на last-known-good из-за
        # битого текущего конфига — _on_connected об этом алертит владельцу.
        self._startup_recovery: str | None = None
        # config-арка §5: базовый mtime конфига для авто-перечитывания.
        self._config_mtime: float = 0.0
        self._auto_reload_error: str | None = None

    def persona_for(self, sender_id: int) -> str:
        return self._sender_persona.get(sender_id, self.primary_slug)

    def reload_configs(self) -> tuple[bool, str | None]:
        """Перечитать конфиг БЕЗ рестарта, АТОМАРНО и FAIL-SAFE (config-арка §2).

        Валидируем НОВЫЙ конфиг на scratch-сборке (load_personas → load_config
        кидает ConfigError на кривом файле) ДО того, как трогаем живое. Успех →
        атомарный своп personas + полей гейта, переиспользуя ТОТ ЖЕ Store (живое
        состояние — паузы/история/флаги — цело). Провал → НЕ свопаем, возвращаем
        (False, человеческая-причина: файл+причина). Аня продолжает на СТАРОМ
        конфиге — опечатка клиента не имеет права её заглушить (DEV-18)."""
        if self._clients_dir is None:
            return False, "reload недоступен: раннер собран без clients_dir"
        store = self.primary_store()   # ТОТ ЖЕ store — не пересоздаём живое состояние
        try:
            new_personas = load_personas(
                self._clients_dir, self._persona_slugs, store, llm_mode=self._llm_mode)
        except ConfigError as e:
            log.warning("reload: конфиг невалиден, остаюсь на старом: %s", e)
            return False, str(e)
        except Exception as e:  # noqa: BLE001 — любой сбой сборки = остаёмся на старом
            log.exception("reload: неожиданный сбой сборки конфига, остаюсь на старом")
            return False, f"{type(e).__name__}: {e}"
        # Переинъектим рантайм-зависимости (как build_runner).
        for bundle in new_personas.values():
            bundle.deps.notifier = self.notifier
            bundle.deps.escalation_card = self.build_escalation_card
        # Атомарный своп (одно присваивание ссылки dict).
        self.personas = new_personas
        tg = new_personas[self.primary_slug].cfg.settings.telegram
        if tg is not None:
            self.allowlist = frozenset(tg.allowlist)
            self.denylist = frozenset(tg.denylist)
            self.funnel_gate = tg.funnel_gate
        now = time.time()
        store.set_runtime_flag("config_changed_ts", str(now), ts=now)
        self._snapshot_configs(now)   # версия нового хорошего состояния (для /rollback + fail-safe)
        log.info("reload: конфиг перечитан и заменён успешно")
        return True, None

    def _config_mtime_now(self) -> float:
        """Максимальный mtime среди 4 конфиг-файлов всех персон."""
        if self._clients_dir is None:
            return 0.0
        mtimes = [
            (self._clients_dir / slug / f).stat().st_mtime
            for slug in self._persona_slugs for f in CONFIG_FILES
            if (self._clients_dir / slug / f).exists()
        ]
        return max(mtimes) if mtimes else 0.0

    def maybe_reload_on_change(self) -> bool:
        """config-арка §5: если файлы правились руками (mtime вырос) — перечитать
        (тот же fail-safe). Возвращает True, если изменение замечено (и попытка
        перечитывания сделана). Базовый mtime двигаем ВСЕГДА — даже при кривом
        файле, иначе битую правку дёргали бы каждый тик (шторм)."""
        m = self._config_mtime_now()
        if m <= self._config_mtime:
            return False
        self._config_mtime = m
        ok, err = self.reload_configs()
        if not ok:
            log.warning("auto-reload: конфиг битый, остаюсь на старом: %s", err)
            self._auto_reload_error = err
        return True

    def _snapshot_configs(self, now: float) -> None:
        """Снять версию каждого клиент-каталога (config-арка §4). Best-effort:
        сбой версионирования не должен ронять reload (DEV-18)."""
        if self._clients_dir is None:
            return
        for slug in self._persona_slugs:
            try:
                snapshot(self._clients_dir / slug, now=now)
            except Exception:
                log.warning("snapshot версии не удался для %s", slug, exc_info=True)

    async def handle_config_command(self, name: str, arg: str, *, language: str) -> str:
        """Диспетчер config-команд пульта (/config /reload /knowledge /rollback).
        Возвращает текст-ответ для пульта. Работает и из контрол-бота, и из
        Saved Messages (общий раннер-метод)."""
        if name == "config":
            return self._format_config(language)
        if name == "reload":
            ok, err = self.reload_configs()
            return cfg_text("cfg_reload_ok", language) if ok \
                else cfg_text("cfg_reload_fail", language, reason=err)
        if name == "knowledge":
            return self._show_knowledge(language) if not arg.strip() \
                else self._set_knowledge(arg, language)
        if name == "rollback":
            return self._rollback_config(language)
        if name == "funnel_gate":
            return self._set_funnel_gate(arg, language)
        return cfg_text("cfg_unknown", language)

    def _set_funnel_gate(self, arg: str, language: str) -> str:
        """Онбординг-дырка №2: переключатель гейта — команда, а не правка yaml.

        Включение требует ЯВНОГО подтверждения: на невыделенном аккаунте это
        означает, что Аня заговорит с реальными знакомыми владельца от его
        имени. Выключение — безопасное направление, исполняется сразу (чинить
        аварию надо быстро, а не через второй экран)."""
        tokens = arg.strip().casefold().split()
        action = tokens[0] if tokens else ""
        confirmed = len(tokens) > 1 and tokens[1] in ("confirm", "да", "yes", "так")

        if not action:
            return cfg_text(
                "cfg_gate_status_on" if self.funnel_gate else "cfg_gate_status_off", language)
        if action not in ("on", "off"):
            return cfg_text("cfg_gate_usage", language)
        if action == "on" and not confirmed:
            return cfg_text("cfg_gate_confirm", language)

        enabled = action == "on"
        path = self._primary_dir() / "settings.yaml"
        old = path.read_text(encoding="utf-8")
        try:
            path.write_text(set_funnel_gate(old, enabled), encoding="utf-8")
        except (YamlEditError, OSError) as e:
            log.warning("funnel_gate: правка settings.yaml не удалась", exc_info=True)
            return cfg_text("cfg_gate_fail", language, reason=str(e))

        ok, err = self.reload_configs()
        if not ok:
            path.write_text(old, encoding="utf-8")   # вернуть заведомо рабочий файл
            self.reload_configs()
            return cfg_text("cfg_gate_fail", language, reason=err)
        return cfg_text("cfg_gate_on_done", language) if enabled \
            else cfg_text("cfg_gate_off_done", language, allow=len(self.allowlist))

    def _primary_dir(self) -> Path:
        return self._clients_dir / self.primary_slug

    def _format_config(self, language: str) -> str:
        cfg = self.personas[self.primary_slug].cfg
        s = cfg.settings
        store = self.primary_store()
        beat = store.get_runtime_flag("config_changed_ts")
        changed_ago = _humanize_gap(time.time() - float(beat)) if beat else None
        return format_config(
            persona_name=s.persona_name, persona_age=s.persona_age, language=s.language,
            model=s.model, knowledge=cfg.knowledge, funnel_gate=self.funnel_gate,
            allow_count=len(self.allowlist), deny_count=len(self.denylist),
            changed_ago=changed_ago, lang=language,
            currency=s.currency, forbidden_count=len(s.forbidden_terms))

    def _show_knowledge(self, language: str) -> str:
        kb = self.personas[self.primary_slug].cfg.knowledge
        return cfg_text("cfg_kb_current", language, knowledge=safe_snippet(kb, limit=3500))

    def _set_knowledge(self, text: str, language: str) -> str:
        if not text.strip():
            return cfg_text("cfg_kb_empty", language)
        kb = self._primary_dir() / "knowledge.md"
        old = kb.read_text(encoding="utf-8") if kb.exists() else ""
        kb.write_text(text, encoding="utf-8")
        ok, err = self.reload_configs()
        if not ok:
            # knowledge.md — свободный markdown, валидатор проверяет лишь
            # непустоту; сюда попадём только на неожиданном сбое. Восстанавливаем.
            kb.write_text(old, encoding="utf-8")
            self.reload_configs()
            return cfg_text("cfg_reload_fail", language, reason=err)
        return cfg_text("cfg_kb_updated", language, n=len(text))

    def _rollback_config(self, language: str) -> str:
        client_dir = self._primary_dir()
        prev = previous_version(client_dir)
        if prev is None:
            return cfg_text("cfg_rollback_none", language)
        try:
            restore(client_dir, prev)
        except Exception as e:  # noqa: BLE001
            log.exception("rollback restore упал")
            return cfg_text("cfg_rollback_fail", language, reason=str(e))
        ok, err = self.reload_configs()
        if not ok:
            return cfg_text("cfg_rollback_fail", language, reason=err)
        return cfg_text("cfg_rollback_ok", language)

    def build_escalation_card(
        self, contact_id: str, summary: str, why: str, recent: list[tuple[str, str]],
    ) -> Card:
        """Билдер карточки эскалации с КЛИКАБЕЛЬНЫМ именем/ссылкой (§3). Вызывается
        из process_batch (worker-поток) — резолв entity маршалим на loop через
        run_coroutine_threadsafe (как транспорт). Инъектится в Deps.escalation_card."""
        peer = int(contact_id.split(":", 1)[0])
        settings = self._persona_settings(contact_id)
        language = settings.language
        try:
            entity = asyncio.run_coroutine_threadsafe(
                self.client.get_entity(peer), self.loop).result(timeout=15)
            name_html = html_link(
                display_name(
                    first_name=getattr(entity, "first_name", None),
                    last_name=getattr(entity, "last_name", None),
                    title=getattr(entity, "title", None),
                    username=getattr(entity, "username", None),
                    user_id=peer,
                ),
                contact_link(username=getattr(entity, "username", None), user_id=peer),
            )
            link = contact_link(username=getattr(entity, "username", None), user_id=peer)
        except Exception:
            log.warning("build_escalation_card: не смог разрешить peer %s", peer, exc_info=True)
            name_html = html_link(display_name(user_id=peer), contact_link(user_id=peer))
            link = contact_link(user_id=peer)
        text = format_escalation_card(
            name_html=name_html, link=link, summary=summary, reason=why,
            recent=recent, language=language, persona_name=settings.persona_name)
        return Card(
            kind="escalation", contact_id=contact_id, text_html=text,
            buttons=escalation_buttons(language),
            reply_hints=[
                console_text("card_resume_reply_hint", language),
                console_text("card_resume_status_hint", language),
            ],
            link=link)

    def primary_store(self) -> Store:
        """Единственный Store процесса. `load_personas` получает ОДИН `store`
        и раздаёт его во все `PersonaBundle.deps` (см. build_runner), так что
        это не «store первичной персоны» — это store, разделяемый всеми."""
        return self.personas[self.primary_slug].deps.store

    @property
    def control(self):
        return self.personas[self.primary_slug].cfg.settings.control

    def contact_id_for_chat(self, event) -> str | None:
        """Управляемый диалог = есть строка в contacts (Аня уже общалась) ИЛИ
        отправитель в allowlist. Иначе владелец, написавший с этого аккаунта
        кому угодно, наплодит паузы в чужих диалогах и утопит /status в
        мусоре (спека §3)."""
        peer_id = event.chat_id
        if peer_id in self.allowlist:
            return f"{peer_id}:{self.persona_for(peer_id)}"
        store = self.primary_store()
        for slug in self.personas:
            cid = f"{peer_id}:{slug}"
            if store.has_contact(cid):
                return cid
        return None

    async def on_human_takeover(self, event, contact_id: str) -> None:
        """Владелец перехватил диалог руками: заглушить, атрибутировать,
        уведомить (спека §3/§4).

        Telethon без `sequential_updates=True` (наш дефолт) диспетчеризует
        КАЖДОЕ исходящее `NewMessage` отдельной параллельной задачей -- если
        владелец быстро печатает лиду 3 сообщения подряд, это 3 параллельных
        `_outgoing_handler`, каждый ждёт свой грейс и был бы готов заново
        мутить/слать карточку/писать событие `takeover`, хотя это ОДИН
        эпизод. `Store.begin_takeover` захватывает эпизод атомарно (один
        UPDATE ... WHERE paused=0): только победитель шлёт карточку и
        событие, проигравшие лишь освежают атрибуцию на более позднее
        сообщение (`update_pause_attribution`, устойчиво к тому, что задачи
        завершаются не в порядке сообщений)."""
        text = (event.raw_text or "").strip()
        now = time.time()
        store = self.primary_store()
        store.get_or_create_contact(contact_id)
        # Всегда, независимо от исхода ниже: история и last_human_out_ts
        # (от него авто-возврат, спека §8, отсчитывает молчание владельца)
        # обязаны видеть КАЖДОЕ его ручное сообщение в этом диалоге, не
        # только первое, что открыло эпизод -- иначе после /resume у Ани
        # амнезия про часть переписки, а таймер авто-возврата думает, что
        # владелец молчит, пока он активно печатает.
        #
        # Ручное сообщение владельца -- в историю КАК assistant: brain.py:32
        # (build_messages) мапит роли истории НАПРЯМУЮ в поле role сообщений
        # Anthropic-API, а роли 'human' там нет -- отправка её сломала бы
        # вызов LLM. С точки зрения лида это и есть Аня (общий аккаунт), так
        # что роль 'assistant' и семантически верна.
        store.add_message(contact_id, "assistant", text, ts=now)
        store.note_human_out(contact_id, ts=now)

        started = store.begin_takeover(
            contact_id, msg_id=event.message.id, detail=text[:200], now=now)
        if not started:
            row = store.get_or_create_contact(contact_id)
            if row["pause_source"] == "human_takeover":
                # Продолжение уже идущего эпизода (типично: параллельный
                # залп сообщений владельца). Одна карточка на эпизод, но
                # атрибуция обязана указывать на самое СВЕЖЕЕ сообщение --
                # update_pause_attribution сравнивает msg_id, а не порядок
                # завершения asyncio-задач.
                store.update_pause_attribution(
                    contact_id, msg_id=event.message.id, detail=text[:200])
                log.info("TAKEOVER %s: продолжение эпизода (msg %s), карточку не шлю",
                         contact_id, event.message.id)
            else:
                # Диалог уже заглушён ДРУГОЙ причиной (например /pause из
                # консоли). История и last_human_out_ts выше уже это
                # отразили; менять существующую атрибуцию/причину и слать
                # вторую карточку не нужно -- /status и так покажет диалог
                # заглушённым.
                log.info("TAKEOVER %s: уже заглушён источником %r, карточку не шлю",
                         contact_id, row["pause_source"])
            return

        log.info("TAKEOVER %s by owner: msg %s %r (новый эпизод)",
                 contact_id, event.message.id, text[:60])
        store.add_event("takeover", contact_id=contact_id, detail=str(event.message.id), ts=now)
        try:
            await self.post_pause_card(event, contact_id, text)
        except Exception:
            # Мут (выше) УЖЕ встал -- отказ карточки не оставляет Аню
            # отвечающей поверх владельца, отказ безопасный. Но без карточки
            # владельцу нечем адресовать /resume реплаем -- только
            # /status + /resume <id> руками, и он об этом даже не узнает,
            # если промолчать. DEV-18: разница между "залогировано в файл" и
            # "владелец узнал" -- ровно то, на чём эта арка стоит.
            #
            # НЕ через send_alert() из telethon_tg.py: та функция
            # рассчитана на вызов С ДРУГОГО потока (worker-поток через
            # asyncio.to_thread, либо main() ПОСЛЕ того как
            # run_until_disconnected() уже вернул управление и loop не
            # крутится) -- она блокирующе ждёт `fut.result(timeout=30)`
            # результата корутины, запланированной НА ТОТ ЖЕ loop через
            # run_coroutine_threadsafe. on_human_takeover уже выполняется
            # КАК КОРУТИНА НА ЭТОМ САМОМ loop (обработчик Telethon-события),
            # так что send_alert() отсюда заблокировала бы поток loop'а,
            # ожидая корутину, которую сам же не даёт выполнить -- то есть
            # loop встал бы целиком на 30с. Проверено отдельным скриптом
            # (run_coroutine_threadsafe + fut.result() с того же loop
            # гарантированно таймаутит). Здесь мы уже на loop, поэтому
            # обычный await -- корректный и небllocking способ.
            log.exception("post_pause_card FAILED for %s (msg %s)", contact_id, event.message.id)
            # Даже в этом отказном пути -- НИ ОДНОГО голого id, если можно
            # назвать человека по имени (спека §1): карточка не ушла, но
            # это не повод откатиться к "⏸ Пауза: 237616472", ровно к тому,
            # что провалило живой дрил. display_name сама падает на id
            # ТОЛЬКО если о человеке правда ничего не известно -- отдельный
            # try тут просто на случай, если event.chat вообще недоступен
            # (сеть уже один раз подвела в этом блоке, паранойя оправдана).
            try:
                name = display_name(
                    first_name=getattr(event.chat, "first_name", None),
                    last_name=getattr(event.chat, "last_name", None),
                    title=getattr(event.chat, "title", None),
                    username=getattr(event.chat, "username", None),
                    user_id=event.chat_id,
                )
            except Exception:
                name = str(contact_id)
            try:
                await self.client.send_message(
                    "me",
                    f"⚠️ Диалог {name} заглушён (вы вмешались), но карточка в "
                    "Saved Messages не отправилась -- реплаем адресовать нечем. "
                    "Снять паузу: /status покажет диалог, затем /resume <номер|@user|ссылка>.",
                    parse_mode="html",
                )
            except Exception:
                log.exception(
                    "alert about a failed pause card ALSO failed to send for %s", contact_id)

    def _persona_settings(self, contact_id: str):
        """cfg.settings нужного диалога, по slug из хвоста contact_id
        ("<peer_id>:<slug>") -- у каждой персоны свой `language`/
        `persona_name`, карточка обязана говорить на языке ЕЁ владельца, не
        всегда primary. Неизвестный/битый slug -- фолбэк на primary, чтобы
        карточка всё равно ушла (лучше не на том языке, чем никак)."""
        slug = contact_id.rsplit(":", 1)[-1]
        return self.personas.get(slug, self.personas[self.primary_slug]).cfg.settings

    async def _notify_owner_notice(self, text_html: str) -> None:
        """Короткое информационное сообщение владельцу через Notifier (без
        кнопок). С loop → sync-notifier оборачиваем в to_thread."""
        if self.notifier is None:
            return
        card = Card(kind="notice", contact_id="", text_html=text_html, buttons=[], reply_hints=[])
        try:
            await asyncio.to_thread(self.notifier.notify, card)
        except Exception:
            log.exception("не смог уведомить пульт (inline-cmd notice)")

    async def _notify_known_contact(self, event, sender_id: int) -> None:
        """Арка 3C: знакомый (User.contact) написал — Аня ему НЕ отвечает,
        а владелец получает уведомление в пульт. Дебаунс: один знакомый = одно
        уведомление за окно (status_window_hours), иначе болтливый контакт
        засыпал бы пульт."""
        store = self.primary_store()
        settings = self.personas[self.primary_slug].cfg.settings
        language = settings.language
        now = time.time()
        key = f"known_contact_notified:{sender_id}"
        last = store.get_runtime_flag(key)
        window = self.control.status_window_hours * 3600.0
        if last and (now - float(last)) < window:
            log.info("known contact %s написал снова — уведомление уже слал (дебаунс)", sender_id)
            return
        store.set_runtime_flag(key, str(now), ts=now)
        name = display_name(
            first_name=getattr(event.sender, "first_name", None),
            last_name=getattr(event.sender, "last_name", None),
            username=getattr(event.sender, "username", None),
            user_id=sender_id)
        text = console_text(
            "known_contact_notice", language, name=name,
            snippet=safe_snippet(event.raw_text or "", limit=120), id=sender_id)
        await self._notify_owner_notice(text)

    async def handle_inline_command(self, event, contact_id: str, cmd) -> None:
        """Fix 3: владелец набрал команду (/resume и т.п.) ПРЯМО в диалоге лида,
        а не в пульте. Люди так делают — это естественно. Обрабатываем ДО
        детекции перехвата: выполняем команду, НЕ пишем её в историю (модель не
        должна видеть «/resume»), удаляем сообщение (лид уже увидел пуш, но экран
        чище) и подсказываем в пульт, что команды лучше набирать там."""
        store = self.primary_store()
        language = self._persona_settings(contact_id).language
        status_text = await self.render_status() if cmd.name == "status" else None
        result = execute_command(
            cmd, store=store, contact_id=contact_id, now=time.time(),
            status_text=status_text, language=language)
        log.info("INLINE-CMD /%s в диалоге %s -> %s", cmd.name, contact_id, result[:60])
        try:
            await self.client.delete_messages(event.chat_id, [event.message.id])
        except Exception:
            log.warning("inline-cmd: не смог удалить команду из диалога лида", exc_info=True)
        notice = console_text("inline_cmd_notice", language, cmd=f"/{cmd.name}")
        await self._notify_owner_notice(f"{notice}\n\n{result}")

    async def post_pause_card(self, event, contact_id: str, text: str) -> None:
        """Кладёт в Saved Messages карточку паузы и запоминает её id для
        адресации `/resume` реплаем (спека §5/§7; полная карточка эскалации --
        арка 3B).

        Имя -- кликабельная HTML-ссылка (спека §1/§5), не голый id: это
        ровно то, что провалило живой дрил ("⏸ Пауза: 237616472"). Две
        строки подсказки внизу (реплай ИЛИ /status→/resume <номер>) --
        находка ТОГО ЖЕ дрила: владелец не понял, как вернуть Аню, имея
        только одну."""
        settings = self._persona_settings(contact_id)
        language = settings.language
        name_html = html_link(
            display_name(
                first_name=getattr(event.chat, "first_name", None),
                last_name=getattr(event.chat, "last_name", None),
                title=getattr(event.chat, "title", None),
                username=getattr(event.chat, "username", None),
                user_id=event.chat_id,
            ),
            contact_link(username=getattr(event.chat, "username", None), user_id=event.chat_id),
        )
        card_body = "\n".join([
            console_text("card_header", language, name=name_html),
            console_text("card_intervened_detail", language, detail=safe_snippet(text, limit=200)),
            console_text("card_silent", language, persona=settings.persona_name),
        ])
        hints = [
            console_text("card_resume_reply_hint", language),
            console_text("card_resume_status_hint", language),
        ]
        # Арка 3B: карточка идёт через Notifier — контрол-бот рисует инлайн-кнопки,
        # Saved Messages приклеивает hints (то же тело, что арка 3A). Notifier
        # синхронный → с loop оборачиваем в to_thread (иначе SavedMessages-
        # маршалинг run_coroutine_threadsafe заблокировал бы этот же loop).
        if self.notifier is None:
            # Прямой фоллбек (напр. раннер, собранный в обход build_runner в
            # тестах): сохраняем прежнее поведение арки 3A.
            card = await self.client.send_message(
                "me", card_body + "\n\n" + "\n".join(hints), parse_mode="html")
            self.primary_store().add_card(
                msg_id=card.id, contact_id=contact_id, kind="pause", ts=time.time())
            return
        card = Card(
            kind="pause", contact_id=contact_id, text_html=card_body,
            buttons=pause_buttons(language), reply_hints=hints,
            link=contact_link(username=getattr(event.chat, "username", None), user_id=event.chat_id))
        handle = await asyncio.to_thread(self.notifier.notify, card)
        if handle is None:
            # Доставка не удалась — пусть сработает аварийный алерт в
            # on_human_takeover (DEV-18: владелец обязан узнать, что карточки нет).
            raise RuntimeError("notifier failed to deliver the pause card")
        try:
            msg_id = int(handle.ref.split(":")[-1])
            self.primary_store().add_card(
                msg_id=msg_id, contact_id=contact_id, kind="pause", ts=time.time())
        except Exception:
            log.warning("post_pause_card: не смог записать handle %r", handle, exc_info=True)

    async def resolve_target(self, event, cmd) -> tuple[str | None, str | None]:
        """Какой диалог имел в виду владелец (спека §3/§7). Возвращает
        (contact_id, error) -- `error` непустой значит адресация провалилась
        ОСМЫСЛЕННО (например список устарел) и `execute_command` обязан
        показать именно его, а не тихо промахнуться.

        Порядок попыток (спека §4, по убыванию удобства):
        1. Реплай на карточку -- карточка уже лежит в Saved Messages.
        2. Номер из последнего /status -- ЧИСТАЯ проверка через
           `resolve_numbered_target` (только `store`, без сети): если
           цифровая строка найдена в `status_index`, это и есть номер, и
           дальше в этой ветке МЫ НЕ ТРОГАЕМ Telethon вообще -- нет смысла
           резолвить entity, адрес уже есть.
        3. Фоллбек -- `<id | t.me/user | @user>` через `client.get_entity`,
           для диалогов без свежей карточки и без под рукой /status."""
        if cmd.name not in ("pause", "resume"):
            return None, None
        reply_to = getattr(event, "reply_to_msg_id", None)
        if reply_to:
            hit = self.primary_store().card_contact(reply_to)
            if hit:
                return hit, None
        if not cmd.target:
            return None, None
        language = self.personas[self.primary_slug].cfg.settings.language
        numbered_id, numbered_error = resolve_numbered_target(
            self.primary_store(), cmd.target, language=language)
        if numbered_id is not None or numbered_error is not None:
            return numbered_id, numbered_error
        # Фоллбек: /resume <id | t.me/user | @user> -- для диалогов без
        # свежей карточки в Saved Messages и без известного номера.
        raw = cmd.target.strip().rstrip("/").split("/")[-1].lstrip("@")
        try:
            entity = await self.client.get_entity(int(raw) if raw.isdigit() else raw)
        except Exception:
            # DEV-18: не молчать -- владелец получит от execute_command
            # "не понял, какой диалог" вместо тишины, но ПОЧЕМУ не разрешилось
            # видно только в логе.
            log.warning("resolve_target: не смог разрешить %r", cmd.target, exc_info=True)
            return None, None
        return f"{entity.id}:{self.persona_for(entity.id)}", None

    async def render_status(self) -> str:
        """Собрать PauseView-ы (единственное место, где для /status нужен
        живой Telethon -- имя и username) и отдать чистому форматтеру
        core.console.format_status. Корутина из-за client.get_entity ниже --
        вызывающая сторона (_console_handler) обязана её await'ить.

        `rows` перечисляется ОДИН раз через `enumerate(rows, start=1)` и
        РОВНО этот порядок уходит и в `store.issue_status_index(...)`, и в
        `PauseView.n` каждого элемента -- это и есть гарантия того, что
        напечатанный номер == номер, под которым `/resume N` найдёт
        контакт (спека §3: "печатаются в ТОМ ЖЕ порядке... это критично").
        Если бы номера выдавались по одному проходу, а печатались по
        другому (например после промежуточной пересортировки), "1" в тексте
        мог бы означать не того, кому владелец в итоге присвоит /resume 1."""
        store = self.primary_store()
        now = time.time()
        window = self.control.status_window_hours * 3600.0
        language = self.personas[self.primary_slug].cfg.settings.language
        rows = store.muted_contacts()
        store.issue_status_index([row["contact_id"] for row in rows], now=now)
        views: list[PauseView] = []
        for n, row in enumerate(rows, start=1):
            peer_id = int(row["contact_id"].split(":")[0])
            try:
                entity = await self.client.get_entity(peer_id)
                name = display_name(
                    first_name=getattr(entity, "first_name", None),
                    last_name=getattr(entity, "last_name", None),
                    title=getattr(entity, "title", None),
                    username=getattr(entity, "username", None),
                    user_id=peer_id,
                )
                link = contact_link(username=getattr(entity, "username", None), user_id=peer_id)
            except Exception:
                # Разрешение имени -- УДОБСТВО отображения, не критично для
                # смысла /status (пауза всё равно покажется, просто по id).
                # DEV-18: тем не менее логируем, не глотаем молча -- иначе
                # растущее число нерешённых entity останется незамеченным.
                log.warning("render_status: не смог разрешить peer %s", peer_id, exc_info=True)
                name = display_name(user_id=peer_id)
                link = contact_link(user_id=peer_id)
            eta = None
            if row["pause_until"] is not None:
                eta = float(row["pause_until"])
            elif row["pause_source"] == "human_takeover":
                last = row["last_human_out_ts"] or row["paused_at"] or now
                eta = float(last) + self.control.auto_resume_hours * 3600.0
            views.append(PauseView(
                n=n, title=name, link=link,
                since_ts=float(row["paused_at"] or now),
                source=row["pause_source"] or "?",
                detail=row["pause_detail"],
                msg_id=row["pause_msg_id"],
                resume_eta_ts=eta,
            ))
        beat_raw = store.get_runtime_flag("autoresume_beat")
        beat_age = (now - float(beat_raw)) if beat_raw else None
        return format_status(
            kill_switch=store.get_runtime_flag("kill_switch") == "1",
            pauses=views,
            counters={k: store.count_events(k, since_ts=now - window)
                      for k in ("takeover", "unattributed_pause", "unknown_outgoing")},
            autoresume_beat_age=beat_age,
            autoresume_interval=AUTORESUME_INTERVAL_SECONDS,
            now=now, window_hours=self.control.status_window_hours,
            language=language,
        )

    def toggle_persona(self, sender_id: int) -> str:
        """Flip demo<->demo2 for this sender. Requires exactly the two-persona
        case described in spec S7; with >2 personas loaded this picks the
        first other slug deterministically (dict insertion order)."""
        current = self.persona_for(sender_id)
        other = next(slug for slug in self.personas if slug != current)
        self._sender_persona[sender_id] = other
        return other

    async def handle_event(self, event) -> None:
        """Wired to events.NewMessage(incoming=True). `event` duck-types the
        Telethon NewMessage.Event attributes this reads: is_private, out,
        sender.bot, action, sender_id, raw_text, chat_id."""
        eligible = should_handle(
            is_private=bool(event.is_private),
            is_outgoing=bool(event.out),
            sender_is_bot=bool(getattr(event.sender, "bot", False)),
            is_service=getattr(event, "action", None) is not None,
        )
        if not eligible:
            log.debug("ignored non-eligible event from %s", getattr(event, "sender_id", "?"))
            return

        sender_id = event.sender_id
        # Арка 3C: перевёрнутый гейт. is_contact — знакомый ли аккаунта
        # (Telethon User.contact). funnel_gate off → старое поведение (allowlist).
        decision = admission_decision(
            sender_id=sender_id, is_contact=bool(getattr(event.sender, "contact", False)),
            allowlist=self.allowlist, denylist=self.denylist, funnel_gate=self.funnel_gate)
        if decision == "notify_owner":
            await self._notify_known_contact(event, sender_id)
            return
        if decision != "answer":
            log.info("admission: %s -> %s (не отвечаю)", sender_id, decision)
            return

        text = (event.raw_text or "").strip()
        chat_id = event.chat_id
        log.info("IN %s [%s]: %s", sender_id, self.persona_for(sender_id), text)

        # Send to the event's INPUT PEER (carries access_hash), not the bare
        # chat_id int: client.send_message/action(<int>) does get_input_entity,
        # which fails ("Could not find the input entity") on a fresh session
        # that hasn't cached that user. The event already has the resolvable peer.
        peer = await event.get_input_chat()

        if text == "/switch":
            new_slug = self.toggle_persona(sender_id)
            new_cfg = self.personas[new_slug].cfg
            transport = TelethonTransport(self.client, peer, self.loop, sent_registry=self.sent_registry)
            await asyncio.to_thread(transport.send, _switch_ack(new_cfg))
            return

        persona_slug = self.persona_for(sender_id)
        deb = self._debouncers.get(chat_id)
        if deb is None or deb.task is None or deb.task.done():
            deb = self._new_debouncer(peer=peer, sender_id=sender_id, persona_slug=persona_slug)
            self._debouncers[chat_id] = deb
        deb.add(text)

    def _new_debouncer(self, *, peer, sender_id: int, persona_slug: str) -> ChatDebouncer:
        bundle = self.personas[persona_slug]
        t = bundle.cfg.settings.timings

        async def _on_ready(batch: list[str]) -> None:
            contact_id = f"{sender_id}:{persona_slug}"
            transport = TelethonTransport(self.client, peer, self.loop, sent_registry=self.sent_registry)
            bundle.deps.store.get_or_create_contact(contact_id)  # process_batch assumes the row exists
            log.info("process START %s batch=%r", contact_id, batch)
            try:
                await asyncio.to_thread(process_batch, contact_id, batch, transport, bundle.deps)
                log.info("process END %s", contact_id)
            except Exception:
                # A fire-and-forget debouncer task swallows exceptions otherwise;
                # surface them loudly (this is what a silent no-reply looked like).
                log.exception("process_batch FAILED for %s", contact_id)

        return ChatDebouncer(window=t.debounce_window, max_window=t.debounce_max, on_ready=_on_ready)

    # --- catch-up on start --------------------------------------------------
    async def _collect_dialogs(self, *, per_dialog_scan: int = 50) -> list[dict]:
        """Snapshot private dialogs that have UNREAD messages into the
        transport-agnostic dicts `select_missed` consumes. The ONLY place that
        touches the live Telethon dialog/message iterators -- kept thin so the
        decision logic stays pure and unit-tested."""
        dialogs: list[dict] = []
        async for dialog in self.client.iter_dialogs():
            if not getattr(dialog, "is_user", False):
                continue
            if getattr(dialog, "unread_count", 0) <= 0:
                continue
            entity = dialog.entity
            sender_id = getattr(entity, "id", None)
            if sender_id is None:
                continue
            messages: list[dict] = []
            async for m in self.client.iter_messages(
                entity, limit=min(int(dialog.unread_count), per_dialog_scan),
            ):
                messages.append({
                    "text": getattr(m, "message", None) or "",
                    "out": bool(getattr(m, "out", False)),
                    "date_ts": m.date.timestamp(),
                })
            dialogs.append({
                "sender_id": sender_id,
                "is_user": True,
                "is_bot": bool(getattr(entity, "bot", False)),
                "is_contact": bool(getattr(entity, "contact", False)),  # арка 3C
                "messages": messages,
            })
        return dialogs

    async def catch_up_missed(
        self, *, now: float | None = None, max_age_seconds: float = CATCHUP_MAX_AGE_SECONDS,
        collect: Callable[[], Awaitable[list[dict]]] | None = None,
    ) -> None:
        """On start, answer messages that arrived while the runner was OFFLINE
        (spec liveness: a restart/crash must not silently drop leads). Scans
        unread private dialogs, selects allowlisted inbound within the age cap,
        and runs each through the SAME process_batch path as a live message --
        but with the message's age, so the "sorry for the pause" path fires."""
        now = time.time() if now is None else now
        collect = collect or self._collect_dialogs
        try:
            dialogs = await collect()
        except Exception:
            log.exception("catch-up: failed to collect dialogs; skipping catch-up")
            return
        missed = select_missed(
            dialogs, allowlist=self.allowlist, now=now, max_age_seconds=max_age_seconds,
            denylist=self.denylist, funnel_gate=self.funnel_gate)
        log.info("catch-up: %d dialog(s) with missed messages", len(missed))
        for mm in missed:
            try:
                await self._process_missed(mm)
            except Exception:
                log.exception("catch-up: FAILED for sender %s", mm.sender_id)

    async def _process_missed(self, mm: MissedMessage) -> None:
        persona_slug = self.persona_for(mm.sender_id)
        bundle = self.personas[persona_slug]
        contact_id = f"{mm.sender_id}:{persona_slug}"
        peer = await self.client.get_input_entity(mm.sender_id)
        transport = TelethonTransport(self.client, peer, self.loop, sent_registry=self.sent_registry)
        bundle.deps.store.get_or_create_contact(contact_id)
        log.info("catch-up process START %s texts=%r age=%.0fs",
                 contact_id, mm.texts, mm.oldest_age_seconds)
        await asyncio.to_thread(
            process_batch, contact_id, mm.texts, transport, bundle.deps,
            missed_age_seconds=mm.oldest_age_seconds,
        )
        log.info("catch-up process END %s", contact_id)


async def config_watch_loop(
    runner: "TelethonRunner", *, interval: float = CONFIG_WATCH_INTERVAL_SECONDS,
    async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """config-арка §5: вечный фон — при auto_reload перечитывает конфиг, когда
    файлы правились руками (mtime вырос). Тот же fail-safe (кривой файл →
    остаёмся на старом), а владельца уведомляем через пульт. DEV-18: сбой не
    убивает цикл. Само перечитывание — в to_thread, чтобы не блокировать loop."""
    while True:
        try:
            changed = await asyncio.to_thread(runner.maybe_reload_on_change)
            if changed and runner._auto_reload_error:
                err, runner._auto_reload_error = runner._auto_reload_error, None
                lang = runner.personas[runner.primary_slug].cfg.settings.language
                await runner._notify_owner_notice(cfg_text("cfg_reload_fail", lang, reason=err))
        except Exception:
            log.exception("config-watch: сбой, продолжаю цикл")
        await async_sleep(interval)


async def heartbeat_loop(
    *, interval: float = HEARTBEAT_INTERVAL_SECONDS, path: Path = HEARTBEAT_PATH,
) -> None:
    """Rewrite the runner's liveness stamp every `interval` seconds forever, so
    the guardian can tell a live runner from a hung one. Never raises out."""
    while True:
        write_heartbeat(path)
        await asyncio.sleep(interval)


# --- periodic auto-resume + its OWN heartbeat (arc 3A, spec §8) -------------
def autoresume_sweep(store: Store, *, now: float, auto_resume_hours: float) -> int:
    """Один прогон авто-возврата. Возвращает число размороженных диалогов.

    Пишет свой heartbeat (`runtime_flags['autoresume_beat']`) ВСЕГДА -- по
    его возрасту /status отличает живую задачу от мёртвой. Мёртвая задача
    выглядит РОВНО как «пауз к возврату нет»: тихо и правдоподобно.
    Единственная разница -- возраст этого heartbeat.

    ФАКТ-ПРОВЕРКА против плана: план писал heartbeat ОДНОЙ строкой ПОСЛЕ
    цикла `for row in store.muted_contacts(): ... store.unmute(...)`. Если
    `unmute`/`add_event` бросает исключение на КАКОЙ-ТО одной строке (сбой
    БД, гонка с /resume того же контакта из консоли), исключение уносит
    выполнение мимо строки с heartbeat -- он не пишется, и /status начинает
    ВРАТЬ «задача жива», хотя она застряла на первой же сломанной строке.
    Это ровно тот баг-класс, от которого вся идея heartbeat: обёрнуто в
    try/except на уровне КАЖДОЙ строки (DEV-18: логируем, не глотаем), чтобы
    одна порченая строка не блокировала ни heartbeat, ни размораживание
    ОСТАЛЬНЫХ диалогов после неё. Чтение store.muted_contacts() тоже
    обёрнуто отдельно по той же причине."""
    resumed = 0
    try:
        rows = store.muted_contacts()
    except Exception:
        log.exception("autoresume sweep: не смог прочитать список пауз")
        rows = []
    for row in rows:
        try:
            if should_auto_resume(row, now=now, auto_resume_hours=auto_resume_hours):
                store.unmute(row["contact_id"])
                store.add_event("auto_resume", contact_id=row["contact_id"], ts=now)
                resumed += 1
        except Exception:
            log.exception("autoresume sweep: не смог разморозить %s", row.get("contact_id"))
    store.set_runtime_flag("autoresume_beat", str(now), ts=now)
    return resumed


async def autoresume_loop(
    store: Store, *, auto_resume_hours: float, interval: float = AUTORESUME_INTERVAL_SECONDS,
) -> None:
    """Крутит autoresume_sweep вечно, раз в `interval` секунд. DEV-18:
    исключение внутри НЕ убивает цикл -- иначе паузы залипнут навсегда, а
    владелец узнает об этом только по возрасту heartbeat в /status (и то
    только если додумается посмотреть). autoresume_sweep уже ловит свои
    внутренние сбои построчно (см. выше) -- этот try/except здесь как вторая
    линия обороны на случай сбоя ВНЕ цикла по строкам (например
    store.muted_contacts() и store.set_runtime_flag() оба упали до того, как
    внутренние try/except успели сработать)."""
    while True:
        try:
            autoresume_sweep(store, now=time.time(), auto_resume_hours=auto_resume_hours)
        except Exception:
            log.exception("autoresume sweep failed; продолжаю цикл")
        await asyncio.sleep(interval)


# --- runner assembly / CLI --------------------------------------------------
def _build_llm(cfg: Config, mode: str) -> LLMClient:
    if mode == "real" or (mode == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicLLM(cfg.settings.model)
    return FakeLLM()


def _bind_classifier(llm: LLMClient, cfg: Config):
    """Замыкание дешёвого классификатора эскалации на LLM/плейбук персоны."""
    def _run(history: list[dict]) -> ClassifierResult:
        return _classify(
            llm, playbook=cfg.playbook, language=cfg.settings.language, history=history)
    return _run


def load_personas(
    clients_dir: Path, slugs: list[str], store: Store, *, llm_mode: str = "auto",
) -> dict[str, PersonaBundle]:
    personas: dict[str, PersonaBundle] = {}
    for slug in slugs:
        cfg = load_config(clients_dir, slug)
        llm = _build_llm(cfg, llm_mode)
        deps = Deps(
            cfg=cfg, store=store, brain=Brain(llm, cfg),
            rng=random.Random(), clock=time.time, sleep=time.sleep,
            escalation_keywords=parse_escalation_keywords(cfg.playbook),
            control=cfg.settings.control,
        )
        # Классификатор — только на РЕАЛЬНОМ LLM: в fake-режиме он делил бы
        # scripted-очередь с Brain и деградировал бы на каждом ходу (шум +
        # ложный алерт). Детерминированный слой эскалации работает всегда.
        if cfg.settings.control.classifier_enabled and isinstance(llm, AnthropicLLM):
            deps.classify = _bind_classifier(llm, cfg)
        personas[slug] = PersonaBundle(cfg=cfg, deps=deps)
    return personas


def _resolve_control_token(token_env: str | None) -> str | None:
    """Токен контрол-бота по ИМЕНИ env-переменной: сначала os.environ, потом
    .env (как ANTHROPIC_API_KEY) — гардиан-раннер может не унаследовать
    shell-переменную, а .env читается всегда."""
    if not token_env:
        return None
    token = os.environ.get(token_env)
    if token:
        return token
    try:
        return _parse_env_file(DEFAULT_ENV_FILE).get(token_env)
    except Exception:
        log.warning("не смог прочитать %s из .env для токена контрол-бота", DEFAULT_ENV_FILE, exc_info=True)
        return None


def _build_notifier_and_poller(
    *, client, loop, store: Store, control: ControlConfig, language: str, config_handler=None,
) -> tuple[Notifier, "ControlBotPoller | None"]:
    """Контрол-бот, если его токен есть в окружении (по ИМЕНИ из settings.yaml);
    иначе — Saved Messages (инвариант арки: без токена = поведение 3A).

    owner_chat_id может быть None (bind на первый /start): и notifier, и поллер
    читают привязанного владельца из runtime_flags — поэтому notifier получает
    РАЗРЕШАТЕЛЬ chat_id, а не фиксированное число."""
    token = _resolve_control_token(control.control_bot_token_env)
    if not token:
        return SavedMessagesNotifier(client, loop), None

    def _owner() -> int | None:
        if control.owner_chat_id is not None:
            return control.owner_chat_id
        flag = store.get_runtime_flag("control_owner_chat_id")
        return int(flag) if flag else None

    notifier = ControlBotNotifier(token, _owner)
    poller = ControlBotPoller(
        token, store=store, language=language, snooze_seconds=control.snooze_seconds,
        owner_chat_id=control.owner_chat_id, pairing_code=control.pairing_code,
        config_handler=config_handler)
    return notifier, poller


def _load_personas_failsafe(
    clients_dir: Path, slugs: list[str], store: Store, llm_mode: str,
) -> tuple[dict[str, PersonaBundle], str | None]:
    """Загрузка персон со СТАРТОВЫМ fail-safe (config-арка §2b): если текущий
    конфиг битый, восстанавливаем last-known-good из `.versions` и грузим его.
    Так `git checkout`/опечатка клиента + рестарт больше не = crash-loop через
    гардиан. Возвращает (personas, recovery_reason|None). Hard-fail ТОЛЬКО если
    снимков нет вовсе (самый первый запуск с битым конфигом)."""
    try:
        return load_personas(clients_dir, slugs, store, llm_mode=llm_mode), None
    except ConfigError as e:
        log.error("startup: конфиг битый, пробую last-known-good: %s", e)
        restored = False
        for slug in slugs:
            v = latest_version(clients_dir / slug)
            if v is not None:
                restore(clients_dir / slug, v)
                restored = True
        if not restored:
            raise ConfigError(
                f"config broken at startup and NO last-known-good snapshot to recover from: {e}"
            ) from e
        try:
            return load_personas(clients_dir, slugs, store, llm_mode=llm_mode), str(e)
        except ConfigError as e2:
            raise ConfigError(
                f"config broken at startup and even the last-known-good snapshot failed: {e2}"
            ) from e2


def build_runner(
    *, client, clients_dir: Path, persona_slugs: list[str], store: Store,
    loop: asyncio.AbstractEventLoop, llm_mode: str = "auto",
) -> TelethonRunner:
    """Loads personas, resolves the allowlist from the PRIMARY (first) persona
    (spec S5), and wires client.add_event_handler(..., events.NewMessage(
    incoming=True)). Kept separate from main() so tests can pass a mocked
    `client` and never touch the network."""
    if not persona_slugs:
        raise ValueError("need at least one persona")
    personas, startup_recovery = _load_personas_failsafe(
        Path(clients_dir), persona_slugs, store, llm_mode)
    primary_slug = persona_slugs[0]
    telegram_cfg = personas[primary_slug].cfg.settings.telegram
    if telegram_cfg is None:
        raise ValueError(
            f"persona '{primary_slug}' has no [telegram] block in settings.yaml "
            "(the allowlist is required to run the Telethon transport)"
        )
    runner = TelethonRunner(
        client=client, personas=personas, primary_slug=primary_slug,
        allowlist=frozenset(telegram_cfg.allowlist), loop=loop,
        denylist=frozenset(telegram_cfg.denylist), funnel_gate=telegram_cfg.funnel_gate,
        clients_dir=Path(clients_dir), persona_slugs=list(persona_slugs), llm_mode=llm_mode,
    )

    # Арка 3B: Notifier + (для контрол-бота) поллер. Инъектим Notifier и билдер
    # карточки в Deps КАЖДОЙ персоны. Токен берём по ИМЕНИ env-переменной из
    # settings.yaml — само значение в git не попадает.
    control = personas[primary_slug].cfg.settings.control
    primary_language = personas[primary_slug].cfg.settings.language
    runner.notifier, runner.poller = _build_notifier_and_poller(
        client=client, loop=loop, store=store, control=control, language=primary_language,
        config_handler=runner.handle_config_command)
    for bundle in personas.values():
        bundle.deps.notifier = runner.notifier
        bundle.deps.escalation_card = runner.build_escalation_card
    # Базовый снимок конфига (config-арка §4): даёт /rollback точку возврата и
    # стартовому fail-safe последний-хороший на будущее.
    runner._snapshot_configs(time.time())
    runner._startup_recovery = startup_recovery
    runner._config_mtime = runner._config_mtime_now()   # базовый mtime (config-арка §5)

    async def _handler(event) -> None:
        await runner.handle_event(event)

    client.add_event_handler(_handler, events.NewMessage(incoming=True))

    async def _outgoing_handler(event) -> None:
        if runner.me_id is None:
            # Пока не знаем СВОЙ id, мы не можем отличить пульт (Saved
            # Messages) от диалога лида. Судить о перехвате вслепую нельзя:
            # цена ошибки -- ложная пауза или прочитанный как перехват
            # /stop. Раньше это держалось на стечении конфигурации (id
            # аккаунта Ани случайно не совпадает ни с записью в allowlist,
            # ни со строкой в contacts) -- ЯВНЫЙ гейт делает это безопасным
            # по конструкции, а не по счастливой случайности данных. Окно
            # длится доли секунды между регистрацией хендлеров
            # (build_runner) и get_me() (_on_connected), и пропущенное в
            # нём исходящее -- в худшем случае незамеченный перехват за
            # первые мгновения жизни раннера, что несравнимо дешевле
            # самозаглушки.
            log.warning("outgoing event before me_id is known — пропускаю, не сужу")
            return
        # Saved Messages -- это пульт, а не диалог лида: /stop не должен
        # читаться как «владелец перехватил чат с самим собой» (спека §3).
        if event.chat_id == runner.me_id:
            return
        contact_id = runner.contact_id_for_chat(event)
        if contact_id is None:
            return   # диалог, который Аня не ведёт: это просто жизнь аккаунта
        # Fix 3: владелец набрал команду ПРЯМО в диалоге лида (естественное
        # движение). Ловим ДО детекции перехвата: Аня сама «/resume» не шлёт
        # (её id был бы в реестре), поэтому не-наше исходящее, парсящееся как
        # команда, — это команда владельца. Выполнить, не писать в историю,
        # удалить, подсказать пульту. НЕ трактуем как перехват.
        cmd = parse_command(event.raw_text or "")
        if cmd is not None and not runner.sent_registry.is_ours(event.message.id):
            await runner.handle_inline_command(event, contact_id, cmd)
            return
        # Снимок ДО decide_outgoing: сам decide_outgoing может дождаться
        # грейс-окна и застать id уже пополнившим реестр -- тогда признак
        # "реестр опоздал" потеряется. was_known фиксирует состояние на
        # момент прихода апдейта, а не на момент, когда мы закончили решать.
        was_known = runner.sent_registry.is_ours(event.message.id)
        verdict = await decide_outgoing(
            event.message.id, registry=runner.sent_registry,
            grace_seconds=runner.control.takeover_grace_seconds)
        if verdict == "ours":
            if not was_known:
                # КАНАРЕЙКА (спека §4). Своё сообщение, которого не было в
                # реестре при первом взгляде: гонка worker-поток/loop реально
                # проигралась, и от самозаглушки нас спас только грейс.
                # Ноль здесь = гонка не проявляется. Рост = грейс из
                # страховки стал единственной защитой -- разбираться надо
                # сейчас, а не когда Аня замолчит.
                runner.primary_store().add_event(
                    "unknown_outgoing", contact_id=contact_id,
                    detail=str(event.message.id), ts=time.time())
                log.warning("реестр опоздал: id %s опознан только после грейса", event.message.id)
            return
        await runner.on_human_takeover(event, contact_id)

    client.add_event_handler(_outgoing_handler, events.NewMessage(outgoing=True))

    async def _console_handler(event) -> None:
        # Пульт живёт ТОЛЬКО в Saved Messages (спека §7): второй getUpdates
        # на боевом токене недопустим, поэтому команд через бота нет, а без
        # известного me_id (короткое окно на самом старте, см.
        # _outgoing_handler выше) отличить пульт от обычного диалога нельзя
        # -- безопаснее промолчать эти доли секунды, чем сработать вслепую.
        if runner.me_id is None or event.chat_id != runner.me_id:
            return
        # config-арка: config-команды и в Saved Messages (фоллбек-пульт).
        cc = parse_config_command(event.raw_text or "")
        if cc is not None:
            language = runner.personas[runner.primary_slug].cfg.settings.language
            reply = await runner.handle_config_command(cc[0], cc[1], language=language)
            await client.send_message("me", reply, parse_mode="html")
            return
        cmd = parse_command(event.raw_text or "")
        if cmd is None:
            return   # обычная заметка в Saved Messages -- не команда, не трогаем
        contact_id, target_error = await runner.resolve_target(event, cmd)
        # render_status -- КОРУТИНА (внутри await client.get_entity для имён
        # диалогов): обязательно await, иначе status_text станет объектом
        # корутины вместо текста и execute_command() отправит его как есть.
        status_text = await runner.render_status() if cmd.name == "status" else None
        language = runner.personas[runner.primary_slug].cfg.settings.language
        reply = execute_command(cmd, store=runner.primary_store(), contact_id=contact_id,
                                 now=time.time(), status_text=status_text,
                                 target_error=target_error, language=language)
        # parse_mode="html": /status и карточка используют кликабельные
        # имена (<a href=...>), а всё подставленное туда пользовательское
        # содержимое (detail, имена профилей) уже прогнано через
        # escape_html/safe_snippet выше по цепочке (console.py) -- без
        # parse_mode="html" эти теги ушли бы как есть, видимым текстом.
        await client.send_message("me", reply, parse_mode="html")

    client.add_event_handler(_console_handler, events.NewMessage(chats="me"))
    return runner


def run_client(
    client, loop: asyncio.AbstractEventLoop,
    *, on_connected: Callable[[], Awaitable[None]] | None = None,
) -> int:
    """Runs `client.start()` + `client.run_until_disconnected()` -- the
    actual network-facing lifetime of the userbot -- with session-loss
    mapped to a graceful, alerted stop instead of a silent death or a bare
    traceback (spec S2/S8).

    Factored out of main() so a test can simulate `client.start()` raising a
    session-loss error using a plain mock, with NO real TelegramClient and NO
    network. `client` and `loop` are the same objects `build_runner` was
    given -- `loop` is reused (not the client's own) purely so `send_alert`
    can marshal the alert coroutine the same way TelethonTransport does.
    """
    try:
        client.start()
        # Scheduled now, executed once run_until_disconnected() drives the loop:
        # runs AFTER the session is connected (start() blocks until connected),
        # so catch-up's dialog scan and the heartbeat run against a live client.
        if on_connected is not None:
            loop.create_task(on_connected())
        client.run_until_disconnected()
        return 0
    except SESSION_LOST_ERRORS as e:
        text = (
            f"session lost / logged out ({type(e).__name__}: {e}) -- "
            "re-run `python -m chatter.telethon_login` to sign in again"
        )
        log.error(text)
        send_alert(client, loop, text)  # best-effort; send_alert never raises
        return 2


def main(argv: list[str] | None = None) -> int:
    # Same rationale as chatter/run.py's main(): Windows consoles default to a
    # legacy codepage that silently mangles Cyrillic instead of raising.
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")  # incl. stderr: logging writes there; keeps Cyrillic/emoji readable
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx логирует ПОЛНЫЙ URL запроса на INFO, а URL контрол-бота содержит
    # токен (…/bot<TOKEN>/getUpdates) — на INFO токен утекал бы в лог-файл
    # каждые ~25с. Поднимаем httpx до WARNING: токен больше не пишется, ошибки
    # (4xx/5xx) по-прежнему видны.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    p = argparse.ArgumentParser(prog="chatter.telethon_run")
    # Дефолт живёт в chatter/clients/active.yaml (онбординг-дырка №3), а не
    # здесь и не в скрипте гардиана: подключение клиента — правка конфига,
    # а не деплой.
    p.add_argument("--personas", default=None,
                    help="comma-separated persona slugs; the FIRST is primary "
                         "(supplies the allowlist) and the default for new senders. "
                         "По умолчанию — список из clients/active.yaml")
    p.add_argument("--clients-dir", default=str(Path(__file__).resolve().parent / "clients"))
    # Дефолта нет: путь выводится из ПЕРВИЧНОГО slug'а (онбординг-дырка №4),
    # поэтому забыть флаг и молча сесть на файлы другого клиента невозможно.
    p.add_argument("--session", default=None,
                    help="по умолчанию .secrets/<первичный-slug>.session")
    p.add_argument("--db", default=None,
                    help="по умолчанию .secrets/<первичный-slug>.db")
    p.add_argument("--llm", choices=["auto", "real", "fake"], default="auto")
    args = p.parse_args(argv)

    try:
        # Same .env fallback as the login script, so a deploy-faithful `.venv`
        # run doesn't require the operator to export TELEGRAM_API_ID/HASH.
        api_id, api_hash = load_api_credentials(os.environ, DEFAULT_ENV_FILE)
    except CredentialsError as e:
        print(f"[telethon_run] {e}", file=sys.stderr)
        return 1

    # For real Haiku replies, make ANTHROPIC_API_KEY available the same way --
    # fall back to the repo .env so `--llm real` works without a manual export.
    if args.llm in ("real", "auto") and not os.environ.get("ANTHROPIC_API_KEY"):
        _key = _parse_env_file(DEFAULT_ENV_FILE).get("ANTHROPIC_API_KEY")
        if _key:
            os.environ["ANTHROPIC_API_KEY"] = _key

    from telethon import TelegramClient  # deferred: only main() ever constructs a real client

    try:
        slugs = resolve_personas(
            arg=args.personas, clients_dir=Path(args.clients_dir), env=os.environ)
    except ActiveClientsError as e:
        print(f"[telethon_run] {e}", file=sys.stderr)
        return 1

    # Пути ВЫВОДЯТСЯ из первичного slug'а, а не из общего дефолта (дырка №4).
    session_path, db_path = resolve_runtime_paths(
        primary_slug=slugs[0], session_arg=args.session, db_arg=args.db, env=os.environ)
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # Боевой деплой уже живёт на старом общем дефолте — переносим один раз,
    # иначе смена дефолта разлогинила бы живую Аню.
    migrate_legacy_runtime_files(session_path=session_path, db_path=db_path)
    log.info("рантайм-файлы клиента %s: session=%s db=%s", slugs[0], session_path, db_path)

    # Own event loop, set as current: (a) works on Python 3.12+/3.14 where
    # asyncio.get_event_loop() raises at top level, and (b) is the SAME loop
    # Telethon's start()/run_until_disconnected() run on, so the transport's
    # run_coroutine_threadsafe(send/typing, loop) targets the loop that's
    # actually running (otherwise sends would never execute).
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = TelegramClient(session_path, int(api_id), api_hash)

    store = Store(db_path)
    runner = build_runner(
        client=client, clients_dir=Path(args.clients_dir), persona_slugs=slugs,
        store=store, loop=loop, llm_mode=args.llm,
    )

    async def _on_connected() -> None:
        # me_id ПЕРВЫМ ДЕЛОМ, до heartbeat и до catch-up: _outgoing_handler
        # уже зарегистрирован (build_runner отработал до client.start()) и
        # физически может получить апдейт, как только loop начнёт его
        # реально гонять -- это тот самый момент. Ставя присвоение me_id
        # первой строкой планируемой здесь корутины (запланирована
        # run_client'ом сразу после client.start(), ДО run_until_disconnected
        # начинает качать апдейты), даём ей выполниться раньше любого
        # реального сетевого апдейта -- та же гарантия, на которую уже
        # полагается catch_up_missed ниже.
        runner.me_id = (await client.get_me()).id
        # Own liveness stamp for the guardian, forever, alongside the one-shot
        # catch-up of anything that arrived while we were down.
        loop.create_task(heartbeat_loop())
        # Периодический авто-возврат (спека §8) -- тоже вечный фоновый цикл,
        # запускается рядом с heartbeat_loop по той же причине: должен жить
        # весь срок процесса, а не один раз при старте.
        loop.create_task(autoresume_loop(
            runner.primary_store(), auto_resume_hours=runner.control.auto_resume_hours))
        # Арка 3B: изолированный long-poll контрол-бота (свой токен → без 409
        # с основным Jarvis-ботом). Только если контрол-бот настроен.
        if runner.poller is not None:
            log.info("control-bot poller starting (isolated token)")
            loop.create_task(runner.poller.run_forever())
        # config-арка §2b: если стартовали на last-known-good (текущий конфиг
        # битый) — владелец обязан узнать (DEV-18), а не думать, что всё ок.
        if runner._startup_recovery:
            lang = runner.personas[runner.primary_slug].cfg.settings.language
            await runner._notify_owner_notice(
                cfg_text("cfg_startup_recovered", lang, reason=runner._startup_recovery))
        # config-арка §5: авто-перечитывание по mtime (opt-in из settings).
        if runner.control.auto_reload:
            log.info("config auto-reload watch starting (mtime)")
            loop.create_task(config_watch_loop(runner))
        # Fix 3 (одноразово): вычистить из истории команды пульта, которые
        # владелец мог набрать прямо в диалоге лида ДО этого фикса — иначе они
        # так и будут уходить в модель как «сообщения Ани».
        _store = runner.primary_store()
        if _store.get_runtime_flag("cmd_history_purged") != "1":
            removed = _store.delete_command_messages(_COMMAND_PREFIXES)
            _store.set_runtime_flag("cmd_history_purged", "1", ts=time.time())
            if removed:
                log.info("history purge: удалено %d команд из истории (Fix 3)", removed)
        await runner.catch_up_missed()

    print(f"[telethon_run] personas={slugs} session={session_path}")
    return run_client(client, loop, on_connected=_on_connected)


if __name__ == "__main__":
    raise SystemExit(main())
