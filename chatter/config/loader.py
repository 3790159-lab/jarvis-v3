from __future__ import annotations
from dataclasses import dataclass, field
import logging
import re
from pathlib import Path
import yaml

log = logging.getLogger(__name__)

LANGUAGES = {"ru", "en", "uk"}

# honesty_mode: как Аня отвечает на прямой вопрос «ты бот?».
#
#   honest               — раскалывается честно и предлагает владельца (ДЕФОЛТ).
#   free_owner_liability — гарантия честности снята, ответ идёт через brain.
#
# Свободное значение НАМЕРЕННО длинное и самоописывающее. Короткое `free`
# отклоняется: выключение честности — самое опасное действие в продукте
# (в ряде юрисдикций ещё и регулируемое), и оно не должно случиться от
# опечатки или копипасты чужого конфига. Владелец, печатающий
# `free_owner_liability`, читает, на ком ответственность.
HONESTY_HONEST = "honest"
HONESTY_FREE = "free_owner_liability"
HONESTY_MODES = (HONESTY_HONEST, HONESTY_FREE)

class ConfigError(Exception):
    pass

@dataclass(frozen=True)
class Timings:
    read_delay_min: float
    read_delay_max: float
    cps_min: float
    cps_max: float
    jitter_min: float
    jitter_max: float
    split_pause_min: float
    split_pause_max: float
    split_max_len: int
    night_multiplier: float
    debounce_window: float
    debounce_max: float

@dataclass(frozen=True)
class Limits:
    max_reply_tokens: int
    per_contact_hourly: int
    daily_cap: int
    # Окно истории ПО ТОКЕНАМ (арка «память», условие 2): бюджет хвоста
    # диалога в промпт + страховочный лимит по количеству сообщений.
    history_budget_tokens: int = 1700
    history_max_messages: int = 40
    # Потолок профиля лида (условие 4): профиль НЕ кэшируется и платится
    # полностью на каждом вызове (brain + classifier) → его рост = прямой
    # рост стоимости. Сверх потолка профиль НЕ применяется (явная деградация).
    # Промпт просит ⅔ от потолка (~500 симв при 250 ток) — запас между
    # просьбой и рубежом.
    profile_budget_tokens: int = 250

@dataclass(frozen=True)
class WorkHours:
    start: int
    end: int

@dataclass(frozen=True)
class TelegramConfig:
    allowlist: tuple[int, ...]
    # Арка 3C: переворот гейта допуска. funnel_gate=False → старое поведение
    # (отвечаем только allowlist). True → отвечаем незнакомцам-лидам, знакомых
    # (User.contact) не отвечаем (уведомляем владельца), denylist блокируем.
    # Включение = подтверждение оператора «аккаунт выделен под воронку».
    denylist: tuple[int, ...] = ()
    funnel_gate: bool = False

@dataclass(frozen=True)
class ControlConfig:
    """Пульт владельца (арки 3A/3B). Блок опционален: дефолты — рабочие."""
    auto_resume_hours: float = 6.0
    takeover_grace_seconds: float = 2.0   # окно на опознание своего исходящего
    status_window_hours: int = 24
    # Арка 3B: контрол-бот + эскалация. ВСЁ off/безопасно по умолчанию —
    # без токена продукт ведёт себя ровно как арка 3A (Saved Messages).
    control_bot_token_env: str | None = None  # ИМЯ env-переменной с токеном, НЕ значение
    owner_chat_id: int | None = None          # личный чат владельца (жёсткий гейт /start)
    pairing_code: str | None = None           # одноразовый код онбординга: /start <код>, сгорает после привязки
    classifier_enabled: bool = True
    classifier_error_threshold: int = 5       # > стольких ошибок за окно → алерт «деградировал»
    snooze_seconds: float = 3600.0            # кнопка «⏸ Ещё 1ч»
    auto_reload: bool = False                 # config-арка §5: перечитывать по mtime без команды

