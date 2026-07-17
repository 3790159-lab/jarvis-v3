from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml

LANGUAGES = {"ru", "en", "uk"}

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

@dataclass(frozen=True)
class WorkHours:
    start: int
    end: int

@dataclass(frozen=True)
class TelegramConfig:
    allowlist: tuple[int, ...]

@dataclass(frozen=True)
class ControlConfig:
    """Пульт владельца (арки 3A/3B). Блок опционален: дефолты — рабочие."""
    auto_resume_hours: float = 6.0
    takeover_grace_seconds: float = 2.0   # окно на опознание своего исходящего
    status_window_hours: int = 24
    # Арка 3B: контрол-бот + эскалация. ВСЁ off/безопасно по умолчанию —
    # без токена продукт ведёт себя ровно как арка 3A (Saved Messages).
    control_bot_token_env: str | None = None  # ИМЯ env-переменной с токеном, НЕ значение
    owner_chat_id: int | None = None          # личный чат владельца; None = bind на первый /start
    classifier_enabled: bool = True
    classifier_error_threshold: int = 5       # > стольких ошибок за окно → алерт «деградировал»
    snooze_seconds: float = 3600.0            # кнопка «⏸ Ещё 1ч»

@dataclass(frozen=True)
class Settings:
    model: str
    language: str
    owner_id: str
    persona_name: str
    work_hours: WorkHours
    timings: Timings
    limits: Limits
    telegram: TelegramConfig | None = None
    control: ControlConfig = field(default_factory=ControlConfig)

@dataclass(frozen=True)
class Config:
    slug: str
    persona: str
    knowledge: str
    playbook: str
    settings: Settings

_TIMING_FIELDS = [
    "read_delay_min", "read_delay_max", "cps_min", "cps_max",
    "jitter_min", "jitter_max", "split_pause_min", "split_pause_max",
    "split_max_len", "night_multiplier", "debounce_window", "debounce_max",
]
_LIMIT_FIELDS = ["max_reply_tokens", "per_contact_hourly", "daily_cap"]

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

def load_config(clients_dir: Path, slug: str) -> Config:
    client_dir = Path(clients_dir) / slug
    if not client_dir.is_dir():
        raise ConfigError(f"client '{slug}' not found under {clients_dir}")

    persona = _read_text(client_dir, "persona.md")
    knowledge = _read_text(client_dir, "knowledge.md")
    playbook = _read_text(client_dir, "playbook.md")

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

    wh = _require(raw, "work_hours", "settings.yaml")
    work_hours = WorkHours(
        start=int(_require(wh, "start", "settings.yaml.work_hours")),
        end=int(_require(wh, "end", "settings.yaml.work_hours")),
    )

    t = _require(raw, "timings", "settings.yaml")
    timing_kwargs = {f: float(_require(t, f, "settings.yaml.timings")) for f in _TIMING_FIELDS}
    timing_kwargs["split_max_len"] = int(timing_kwargs["split_max_len"])
    timings = Timings(**timing_kwargs)

    l = _require(raw, "limits", "settings.yaml")
    limits = Limits(**{f: int(_require(l, f, "settings.yaml.limits")) for f in _LIMIT_FIELDS})

    telegram: TelegramConfig | None = None
    tg_raw = raw.get("telegram")
    if tg_raw is not None:
        allowlist_raw = _require(tg_raw, "allowlist", "settings.yaml.telegram")
        if not isinstance(allowlist_raw, list):
            raise ConfigError("settings.yaml.telegram: 'allowlist' must be a list")
        telegram = TelegramConfig(allowlist=tuple(int(x) for x in allowlist_raw))

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
        control = ControlConfig(
            auto_resume_hours=float(c_raw.get("auto_resume_hours", default_control.auto_resume_hours)),
            takeover_grace_seconds=float(c_raw.get("takeover_grace_seconds", default_control.takeover_grace_seconds)),
            status_window_hours=int(c_raw.get("status_window_hours", default_control.status_window_hours)),
            control_bot_token_env=None if token_env_raw is None else str(token_env_raw),
            owner_chat_id=None if owner_chat_raw is None else int(owner_chat_raw),
            classifier_enabled=bool(c_raw.get("classifier_enabled", default_control.classifier_enabled)),
            classifier_error_threshold=int(c_raw.get("classifier_error_threshold", default_control.classifier_error_threshold)),
            snooze_seconds=float(c_raw.get("snooze_seconds", default_control.snooze_seconds)),
        )

    return Config(
        slug=slug, persona=persona, knowledge=knowledge, playbook=playbook,
        settings=Settings(model=str(model), language=str(language), owner_id=str(owner_id),
                          persona_name=str(persona_name),
                          work_hours=work_hours, timings=timings, limits=limits,
                          telegram=telegram, control=control),
    )
