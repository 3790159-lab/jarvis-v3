from __future__ import annotations
from dataclasses import dataclass
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

@dataclass(frozen=True)
class Limits:
    max_tokens_per_dialog: int
    per_contact_hourly: int
    daily_cap: int

@dataclass(frozen=True)
class WorkHours:
    start: int
    end: int

@dataclass(frozen=True)
class Settings:
    model: str
    language: str
    owner_id: str
    work_hours: WorkHours
    timings: Timings
    limits: Limits

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
    "split_max_len", "night_multiplier", "debounce_window",
]
_LIMIT_FIELDS = ["max_tokens_per_dialog", "per_contact_hourly", "daily_cap"]

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

    return Config(
        slug=slug, persona=persona, knowledge=knowledge, playbook=playbook,
        settings=Settings(model=str(model), language=str(language), owner_id=str(owner_id),
                          work_hours=work_hours, timings=timings, limits=limits),
    )