@dataclass(frozen=True)
class Settings:
    model: str
    language: str
    owner_id: str
    persona_name: str
    work_hours: WorkHours
    timings: Timings
    limits: Limits
    persona_age: int | None = None   # опционально, для /config; возраст живёт и в persona.md
    # Как Аня называет владельца в БЕЗОПАСНЫХ ответах лиду («свяжу вас с …»).
    # Пишется клиентом уже В НУЖНОМ ПАДЕЖЕ (напр. «владельцем», «менеджером»,
    # «Дмитрием»), чтобы не склонять owner_id программно. Пусто → «владельцем».
    owner_ref: str | None = None
    # Brand-safety (валюта/оплата на клиента): валюта показа + запрещённые
    # термины (для укр. бизнеса: рубли, российские банки/платёжные системы).
    currency: str | None = None                # напр. "грн"/"₴"/"USD" — валюта клиента
    forbidden_terms: tuple[str, ...] = ()      # упоминание в ответе → подавить+эскалация
    # Чем ЗАМЕНИТЬ подавленный ответ про оплату: подавление ≠ тишина. Лид должен
    # получить КОРРЕКТНЫЙ ответ (названы верные способы), без запрещённого слова.
    safe_payment_reply: str | None = None
    # --- Два per-client тумблера (осознанное решение владельца) -------------
    # strict_knowledge: Аня говорит ТОЛЬКО из knowledge. True (дефолт) =
    # необеспеченное обещание (скидка/гарантия/«перезвоню») ПОДАВЛЯЕТСЯ и
    # эскалируется. False = свободный режим: обещание доезжает до лида, но
    # карточка владельцу всё равно уходит. Ослабляется РОВНО этот слой:
    # выдуманные ЦИФРЫ (unbacked_claim) и brand-safety (forbidden_reply)
    # подавляются в ОБОИХ режимах — свободный ≠ право врать про цены и оплату.
    strict_knowledge: bool = True
    # honesty_mode: см. HONESTY_MODES выше. Дефолт — честный.
    honesty_mode: str = HONESTY_HONEST
    telegram: TelegramConfig | None = None
    control: ControlConfig = field(default_factory=ControlConfig)

@dataclass(frozen=True)
class Config:
    slug: str
    persona: str
    knowledge: str
    playbook: str
    settings: Settings
    # М8: эталонные пары «клієнт → персона» из examples.yaml (опционально).
    # Только для brain (голос), в классификатор не идут; факты — из knowledge.
    examples: tuple[tuple[str, str], ...] = ()

_TIMING_FIELDS = [
    "read_delay_min", "read_delay_max", "cps_min", "cps_max",
    "jitter_min", "jitter_max", "split_pause_min", "split_pause_max",
    "split_max_len", "night_multiplier", "debounce_window", "debounce_max",
]
_LIMIT_FIELDS = ["max_reply_tokens", "per_contact_hourly", "daily_cap",
                 "history_budget_tokens", "history_max_messages",
                 "profile_budget_tokens"]

# Дефолты = боевые значения demo-клиента: они обкатаны в проде, поэтому
# клиент, не указавший блок вовсе, получает заведомо рабочее поведение.
DEFAULT_TIMINGS = Timings(
    read_delay_min=1.5, read_delay_max=4.0, cps_min=4.0, cps_max=7.0,
    jitter_min=0.9, jitter_max=1.2, split_pause_min=0.6, split_pause_max=1.8,
    split_max_len=160, night_multiplier=2.5, debounce_window=3.0, debounce_max=15.0,
)
DEFAULT_LIMITS = Limits(max_reply_tokens=20000, per_contact_hourly=20, daily_cap=500,
                        history_budget_tokens=1700, history_max_messages=40,
                        profile_budget_tokens=250)
DEFAULT_WORK_HOURS = WorkHours(start=9, end=22)


def _optional_mapping(raw: dict, key: str) -> dict:
    """Блок настроек, который можно не писать вовсе. Явно указанный, но не
    словарь — ошибка (это опечатка структуры, а не осознанный пропуск)."""
    val = raw.get(key)
    if val is None:
        return {}
    if not isinstance(val, dict):
        raise ConfigError(f"settings.yaml: '{key}' must be a mapping")
    return val


def _reject_unknown(block: dict, allowed, where: str) -> None:
    unknown = sorted(set(block) - set(allowed))
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(unknown)} "
            f"(known: {', '.join(sorted(allowed))})")


def _require(d: dict, key: str, where: str):
    if not isinstance(d, dict) or key not in d:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return d[key]

def _read_text(client_dir: Path, name: str) -> str:
    p = client_dir / name
    if not p.exists():
        raise ConfigError(f"{name}: file not found in {client_dir}")
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        raise ConfigError(f"{name}: file is empty")
    return text

def _load_examples(client_dir: Path) -> tuple[tuple[str, str], ...]:
    """М8: examples.yaml — список пар {client, olga}. Файла нет → пусто.
    Кривой файл → громкий ConfigError (DEV-18): молча выпавшие примеры =
    тихо уехавший голос персоны."""
    path = client_dir / "examples.yaml"
    if not path.exists():
        return ()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"examples.yaml: invalid YAML ({exc})") from exc
    if not isinstance(raw, list):
        raise ConfigError("examples.yaml: top-level must be a list of pairs")
    pairs: list[tuple[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"client", "olga"}:
            raise ConfigError(
                f"examples.yaml: пара #{i + 1} должна быть {{client, olga}}")
        client, olga = item["client"], item["olga"]
        if not (isinstance(client, str) and client.strip()
                and isinstance(olga, str) and olga.strip()):
            raise ConfigError(
                f"examples.yaml: пара #{i + 1}: client/olga — непустые строки")
        pairs.append((client.strip(), olga.strip()))
    return tuple(pairs)


def load_config(clients_dir: Path, slug: str) -> Config:
    client_dir = Path(clients_dir) / slug
    if not client_dir.is_dir():
        raise ConfigError(f"client '{slug}' not found under {clients_dir}")

    persona = _read_text(client_dir, "persona.md")
    knowledge = _read_text(client_dir, "knowledge.md")
    playbook = _read_text(client_dir, "playbook.md")
    examples = _load_examples(client_dir)

    raw_path = client_dir / "settings.yaml"
    if not raw_path.exists():
        raise ConfigError("settings.yaml: file not found")
    try:
        raw = yaml.safe_load(raw_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"settings.yaml: invalid YAML ({exc})") from exc
    if not isinstance(raw, dict):
        raise ConfigError("settings.yaml: top-level must be a mapping")

    model = _require(raw, "model", "settings.yaml")
    language = _require(raw, "language", "settings.yaml")
    if language not in LANGUAGES:
        raise ConfigError(f"settings.yaml: language must be one of {sorted(LANGUAGES)}")
    owner_id = _require(raw, "owner_id", "settings.yaml")
    persona_name = _require(raw, "persona_name", "settings.yaml")
    if str(persona_name).strip().casefold() == str(owner_id).strip().casefold():
        raise ConfigError(
            f"settings.yaml: persona_name and owner_id must be different people "
            f"(both '{owner_id}')"
        )

    # Онбординг-дырка №1: блоки work_hours/timings/limits НЕОБЯЗАТЕЛЬНЫ —
    # у каждого поля есть рабочий дефолт. Раньше 12 обязательных полей в
    # timings означали, что забытое поле не даёт клиенту стартовать вообще.
    # Но ОПЕЧАТКА в имени поля по-прежнему громкая (_reject_unknown): молча
    # проигнорированный ключ = тихо разъехавшиеся тайминги и вопрос «почему
    # Аня печатает не так», на который нечем ответить (DEV-18).
    wh = _optional_mapping(raw, "work_hours")
    _reject_unknown(wh, ("start", "end"), "settings.yaml.work_hours")
    work_hours = WorkHours(
        start=int(wh.get("start", DEFAULT_WORK_HOURS.start)),
        end=int(wh.get("end", DEFAULT_WORK_HOURS.end)),
    )

    t = _optional_mapping(raw, "timings")
    _reject_unknown(t, _TIMING_FIELDS, "settings.yaml.timings")
    timing_kwargs = {f: float(t.get(f, getattr(DEFAULT_TIMINGS, f))) for f in _TIMING_FIELDS}
    timing_kwargs["split_max_len"] = int(timing_kwargs["split_max_len"])
    timings = Timings(**timing_kwargs)

    l = _optional_mapping(raw, "limits")
    _reject_unknown(l, _LIMIT_FIELDS, "settings.yaml.limits")
    limits = Limits(**{f: int(l.get(f, getattr(DEFAULT_LIMITS, f))) for f in _LIMIT_FIELDS})

    # Тумблеры клиента. strict_knowledge — обычный bool с безопасным дефолтом.
    # honesty_mode — валидируется СТРОГО: неизвестное значение это ConfigError,
    # а не тихий фолбэк в honest. Тихий фолбэк скрыл бы от владельца, что его
    # настройка не применилась (DEV-18), а здесь цена ошибки — репутация и,
    # в ряде юрисдикций, закон.
    strict_knowledge = bool(raw.get("strict_knowledge", True))
    honesty_mode = str(raw.get("honesty_mode", HONESTY_HONEST)).strip()
    if honesty_mode not in HONESTY_MODES:
        raise ConfigError(
            f"settings.yaml: 'honesty_mode' must be one of {list(HONESTY_MODES)} "
            f"(got '{honesty_mode}'). Выключение честности требует ПОЛНОГО "
            f"значения '{HONESTY_FREE}' — короткое 'free' отклоняется намеренно, "
            f"чтобы Аня не перестала признаваться в том, что она не человек, "
            f"из-за опечатки.")
    if honesty_mode == HONESTY_FREE:
        # След в логе на каждой загрузке/перечитывании конфига. Смысл не в
        # диагностике (код работает штатно), а в том, чтобы у решения был
        # владелец: в логе видно, что обман включён ЯВНО настройкой клиента,
        # а не приехал нашим дефолтом.
        log.warning(
            "client '%s': honesty_mode=%s — гарантия честности ВЫКЛЮЧЕНА владельцем; "
            "на прямой вопрос «ты бот?» раскрытие не отправляется",
            slug, HONESTY_FREE)

    telegram: TelegramConfig | None = None
    tg_raw = raw.get("telegram")
    if tg_raw is not None:
        allowlist_raw = _require(tg_raw, "allowlist", "settings.yaml.telegram")
        if not isinstance(allowlist_raw, list):
            raise ConfigError("settings.yaml.telegram: 'allowlist' must be a list")
        denylist_raw = tg_raw.get("denylist", [])
        if not isinstance(denylist_raw, list):
            raise ConfigError("settings.yaml.telegram: 'denylist' must be a list")
        telegram = TelegramConfig(
            allowlist=tuple(int(x) for x in allowlist_raw),
            denylist=tuple(int(x) for x in denylist_raw),
            funnel_gate=bool(tg_raw.get("funnel_gate", False)),
        )

    # Единственный источник дефолтов — сам ControlConfig(): читаем их из
    # инстанса, а не дублируем числами здесь, иначе код и дефолты дата-класса
    # разъедутся при следующей правке одного без другого.
    default_control = ControlConfig()
    control = default_control
    c_raw = raw.get("control")
    if c_raw is not None:
        if not isinstance(c_raw, dict):
            raise ConfigError("settings.yaml: 'control' must be a mapping")
        owner_chat_raw = c_raw.get("owner_chat_id", default_control.owner_chat_id)
        token_env_raw = c_raw.get("control_bot_token_env", default_control.control_bot_token_env)
        pairing_raw = c_raw.get("pairing_code", default_control.pairing_code)
        if pairing_raw is not None:
            pairing_str = str(pairing_raw)
            # Код едет как payload deep-link'а t.me/<bot>?start=<код>, а Telegram
            # ограничивает start-параметр алфавитом [A-Za-z0-9_-], ≤64 символа.
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pairing_str):
                raise ConfigError(
                    "settings.yaml.control: 'pairing_code' must match [A-Za-z0-9_-], "
                    "1-64 chars (Telegram deep-link start payload)")
        control = ControlConfig(
            auto_resume_hours=float(c_raw.get("auto_resume_hours", default_control.auto_resume_hours)),
            takeover_grace_seconds=float(c_raw.get("takeover_grace_seconds", default_control.takeover_grace_seconds)),
            status_window_hours=int(c_raw.get("status_window_hours", default_control.status_window_hours)),
            control_bot_token_env=None if token_env_raw is None else str(token_env_raw),
            owner_chat_id=None if owner_chat_raw is None else int(owner_chat_raw),
            pairing_code=None if pairing_raw is None else str(pairing_raw),
            classifier_enabled=bool(c_raw.get("classifier_enabled", default_control.classifier_enabled)),
            classifier_error_threshold=int(c_raw.get("classifier_error_threshold", default_control.classifier_error_threshold)),
            snooze_seconds=float(c_raw.get("snooze_seconds", default_control.snooze_seconds)),
            auto_reload=bool(c_raw.get("auto_reload", default_control.auto_reload)),
        )

    return Config(
        slug=slug, persona=persona, knowledge=knowledge, playbook=playbook,
        examples=examples,
        settings=Settings(model=str(model), language=str(language), owner_id=str(owner_id),
                          persona_name=str(persona_name),
                          persona_age=(int(raw["persona_age"]) if raw.get("persona_age") is not None else None),
                          owner_ref=(str(raw["owner_ref"]) if raw.get("owner_ref") is not None else None),
                          currency=(str(raw["currency"]) if raw.get("currency") is not None else None),
                          forbidden_terms=tuple(str(x) for x in raw.get("forbidden_terms", []) or []),
                          safe_payment_reply=(str(raw["safe_payment_reply"]) if raw.get("safe_payment_reply") is not None else None),
                          strict_knowledge=strict_knowledge, honesty_mode=honesty_mode,
                          work_hours=work_hours, timings=timings, limits=limits,
                          telegram=telegram, control=control),
    )
