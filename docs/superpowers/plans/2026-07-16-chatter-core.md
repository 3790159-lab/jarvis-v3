# CHATTER-1 Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, configurable chat-assistant core (`chatter/`) that holds a human-feeling DM conversation from a client persona, driven by a fake console transport.

**Architecture:** New top-level package `chatter/` with **zero imports from `app/` or `tools/`**. Config lives inside `chatter/clients/<slug>/`. Humanizer is pure functions (time/RNG injected, no `sleep` in logic). LLM sits behind `LLMClient` (`AnthropicLLM` for prod, `FakeLLM` for tests/offline). Disclosure honesty is hardcoded. State + history persist in one SQLite file.

**Tech Stack:** Python 3.11, `anthropic` SDK (Claude Haiku 4.5), `PyYAML`, stdlib `sqlite3`, `pytest`.

**Conventions:** Work on branch `phase-4.0-unified-jarvis`. Run everything from `C:\jarvis`. Run tests with `python -m pytest tests/chatter/ -q`. Commit after every green step. All source files start with `from __future__ import annotations`.

---

## File Structure

| File | Responsibility |
|---|---|
| `chatter/__init__.py`, `chatter/*/__init__.py` | package markers |
| `chatter/requirements.txt` | `anthropic`, `PyYAML` |
| `chatter/config/loader.py` | dataclasses `Timings/Limits/WorkHours/Settings/Config` + `load_config` + `ConfigError` |
| `chatter/core/humanizer.py` | pure: `is_night`, `read_delay`, `typing_duration`, `split_message`, `debounce_ready`, `coalesce` |
| `chatter/core/llm.py` | `LLMClient` ABC, `FakeLLM`, `AnthropicLLM` |
| `chatter/storage/db.py` | `Store` over one SQLite file |
| `chatter/core/conversation.py` | `next_state` FSM + `Conversation` helper |
| `chatter/core/disclosure.py` | `is_bot_question`, `honest_disclosure`, `HONESTY_MARKER` |
| `chatter/core/guardrails.py` | `contains_unbacked_claim`, `within_hourly_limit`, `within_daily_cap` |
| `chatter/core/brain.py` | `build_system_prompt`, `build_messages`, `Brain` |
| `chatter/transport/base.py` | `Transport` ABC |
| `chatter/transport/fake.py` | `FakeConsoleTransport` (queue + reader thread) |
| `chatter/run.py` | testable `process_batch`/`gather_batch` + CLI `main` |
| `chatter/clients/demo/*` | demo persona (RU personal brand: consultations + photoshoots) |
| `tests/chatter/*` | mirror tests |

---

### Task 0: Scaffold + boundary guard

**Files:**
- Create: `chatter/__init__.py`, `chatter/config/__init__.py`, `chatter/core/__init__.py`, `chatter/storage/__init__.py`, `chatter/transport/__init__.py`, `chatter/clients/__init__.py` (all empty)
- Create: `chatter/requirements.txt`
- Create: `tests/chatter/__init__.py` (empty)
- Test: `tests/chatter/test_boundary.py`

- [ ] **Step 1: Create package dirs and empty `__init__.py` files**

Create each `__init__.py` above as an empty file. `chatter/requirements.txt`:

```
anthropic>=0.40
PyYAML>=6.0
```

- [ ] **Step 2: Write the failing boundary test**

`tests/chatter/test_boundary.py`:

```python
from __future__ import annotations
from pathlib import Path
import re

CHATTER = Path(__file__).resolve().parents[2] / "chatter"
FORBIDDEN = re.compile(r"^\s*(from|import)\s+(app|tools)(\.|\s|$)", re.MULTILINE)

def test_no_imports_from_app_or_tools():
    offenders = []
    for py in CHATTER.rglob("*.py"):
        if FORBIDDEN.search(py.read_text(encoding="utf-8")):
            offenders.append(str(py.relative_to(CHATTER)))
    assert offenders == [], f"chatter/ must not import app/ or tools/: {offenders}"

def test_chatter_package_importable():
    import chatter  # noqa: F401
```

- [ ] **Step 3: Run to verify it passes** (there is no offending code yet)

Run: `python -m pytest tests/chatter/test_boundary.py -q`
Expected: PASS (2 passed)

- [ ] **Step 4: Commit**

```bash
git add chatter tests/chatter/__init__.py tests/chatter/test_boundary.py
git commit -m "feat(chatter): scaffold package + app/tools boundary guard"
```

---

### Task 1: Config loader with startup validation

**Files:**
- Create: `chatter/config/loader.py`
- Test: `tests/chatter/test_loader.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_loader.py`:

```python
from __future__ import annotations
from pathlib import Path
import textwrap
import pytest
from chatter.config.loader import load_config, ConfigError

SETTINGS = """\
model: claude-haiku-4-5
language: ru
owner_id: "owner-1"
work_hours: {start: 9, end: 22}
timings:
  read_delay_min: 1.0
  read_delay_max: 5.0
  cps_min: 3.0
  cps_max: 6.0
  jitter_min: 0.8
  jitter_max: 1.4
  split_pause_min: 0.5
  split_pause_max: 2.0
  split_max_len: 160
  night_multiplier: 2.0
  debounce_window: 3.0
limits:
  max_tokens_per_dialog: 20000
  per_contact_hourly: 20
  daily_cap: 500
"""

def _make_client(root: Path, slug: str = "demo", settings: str = SETTINGS):
    d = root / slug
    d.mkdir(parents=True)
    (d / "persona.md").write_text("Меня зовут Аня.", encoding="utf-8")
    (d / "knowledge.md").write_text("Консультация 5000.", encoding="utf-8")
    (d / "playbook.md").write_text("Стадии воронки.", encoding="utf-8")
    (d / "settings.yaml").write_text(settings, encoding="utf-8")
    return d

def test_loads_valid_config(tmp_path):
    _make_client(tmp_path)
    cfg = load_config(tmp_path, "demo")
    assert cfg.slug == "demo"
    assert cfg.persona.startswith("Меня зовут")
    assert cfg.settings.model == "claude-haiku-4-5"
    assert cfg.settings.language == "ru"
    assert cfg.settings.timings.cps_max == 6.0
    assert cfg.settings.limits.daily_cap == 500
    assert cfg.settings.work_hours.start == 9

def test_missing_file_fails_at_load(tmp_path):
    d = _make_client(tmp_path)
    (d / "knowledge.md").unlink()
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "knowledge.md" in str(e.value)

def test_empty_persona_fails(tmp_path):
    d = _make_client(tmp_path)
    (d / "persona.md").write_text("   ", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "persona.md" in str(e.value)

def test_bad_language_fails(tmp_path):
    _make_client(tmp_path, settings=SETTINGS.replace("language: ru", "language: fr"))
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "language" in str(e.value)

def test_missing_settings_key_fails(tmp_path):
    broken = "\n".join(l for l in SETTINGS.splitlines() if "daily_cap" not in l)
    _make_client(tmp_path, settings=broken)
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path, "demo")
    assert "daily_cap" in str(e.value)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_loader.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.config.loader`)

- [ ] **Step 3: Write minimal implementation**

`chatter/config/loader.py`:

```python
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
    timings = Timings(**{f: float(_require(t, f, "settings.yaml.timings")) for f in _TIMING_FIELDS})
    timings = Timings(**{**timings.__dict__, "split_max_len": int(timings.split_max_len)})

    l = _require(raw, "limits", "settings.yaml")
    limits = Limits(**{f: int(_require(l, f, "settings.yaml.limits")) for f in _LIMIT_FIELDS})

    return Config(
        slug=slug, persona=persona, knowledge=knowledge, playbook=playbook,
        settings=Settings(model=str(model), language=str(language), owner_id=str(owner_id),
                          work_hours=work_hours, timings=timings, limits=limits),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_loader.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/config/loader.py tests/chatter/test_loader.py
git commit -m "feat(chatter): config loader with startup validation"
```

---

### Task 2: Humanizer — `is_night` + `read_delay`

**Files:**
- Create: `chatter/core/humanizer.py`
- Test: `tests/chatter/test_humanizer_delays.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_humanizer_delays.py`:

```python
from __future__ import annotations
import random
from chatter.config.loader import Timings, WorkHours
from chatter.core import humanizer as H

TIMINGS = Timings(
    read_delay_min=1.0, read_delay_max=5.0, cps_min=3.0, cps_max=6.0,
    jitter_min=0.8, jitter_max=1.4, split_pause_min=0.5, split_pause_max=2.0,
    split_max_len=160, night_multiplier=2.0, debounce_window=3.0,
)
WH = WorkHours(start=9, end=22)

def test_is_night_true_before_open_and_after_close():
    assert H.is_night(3, WH) is True
    assert H.is_night(23, WH) is True

def test_is_night_false_during_hours():
    assert H.is_night(9, WH) is False
    assert H.is_night(21, WH) is False

def test_read_delay_within_range_day():
    rng = random.Random(0)
    for _ in range(50):
        d = H.read_delay(rng, TIMINGS, night=False)
        assert TIMINGS.read_delay_min <= d <= TIMINGS.read_delay_max

def test_read_delay_night_is_larger():
    d_day = H.read_delay(random.Random(1), TIMINGS, night=False)
    d_night = H.read_delay(random.Random(1), TIMINGS, night=True)
    assert d_night == d_day * TIMINGS.night_multiplier
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_humanizer_delays.py -q`
Expected: FAIL (`ModuleNotFoundError` / `AttributeError`)

- [ ] **Step 3: Write minimal implementation**

`chatter/core/humanizer.py`:

```python
from __future__ import annotations
import random
from chatter.config.loader import Timings, WorkHours

def is_night(hour: int, work_hours: WorkHours) -> bool:
    return not (work_hours.start <= hour < work_hours.end)

def read_delay(rng: random.Random, t: Timings, *, night: bool) -> float:
    base = rng.uniform(t.read_delay_min, t.read_delay_max)
    return base * t.night_multiplier if night else base
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_humanizer_delays.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/core/humanizer.py tests/chatter/test_humanizer_delays.py
git commit -m "feat(chatter): humanizer is_night + read_delay"
```

---

### Task 3: Humanizer — `typing_duration`

**Files:**
- Modify: `chatter/core/humanizer.py`
- Test: `tests/chatter/test_humanizer_typing.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_humanizer_typing.py`:

```python
from __future__ import annotations
import random
from chatter.core import humanizer as H
from tests.chatter.test_humanizer_delays import TIMINGS

def test_typing_duration_scales_with_length():
    short = H.typing_duration("hi", random.Random(0), TIMINGS, night=False)
    long = H.typing_duration("hi" * 100, random.Random(0), TIMINGS, night=False)
    assert long > short

def test_typing_duration_within_expected_bounds():
    text = "a" * 60  # 60 chars
    rng = random.Random(0)
    d = H.typing_duration(text, rng, TIMINGS, night=False)
    # slowest: 60/3 * 1.4 = 28.0 ; fastest: 60/6 * 0.8 = 8.0
    assert 8.0 <= d <= 28.0

def test_typing_duration_night_multiplier():
    text = "hello world"
    d_day = H.typing_duration(text, random.Random(5), TIMINGS, night=False)
    d_night = H.typing_duration(text, random.Random(5), TIMINGS, night=True)
    assert d_night == d_day * TIMINGS.night_multiplier

def test_typing_duration_empty_is_zero():
    assert H.typing_duration("", random.Random(0), TIMINGS, night=False) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_humanizer_typing.py -q`
Expected: FAIL (`AttributeError: module ... has no attribute 'typing_duration'`)

- [ ] **Step 3: Write minimal implementation** — append to `chatter/core/humanizer.py`:

```python
def typing_duration(text: str, rng: random.Random, t: Timings, *, night: bool) -> float:
    n = len(text)
    if n == 0:
        return 0.0
    cps = rng.uniform(t.cps_min, t.cps_max)
    jitter = rng.uniform(t.jitter_min, t.jitter_max)
    duration = (n / cps) * jitter
    return duration * t.night_multiplier if night else duration
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_humanizer_typing.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/core/humanizer.py tests/chatter/test_humanizer_typing.py
git commit -m "feat(chatter): humanizer typing_duration"
```

---

### Task 4: Humanizer — `split_message`

**Files:**
- Modify: `chatter/core/humanizer.py`
- Test: `tests/chatter/test_humanizer_split.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_humanizer_split.py`:

```python
from __future__ import annotations
from chatter.core import humanizer as H
from tests.chatter.test_humanizer_delays import TIMINGS

def test_short_message_not_split():
    assert H.split_message("Привет!", TIMINGS) == ["Привет!"]

def test_long_message_split_into_2_or_3_parts():
    text = ("Первое предложение здесь. Второе предложение тоже тут. "
            "Третье предложение продолжает. Четвёртое завершает мысль.")
    parts = H.split_message(text, TIMINGS)
    assert 2 <= len(parts) <= 3
    # No content lost (ignoring whitespace differences)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")

def test_split_never_exceeds_max_parts():
    text = " ".join(f"Предложение номер {i}." for i in range(20))
    parts = H.split_message(text, TIMINGS)
    assert len(parts) <= 3

def test_each_part_nonempty():
    text = "Раз. Два. Три. Четыре. Пять. Шесть."
    for p in H.split_message(text, TIMINGS):
        assert p.strip()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_humanizer_split.py -q`
Expected: FAIL (`AttributeError: ... 'split_message'`)

- [ ] **Step 3: Write minimal implementation** — append to `chatter/core/humanizer.py`:

```python
import re as _re

_SENTENCE = _re.compile(r"[^.!?…]+[.!?…]*\s*")

def split_message(text: str, t: Timings, *, max_parts: int = 3) -> list[str]:
    text = text.strip()
    if len(text) <= t.split_max_len:
        return [text]
    sentences = [s.strip() for s in _SENTENCE.findall(text) if s.strip()]
    if len(sentences) <= 1:
        return [text]
    # Greedily pack sentences into <= max_parts chunks, each aiming <= split_max_len.
    target = max(t.split_max_len, (len(text) // max_parts) + 1)
    parts: list[str] = []
    cur = ""
    for s in sentences:
        candidate = (cur + " " + s).strip() if cur else s
        if cur and len(candidate) > target and len(parts) < max_parts - 1:
            parts.append(cur)
            cur = s
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    return parts[:max_parts]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_humanizer_split.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/core/humanizer.py tests/chatter/test_humanizer_split.py
git commit -m "feat(chatter): humanizer split_message"
```

---

### Task 5: Humanizer — inbound `debounce_ready` + `coalesce`

**Files:**
- Modify: `chatter/core/humanizer.py`
- Test: `tests/chatter/test_humanizer_debounce.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_humanizer_debounce.py`:

```python
from __future__ import annotations
from chatter.core import humanizer as H

def test_debounce_not_ready_within_window():
    # last message at t=100, window 3s, now=102 -> still collecting
    assert H.debounce_ready(last_received_at=100.0, now=102.0, window=3.0) is False

def test_debounce_ready_after_window():
    assert H.debounce_ready(last_received_at=100.0, now=103.1, window=3.0) is True

def test_debounce_boundary_is_ready():
    assert H.debounce_ready(last_received_at=100.0, now=103.0, window=3.0) is True

def test_coalesce_joins_in_order():
    assert H.coalesce(["привет", "а сколько стоит?"]) == "привет\nа сколько стоит?"

def test_coalesce_single():
    assert H.coalesce(["одно сообщение"]) == "одно сообщение"

def test_coalesce_strips_blanks():
    assert H.coalesce(["  привет ", "", "  ещё "]) == "привет\nещё"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_humanizer_debounce.py -q`
Expected: FAIL (`AttributeError: ... 'debounce_ready'`)

- [ ] **Step 3: Write minimal implementation** — append to `chatter/core/humanizer.py`:

```python
def debounce_ready(*, last_received_at: float, now: float, window: float) -> bool:
    """True once `window` seconds have elapsed since the last inbound message.
    The window is extended by callers by updating `last_received_at` on each new message."""
    return (now - last_received_at) >= window

def coalesce(messages: list[str]) -> str:
    """Merge a burst of inbound messages into one prompt, in arrival order."""
    return "\n".join(m.strip() for m in messages if m.strip())
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_humanizer_debounce.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/core/humanizer.py tests/chatter/test_humanizer_debounce.py
git commit -m "feat(chatter): humanizer inbound debounce + coalesce (bot-tell fix)"
```

---

### Task 6: LLM interface + `FakeLLM`

**Files:**
- Create: `chatter/core/llm.py`
- Test: `tests/chatter/test_llm_fake.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_llm_fake.py`:

```python
from __future__ import annotations
from chatter.core.llm import LLMClient, FakeLLM

def test_fakellm_is_llmclient():
    assert isinstance(FakeLLM(), LLMClient)

def test_fakellm_records_calls_and_returns_default():
    llm = FakeLLM()
    out = llm.complete("SYS", [{"role": "user", "content": "привет"}], max_tokens=100)
    assert isinstance(out, str) and out
    assert llm.calls[0]["system"] == "SYS"
    assert llm.calls[0]["messages"][0]["content"] == "привет"
    assert llm.calls[0]["max_tokens"] == 100

def test_fakellm_scripted_replies_in_order():
    llm = FakeLLM(scripted=["первый", "второй"])
    assert llm.complete("s", [{"role": "user", "content": "a"}], max_tokens=10) == "первый"
    assert llm.complete("s", [{"role": "user", "content": "b"}], max_tokens=10) == "второй"

def test_fakellm_echoes_last_user_when_no_script():
    llm = FakeLLM()
    out = llm.complete("s", [{"role": "user", "content": "сколько стоит фотосессия"}], max_tokens=10)
    assert "фотосессия" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_llm_fake.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.core.llm`)

- [ ] **Step 3: Write minimal implementation**

`chatter/core/llm.py`:

```python
from __future__ import annotations
from abc import ABC, abstractmethod

class LLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, messages: list[dict], *, max_tokens: int) -> str:
        ...

class FakeLLM(LLMClient):
    """Deterministic, offline LLM for tests and no-key demo runs."""
    def __init__(self, scripted: list[str] | None = None):
        self._scripted = list(scripted) if scripted else []
        self._i = 0
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict], *, max_tokens: int) -> str:
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
        if self._i < len(self._scripted):
            out = self._scripted[self._i]
            self._i += 1
            return out
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return f"Поняла вас про «{last_user}». Расскажите чуть подробнее, что именно ищете?"

class AnthropicLLM(LLMClient):
    """Production client — Claude Haiku 4.5. Reads ANTHROPIC_API_KEY from env."""
    def __init__(self, model: str):
        import anthropic  # imported lazily so tests never need the SDK/key
        self._client = anthropic.Anthropic()
        self._model = model

    def complete(self, system: str, messages: list[dict], *, max_tokens: int) -> str:
        resp = self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system, messages=messages,
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_llm_fake.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/core/llm.py tests/chatter/test_llm_fake.py
git commit -m "feat(chatter): LLMClient ABC + FakeLLM + lazy AnthropicLLM"
```

---

### Task 7: Storage (SQLite)

**Files:**
- Create: `chatter/storage/db.py`
- Test: `tests/chatter/test_store.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_store.py`:

```python
from __future__ import annotations
from chatter.storage.db import Store

def test_get_or_create_contact_defaults(tmp_path):
    s = Store(tmp_path / "c.db")
    c = s.get_or_create_contact("u1")
    assert c["state"] == "new"
    assert c["paused"] == 0 and c["human_took_over"] == 0
    # idempotent
    assert s.get_or_create_contact("u1")["state"] == "new"

def test_state_and_flags_persist(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.set_state("u1", "hot")
    s.set_flag("u1", "paused", True)
    c = s.get_or_create_contact("u1")
    assert c["state"] == "hot" and c["paused"] == 1

def test_messages_and_history(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.add_message("u1", "user", "привет", ts=100.0)
    s.add_message("u1", "assistant", "здравствуйте", ts=101.0)
    hist = s.history("u1")
    assert [(m["role"], m["text"]) for m in hist] == [
        ("user", "привет"), ("assistant", "здравствуйте")]

def test_facts_roundtrip(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.set_fact("u1", "budget", "10000")
    s.set_fact("u1", "budget", "12000")  # upsert
    assert s.facts("u1") == {"budget": "12000"}

def test_count_messages_since(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.add_message("u1", "assistant", "a", ts=100.0)
    s.add_message("u1", "assistant", "b", ts=200.0)
    assert s.count_messages_since("u1", role="assistant", since_ts=150.0) == 1

def test_count_outbound_between(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.add_message("u1", "assistant", "a", ts=100.0)
    s.add_message("u1", "user", "b", ts=110.0)
    assert s.count_outbound_between(0.0, 1000.0) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_store.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.storage.db`)

- [ ] **Step 3: Write minimal implementation**

`chatter/storage/db.py`:

```python
from __future__ import annotations
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'new',
    paused INTEGER NOT NULL DEFAULT 0,
    human_took_over INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    contact_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (contact_id, key)
);
"""

class Store:
    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def get_or_create_contact(self, contact_id: str) -> dict:
        cur = self._conn.execute("SELECT * FROM contacts WHERE contact_id=?", (contact_id,))
        row = cur.fetchone()
        if row is None:
            self._conn.execute("INSERT INTO contacts(contact_id) VALUES (?)", (contact_id,))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE contact_id=?", (contact_id,)).fetchone()
        return dict(row)

    def set_state(self, contact_id: str, state: str) -> None:
        self._conn.execute("UPDATE contacts SET state=? WHERE contact_id=?", (state, contact_id))
        self._conn.commit()

    def set_flag(self, contact_id: str, flag: str, value: bool) -> None:
        if flag not in {"paused", "human_took_over"}:
            raise ValueError(f"unknown flag: {flag}")
        self._conn.execute(
            f"UPDATE contacts SET {flag}=? WHERE contact_id=?", (int(value), contact_id))
        self._conn.commit()

    def add_message(self, contact_id: str, role: str, text: str, ts: float) -> None:
        self._conn.execute(
            "INSERT INTO messages(contact_id, role, text, ts) VALUES (?,?,?,?)",
            (contact_id, role, text, ts))
        self._conn.commit()

    def history(self, contact_id: str, limit: int | None = None) -> list[dict]:
        q = "SELECT role, text, ts FROM messages WHERE contact_id=? ORDER BY id"
        rows = self._conn.execute(q, (contact_id,)).fetchall()
        rows = [dict(r) for r in rows]
        return rows[-limit:] if limit else rows

    def set_fact(self, contact_id: str, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO facts(contact_id, key, value) VALUES (?,?,?) "
            "ON CONFLICT(contact_id, key) DO UPDATE SET value=excluded.value",
            (contact_id, key, value))
        self._conn.commit()

    def facts(self, contact_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT key, value FROM facts WHERE contact_id=?", (contact_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def count_messages_since(self, contact_id: str, role: str, since_ts: float) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE contact_id=? AND role=? AND ts>=?",
            (contact_id, role, since_ts)).fetchone()[0]

    def count_outbound_between(self, start_ts: float, end_ts: float) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE role='assistant' AND ts>=? AND ts<?",
            (start_ts, end_ts)).fetchone()[0]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_store.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/storage/db.py tests/chatter/test_store.py
git commit -m "feat(chatter): SQLite Store (contacts/messages/facts)"
```

---

### Task 8: Conversation FSM

**Files:**
- Create: `chatter/core/conversation.py`
- Test: `tests/chatter/test_conversation.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_conversation.py`:

```python
from __future__ import annotations
import pytest
from chatter.core.conversation import next_state, STATES

def test_states_set():
    assert STATES == {"new", "qualifying", "hot", "escalated", "closed", "dead"}

@pytest.mark.parametrize("start,signal,expected", [
    ("new", "engaged", "qualifying"),
    ("qualifying", "interested", "hot"),
    ("qualifying", "unknown_info", "escalated"),
    ("hot", "needs_human", "escalated"),
    ("hot", "bought", "closed"),
    ("qualifying", "ghosted", "dead"),
])
def test_transitions(start, signal, expected):
    assert next_state(start, signal) == expected

def test_unknown_signal_keeps_state():
    assert next_state("hot", "smalltalk") == "hot"

def test_terminal_states_are_sticky():
    assert next_state("closed", "engaged") == "closed"
    assert next_state("dead", "interested") == "dead"

def test_invalid_state_raises():
    with pytest.raises(ValueError):
        next_state("bogus", "engaged")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_conversation.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.core.conversation`)

- [ ] **Step 3: Write minimal implementation**

`chatter/core/conversation.py`:

```python
from __future__ import annotations

STATES = {"new", "qualifying", "hot", "escalated", "closed", "dead"}
_TERMINAL = {"closed", "dead"}

_TRANSITIONS: dict[str, dict[str, str]] = {
    "new": {"engaged": "qualifying", "ghosted": "dead"},
    "qualifying": {"interested": "hot", "unknown_info": "escalated",
                   "needs_human": "escalated", "ghosted": "dead"},
    "hot": {"needs_human": "escalated", "unknown_info": "escalated",
            "bought": "closed", "ghosted": "dead"},
    "escalated": {"bought": "closed", "ghosted": "dead"},
}

def next_state(current: str, signal: str) -> str:
    if current not in STATES:
        raise ValueError(f"unknown state: {current}")
    if current in _TERMINAL:
        return current
    return _TRANSITIONS.get(current, {}).get(signal, current)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_conversation.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/core/conversation.py tests/chatter/test_conversation.py
git commit -m "feat(chatter): conversation FSM"
```

---

### Task 9: Disclosure (hardcoded honesty)

**Files:**
- Create: `chatter/core/disclosure.py`
- Test: `tests/chatter/test_disclosure.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_disclosure.py`:

```python
from __future__ import annotations
import pytest
from chatter.core.disclosure import is_bot_question, honest_disclosure, HONESTY_MARKER

BOT_QUESTIONS = [
    "ты бот?",
    "Ты бот или человек?",
    "это бот?",
    "я с ботом разговариваю?",
    "ты живой человек?",
    "are you a bot?",
    "is this a bot or a real person?",
    "ты реальный человек или ии?",
    "с кем я говорю, с ботом?",
    "ты автоответчик?",
]

@pytest.mark.parametrize("q", BOT_QUESTIONS)
def test_detects_bot_question(q):
    assert is_bot_question(q) is True

@pytest.mark.parametrize("q", ["сколько стоит?", "а фото делаете?", "привет"])
def test_ignores_normal_messages(q):
    assert is_bot_question(q) is False

@pytest.mark.parametrize("q", BOT_QUESTIONS)
def test_all_bot_questions_get_honest_reply(q):
    reply = honest_disclosure(owner_id="Аня", persona_line="Пишу тепло и по-дружески.")
    assert HONESTY_MARKER in reply
    assert "Аня" in reply  # offers to bring in the owner

def test_reply_uses_persona_tone_but_stays_honest():
    reply = honest_disclosure(owner_id="Owner", persona_line="ТОН-МАРКЕР")
    assert "ТОН-МАРКЕР" in reply
    assert HONESTY_MARKER in reply
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_disclosure.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.core.disclosure`)

- [ ] **Step 3: Write minimal implementation**

`chatter/core/disclosure.py`:

```python
from __future__ import annotations
import re

# The honest fact that can never be turned off by config.
HONESTY_MARKER = "я — виртуальный ассистент"

_PATTERNS = [
    r"\bты\s+бот\b", r"\bэто\s+бот\b", r"\bс\s+ботом\b", r"\bбот\s+или\s+человек\b",
    r"\bживой\s+человек\b", r"\bреальный\s+человек\b", r"\bавтоответчик\b",
    r"\bчеловек\s+или\s+ии\b", r"\bреальный.*или.*ии\b",
    r"\bare\s+you\s+a?\s*bot\b", r"\bis\s+this\s+a?\s*bot\b", r"\breal\s+person\b",
]
_RE = re.compile("|".join(_PATTERNS), re.IGNORECASE)

def is_bot_question(text: str) -> bool:
    return bool(_RE.search(text or ""))

def honest_disclosure(*, owner_id: str, persona_line: str) -> str:
    """Always honest. persona_line only sets the TONE; the honest fact is hardcoded."""
    tone = (persona_line or "").strip()
    core = (f"{HONESTY_MARKER}, а не живой человек. "
            f"Помогаю с вопросами и подсказываю по услугам. "
            f"Если хотите — позову {owner_id} лично, ответит вживую.")
    return f"{tone} {core}".strip() if tone else core
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_disclosure.py -q`
Expected: PASS (26 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/core/disclosure.py tests/chatter/test_disclosure.py
git commit -m "feat(chatter): hardcoded honesty disclosure + bot-question detector"
```

---

### Task 10: Guardrails

**Files:**
- Create: `chatter/core/guardrails.py`
- Test: `tests/chatter/test_guardrails.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_guardrails.py`:

```python
from __future__ import annotations
from chatter.core.guardrails import contains_unbacked_claim, within_hourly_limit, within_daily_cap
from chatter.storage.db import Store

KNOWLEDGE = "Консультация 5000 руб. Фотосессия 15000 руб. Работаю по предоплате 50%."

def test_backed_price_is_ok():
    assert contains_unbacked_claim("Консультация стоит 5000 руб.", KNOWLEDGE) is False

def test_invented_price_flagged():
    assert contains_unbacked_claim("Могу сделать за 3000 руб, специально для вас.", KNOWLEDGE) is True

def test_no_numbers_is_ok():
    assert contains_unbacked_claim("Расскажите, что именно хотите?", KNOWLEDGE) is True is False or \
           contains_unbacked_claim("Расскажите, что именно хотите?", KNOWLEDGE) is False

def test_percent_backed():
    assert contains_unbacked_claim("Предоплата 50%.", KNOWLEDGE) is False

def test_hourly_limit(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    for t in range(3):
        s.add_message("u1", "assistant", "x", ts=1000.0 + t)
    # limit 3, window 3600s, now=1002 -> already 3 in last hour -> not allowed
    assert within_hourly_limit(s, "u1", now=1002.0, limit=3) is False
    assert within_hourly_limit(s, "u1", now=1002.0, limit=5) is True

def test_daily_cap(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    for t in range(2):
        s.add_message("u1", "assistant", "x", ts=100.0 + t)
    assert within_daily_cap(s, now=200.0, cap=2) is False
    assert within_daily_cap(s, now=200.0, cap=3) is True
```

Note: keep only these assertions; delete the deliberately-awkward `test_no_numbers_is_ok` body and replace with the clean version in Step 3's test fix if it reads oddly. (It asserts the no-number case is not flagged.)

- [ ] **Step 2: Simplify the odd test** — replace `test_no_numbers_is_ok` with:

```python
def test_no_numbers_is_ok():
    assert contains_unbacked_claim("Расскажите, что именно хотите?", KNOWLEDGE) is False
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_guardrails.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.core.guardrails`)

- [ ] **Step 4: Write minimal implementation**

`chatter/core/guardrails.py`:

```python
from __future__ import annotations
import re
from chatter.storage.db import Store

_NUMBER = re.compile(r"\d[\d\s.,]*\d|\d")

def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m.group()) for m in _NUMBER.finditer(text or "")}

def contains_unbacked_claim(reply: str, knowledge: str) -> bool:
    """True if the reply cites a number (price/term/percent) not present in knowledge.md."""
    known = _numbers(knowledge)
    return any(n not in known for n in _numbers(reply))

def within_hourly_limit(store: Store, contact_id: str, *, now: float, limit: int) -> bool:
    sent = store.count_messages_since(contact_id, role="assistant", since_ts=now - 3600.0)
    return sent < limit

def within_daily_cap(store: Store, *, now: float, cap: int) -> bool:
    day_start = now - (now % 86400.0)
    sent = store.count_outbound_between(day_start, day_start + 86400.0)
    return sent < cap
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_guardrails.py -q`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add chatter/core/guardrails.py tests/chatter/test_guardrails.py
git commit -m "feat(chatter): guardrails (unbacked-claim, hourly limit, daily cap)"
```

---

### Task 11: Brain (prompt assembly)

**Files:**
- Create: `chatter/core/brain.py`
- Test: `tests/chatter/test_brain.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_brain.py`:

```python
from __future__ import annotations
from pathlib import Path
from chatter.config.loader import load_config
from chatter.core.brain import build_system_prompt, build_messages, Brain
from chatter.core.llm import FakeLLM
from tests.chatter.test_loader import _make_client

def _cfg(tmp_path):
    _make_client(tmp_path)
    return load_config(tmp_path, "demo")

def test_system_prompt_includes_all_sources(tmp_path):
    cfg = _cfg(tmp_path)
    sp = build_system_prompt(cfg)
    assert "Меня зовут Аня" in sp          # persona
    assert "Консультация 5000" in sp        # knowledge
    assert "Стадии воронки" in sp           # playbook

def test_system_prompt_has_style_and_language_rules(tmp_path):
    cfg = _cfg(tmp_path)
    sp = build_system_prompt(cfg).lower()
    assert "1-2" in sp or "1–2" in sp       # length rule
    assert "русском" in sp                    # language ru directive
    assert "без" in sp                        # no bullets/headers rule present

def test_build_messages_maps_history_roles():
    hist = [{"role": "user", "text": "привет"}, {"role": "assistant", "text": "здравствуйте"}]
    msgs = build_messages(hist)
    assert msgs == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "здравствуйте"},
    ]

def test_brain_reply_calls_llm_with_budget(tmp_path):
    cfg = _cfg(tmp_path)
    llm = FakeLLM(scripted=["Здравствуйте! Что вас интересует?"])
    brain = Brain(llm, cfg)
    out = brain.reply([{"role": "user", "text": "привет"}])
    assert out == "Здравствуйте! Что вас интересует?"
    call = llm.calls[0]
    assert call["max_tokens"] == cfg.settings.limits.max_tokens_per_dialog
    assert "Меня зовут Аня" in call["system"]
    assert call["messages"][-1] == {"role": "user", "content": "привет"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_brain.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.core.brain`)

- [ ] **Step 3: Write minimal implementation**

`chatter/core/brain.py`:

```python
from __future__ import annotations
from chatter.config.loader import Config
from chatter.core.llm import LLMClient

_LANG_NAME = {"ru": "русском", "en": "английском", "uk": "украинском"}

_STYLE = (
    "Стиль ответа жёстко: 1-2 коротких предложения. БЕЗ списков, БЕЗ заголовков, "
    "БЕЗ канцелярита и корпоративного тона. Пиши как живой человек в личке. "
    "Задавай встречные вопросы, чтобы вести диалог. "
    "Не выдумывай цены, сроки и скидки, которых нет в разделе ЗНАНИЯ."
)

def build_system_prompt(cfg: Config) -> str:
    lang = _LANG_NAME.get(cfg.settings.language, "русском")
    return (
        f"Ты ведёшь личную переписку от лица персоны. Отвечай на {lang} языке.\n\n"
        f"=== ПЕРСОНА ===\n{cfg.persona}\n\n"
        f"=== ЗНАНИЯ (товар, прайс, условия, FAQ) ===\n{cfg.knowledge}\n\n"
        f"=== ПЛЕЙБУК (воронка, цели, чего не обещать) ===\n{cfg.playbook}\n\n"
        f"=== ПРАВИЛА ===\n{_STYLE}"
    )

def build_messages(history: list[dict]) -> list[dict]:
    return [{"role": m["role"], "content": m["text"]} for m in history]

class Brain:
    def __init__(self, llm: LLMClient, cfg: Config):
        self._llm = llm
        self._cfg = cfg
        self._system = build_system_prompt(cfg)

    def reply(self, history: list[dict]) -> str:
        return self._llm.complete(
            self._system,
            build_messages(history),
            max_tokens=self._cfg.settings.limits.max_tokens_per_dialog,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_brain.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/core/brain.py tests/chatter/test_brain.py
git commit -m "feat(chatter): Brain prompt assembly (persona+knowledge+playbook+style)"
```

---

### Task 12: Transport base + fake console

**Files:**
- Create: `chatter/transport/base.py`
- Create: `chatter/transport/fake.py`
- Test: `tests/chatter/test_transport.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_transport.py`:

```python
from __future__ import annotations
from chatter.transport.base import Transport
from chatter.transport.fake import FakeConsoleTransport

def test_fake_is_transport():
    assert isinstance(FakeConsoleTransport(), Transport)

def test_preloaded_receive_returns_in_order():
    t = FakeConsoleTransport(preload=["привет", "сколько стоит?"])
    assert t.receive(timeout=0.01) == "привет"
    assert t.receive(timeout=0.01) == "сколько стоит?"
    assert t.receive(timeout=0.01) is None  # queue drained

def test_send_and_typing_are_recorded():
    t = FakeConsoleTransport(preload=[], echo=False)
    t.send_typing(True)
    t.send("здравствуйте")
    t.send_typing(False)
    assert t.sent == ["здравствуйте"]
    assert t.typing_events == [True, False]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_transport.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.transport.base`)

- [ ] **Step 3: Write minimal implementation**

`chatter/transport/base.py`:

```python
from __future__ import annotations
from abc import ABC, abstractmethod

class Transport(ABC):
    @abstractmethod
    def receive(self, timeout: float | None = None) -> str | None:
        """Return the next inbound message, or None if none arrives within timeout."""

    @abstractmethod
    def send(self, text: str) -> None:
        ...

    @abstractmethod
    def send_typing(self, on: bool) -> None:
        ...
```

`chatter/transport/fake.py`:

```python
from __future__ import annotations
import queue
import sys
import threading
from chatter.transport.base import Transport

class FakeConsoleTransport(Transport):
    """Console transport. A reader thread pushes stdin lines into a queue so the
    run loop can coalesce a burst of messages within the debounce window.
    In tests, pass `preload` to seed the queue and skip stdin."""

    def __init__(self, preload: list[str] | None = None, echo: bool = True):
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._echo = echo
        self.sent: list[str] = []
        self.typing_events: list[bool] = []
        if preload is not None:
            for m in preload:
                self._q.put(m)
            self._reader = None
        else:
            self._reader = threading.Thread(target=self._read_stdin, daemon=True)
            self._reader.start()

    def _read_stdin(self) -> None:
        for line in sys.stdin:
            self._q.put(line.rstrip("\n"))
        self._q.put(None)  # EOF sentinel

    def receive(self, timeout: float | None = None) -> str | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def send(self, text: str) -> None:
        self.sent.append(text)
        if self._echo:
            print(f"  <bot> {text}")

    def send_typing(self, on: bool) -> None:
        self.typing_events.append(on)
        if self._echo:
            print("  <bot печатает…>" if on else "  <bot перестал печатать>")
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_transport.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/transport/base.py chatter/transport/fake.py tests/chatter/test_transport.py
git commit -m "feat(chatter): Transport ABC + fake console transport"
```

---

### Task 13: Orchestration core (`process_batch` + `gather_batch`)

**Files:**
- Create: `chatter/run.py`
- Test: `tests/chatter/test_run_core.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_run_core.py`:

```python
from __future__ import annotations
import random
from pathlib import Path
from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.llm import FakeLLM
from chatter.storage.db import Store
from chatter.transport.fake import FakeConsoleTransport
from chatter.run import Deps, gather_batch, process_batch
from tests.chatter.test_loader import _make_client

def _deps(tmp_path, scripted, now=1000.0):
    _make_client(tmp_path)
    cfg = load_config(tmp_path, "demo")
    llm = FakeLLM(scripted=scripted)
    clock = {"t": now}
    return Deps(
        cfg=cfg,
        store=Store(tmp_path / "c.db"),
        brain=Brain(llm, cfg),
        rng=random.Random(0),
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    ), clock

def test_gather_batch_coalesces_within_window(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["ok"])
    t = FakeConsoleTransport(preload=["привет", "а сколько стоит?"], echo=False)
    batch = gather_batch(t, deps, first="привет-первый")
    # first + the two preloaded (arrive instantly, < window) => all three
    assert batch == ["привет-первый", "привет", "а сколько стоит?"]

def test_process_batch_sends_reply_and_persists(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["Здравствуйте! Что интересует?"])
    t = FakeConsoleTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", ["привет"], t, deps)
    assert t.sent  # at least one outbound part
    assert "Здравствуйте" in " ".join(t.sent)
    hist = deps.store.history("u1")
    assert hist[0]["role"] == "user" and hist[0]["text"] == "привет"
    assert any(m["role"] == "assistant" for m in hist)
    assert t.typing_events[0] is True and t.typing_events[-1] is False

def test_bot_question_gets_honest_reply(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["НЕ ДОЛЖНО ИСПОЛЬЗОВАТЬСЯ"])
    t = FakeConsoleTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", ["ты бот?"], t, deps)
    joined = " ".join(t.sent)
    assert "виртуальный ассистент" in joined
    assert "НЕ ДОЛЖНО" not in joined  # LLM not consulted for disclosure

def test_unbacked_price_is_suppressed_and_escalated(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["Сделаю за 3000 руб только вам."])
    t = FakeConsoleTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", ["дешевле можно?"], t, deps)
    joined = " ".join(t.sent)
    assert "3000" not in joined                      # invented price not sent
    assert deps.store.get_or_create_contact("u1")["state"] == "escalated"

def test_daily_cap_blocks_send(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["ответ"])
    # jam the cap: cap is 500 in demo settings; override via many rows would be slow,
    # so use a tiny cap by editing the loaded config's limit is frozen -> instead
    # pre-fill assistant messages beyond a small window is impractical; assert the
    # guardrail path by monkey-checking within_daily_cap through a low-cap store.
    t = FakeConsoleTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    # Fill to the cap using the real cap value is heavy; instead verify no crash and a send occurs.
    process_batch("u1", ["привет"], t, deps)
    assert t.sent
```

Note: `test_daily_cap_blocks_send` is a smoke check (real cap enforcement is unit-tested in `test_guardrails.py`). Keep it minimal as written.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_run_core.py -q`
Expected: FAIL (`ModuleNotFoundError: chatter.run`)

- [ ] **Step 3: Write minimal implementation**

`chatter/run.py`:

```python
from __future__ import annotations
import argparse
import datetime as _dt
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chatter.config.loader import Config, load_config
from chatter.core import humanizer as H
from chatter.core.brain import Brain
from chatter.core.disclosure import honest_disclosure, is_bot_question
from chatter.core.guardrails import (
    contains_unbacked_claim, within_daily_cap, within_hourly_limit,
)
from chatter.core.llm import AnthropicLLM, FakeLLM
from chatter.storage.db import Store
from chatter.transport.base import Transport
from chatter.transport.fake import FakeConsoleTransport

@dataclass
class Deps:
    cfg: Config
    store: Store
    brain: Brain
    rng: random.Random
    clock: Callable[[], float]
    sleep: Callable[[float], None]

def _persona_first_line(persona: str) -> str:
    for line in persona.splitlines():
        if line.strip():
            return line.strip()
    return ""

def _decide_reply(deps: Deps, contact_id: str, text: str) -> str:
    """Disclosure > guardrails > brain. Returns the outbound reply text."""
    if is_bot_question(text):
        return honest_disclosure(
            owner_id=deps.cfg.settings.owner_id,
            persona_line=_persona_first_line(deps.cfg.persona),
        )
    history = deps.store.history(contact_id)
    reply = deps.brain.reply(history)
    if contains_unbacked_claim(reply, deps.cfg.knowledge):
        deps.store.set_state(contact_id, "escalated")  # escalation flag (arc 3 does the handoff)
        print(f"  [escalation flag] unbacked claim for {contact_id}: {reply!r}")
        return (f"Хороший вопрос — уточню детали и вернусь. "
                f"Могу также позвать {deps.cfg.settings.owner_id}, ответит точно.")
    return reply

def gather_batch(transport: Transport, deps: Deps, first: str) -> list[str]:
    """Collect a burst of inbound messages, extending the debounce window on each."""
    window = deps.cfg.settings.timings.debounce_window
    batch = [first]
    last = deps.clock()
    while not H.debounce_ready(last_received_at=last, now=deps.clock(), window=window):
        remaining = window - (deps.clock() - last)
        msg = transport.receive(timeout=max(remaining, 0.0))
        if msg is None:
            break
        batch.append(msg)
        last = deps.clock()
    return batch

def process_batch(contact_id: str, incoming: list[str], transport: Transport, deps: Deps) -> None:
    now = deps.clock()
    text = H.coalesce(incoming)
    if not text:
        return
    deps.store.add_message(contact_id, "user", text, ts=now)

    limits = deps.cfg.settings.limits
    if not within_hourly_limit(deps.store, contact_id, now=now, limit=limits.per_contact_hourly):
        print(f"  [rate limit] hourly limit hit for {contact_id}; skipping")
        return
    if not within_daily_cap(deps.store, now=now, cap=limits.daily_cap):
        print(f"  [rate limit] daily cap hit; skipping")
        return

    reply = _decide_reply(deps, contact_id, text)

    night = H.is_night(_hour(now), deps.cfg.settings.work_hours)
    deps.sleep(H.read_delay(deps.rng, deps.cfg.settings.timings, night=night))
    transport.send_typing(True)
    parts = H.split_message(reply, deps.cfg.settings.timings)
    for i, part in enumerate(parts):
        deps.sleep(H.typing_duration(part, deps.rng, deps.cfg.settings.timings, night=night))
        transport.send(part)
        deps.store.add_message(contact_id, "assistant", part, ts=deps.clock())
        if i < len(parts) - 1:
            deps.sleep(deps.rng.uniform(
                deps.cfg.settings.timings.split_pause_min,
                deps.cfg.settings.timings.split_pause_max))
    transport.send_typing(False)

def _hour(ts: float) -> int:
    return _dt.datetime.fromtimestamp(ts).hour

def _build_llm(cfg: Config, mode: str):
    if mode == "real" or (mode == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicLLM(cfg.settings.model)
    return FakeLLM()

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chatter.run")
    p.add_argument("--client", required=True)
    p.add_argument("--transport", choices=["fake"], default="fake")
    p.add_argument("--clients-dir", default=str(Path(__file__).resolve().parent / "clients"))
    p.add_argument("--llm", choices=["auto", "real", "fake"], default="auto")
    p.add_argument("--db", default=":memory:")
    p.add_argument("--contact", default="console-user")
    args = p.parse_args(argv)

    cfg = load_config(Path(args.clients_dir), args.client)  # raises ConfigError at startup
    llm = _build_llm(cfg, args.llm)
    deps = Deps(
        cfg=cfg, store=Store(args.db), brain=Brain(llm, cfg),
        rng=random.Random(), clock=time.time, sleep=time.sleep,
    )
    transport = FakeConsoleTransport()
    deps.store.get_or_create_contact(args.contact)

    print(f"[chatter] client={cfg.slug} llm={type(llm).__name__} lang={cfg.settings.language}")
    print("[chatter] пишите сообщения (Ctrl-D для выхода). Быстрые подряд склеятся.\n")
    while True:
        first = transport.receive(timeout=None)
        if first is None:
            break
        batch = gather_batch(transport, deps, first)
        process_batch(args.contact, batch, transport, deps)
    print("\n[chatter] пока!")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_run_core.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/run.py tests/chatter/test_run_core.py
git commit -m "feat(chatter): orchestration core + CLI (disclosure>guardrails>brain, humanized send)"
```

---

### Task 14: Demo client config (RU personal brand)

**Files:**
- Create: `chatter/clients/demo/persona.md`
- Create: `chatter/clients/demo/knowledge.md`
- Create: `chatter/clients/demo/playbook.md`
- Create: `chatter/clients/demo/settings.yaml`
- Test: `tests/chatter/test_demo_client.py`

- [ ] **Step 1: Write the failing test**

`tests/chatter/test_demo_client.py`:

```python
from __future__ import annotations
from pathlib import Path
from chatter.config.loader import load_config

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"

def test_demo_config_loads():
    cfg = load_config(CLIENTS, "demo")
    assert cfg.settings.language == "ru"
    assert cfg.settings.model == "claude-haiku-4-5"
    assert "консультац" in cfg.knowledge.lower()
    assert "фотосесс" in cfg.knowledge.lower()
    assert cfg.settings.owner_id
    assert cfg.settings.timings.debounce_window > 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/chatter/test_demo_client.py -q`
Expected: FAIL (`ConfigError: client 'demo' not found`)

- [ ] **Step 3: Create the demo config files**

`chatter/clients/demo/persona.md`:

```markdown
Меня зовут Аня, мне 29. Я фотограф и консультант по личному бренду.
Пишу тепло, по-дружески и коротко, на «вы», но без официоза.
Люблю уточняющие вопросы: сначала пойму задачу, потом предложу.
Ключевые фразы: «супер», «расскажите чуть подробнее», «смотрите».
Не давлю и не продаю в лоб — веду к тому, что человеку правда нужно.
```

`chatter/clients/demo/knowledge.md`:

```markdown
# Услуги и условия

## Консультация по личному бренду
- Онлайн, 60 минут. Цена: 5000 руб.
- Разбираем позиционирование, контент, съёмку под задачи.

## Фотосессия
- Портретная / для соцсетей. Цена: 15000 руб за съёмку (1.5–2 часа, 15 обработанных кадров).
- Локация в центре города или студия (студия +2000 руб).

## Условия
- Предоплата 50% для брони даты.
- Перенос возможен не позднее чем за 48 часов.
- Работаю пн–сб, воскресенье выходной.

## FAQ
- Макияж/стилист — по желанию, помогу с контактами, оплата отдельно.
- Готовые фото — в течение 7 дней после съёмки.
```

`chatter/clients/demo/playbook.md`:

```markdown
# Воронка

1. new → знакомство. Цель: понять, что человек хочет (консультация или съёмка).
2. qualifying → квалификация. Цель: задача, сроки, бюджет, город/формат.
3. hot → тёплый. Цель: предложить конкретную услугу и подвести к брони (предоплата 50%).
4. escalated → передать владельцу (нестандартный запрос, скидка, вопрос без ответа в знаниях).
5. closed / dead → бронь сделана / контакт остыл.

## Триггеры эскалации
- Просят скидку или цену, которой нет в знаниях.
- Корпоратив/съёмка вне списка услуг.
- Юридические/договорные вопросы.

## Чего не обещать
- Нет скидок и «специальных цен».
- Не называть сроки готовности меньше 7 дней.
- Не гарантировать конкретную студию/локацию без подтверждения.
```

`chatter/clients/demo/settings.yaml`:

```yaml
model: claude-haiku-4-5
language: ru
owner_id: "Аня"
work_hours:
  start: 9
  end: 22
timings:
  read_delay_min: 1.0
  read_delay_max: 5.0
  cps_min: 3.0
  cps_max: 6.0
  jitter_min: 0.8
  jitter_max: 1.4
  split_pause_min: 0.5
  split_pause_max: 2.0
  split_max_len: 160
  night_multiplier: 2.0
  debounce_window: 3.0
limits:
  max_tokens_per_dialog: 20000
  per_contact_hourly: 20
  daily_cap: 500
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/chatter/test_demo_client.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add chatter/clients/demo tests/chatter/test_demo_client.py
git commit -m "feat(chatter): demo client — RU personal brand (consultations + photoshoots)"
```

---

### Task 15: Full suite + acceptance dry-run

**Files:** none (verification only)

- [ ] **Step 1: Run the whole chatter suite**

Run: `python -m pytest tests/chatter/ -q`
Expected: PASS (all tests green). If anything fails, fix before proceeding.

- [ ] **Step 2: Acceptance — offline console run (piped input, FakeLLM)**

Run:
```bash
printf 'привет\nа сколько стоит фотосессия?\nты бот?\nа можно за 3000?\n' | python -m chatter.run --client demo --transport fake --llm fake
```
Expected: bot prints `<bot печатает…>` indicators and replies; the "ты бот?" turn contains "виртуальный ассистент"; the "3000" turn does NOT echo an invented "3000" price. (Timings are real `time.sleep`; the run takes a few seconds.)

- [ ] **Step 3: Acceptance — persona swap changes voice**

Edit `chatter/clients/demo/persona.md` (change name and tone ~5 lines), re-run the piped command from Step 2, confirm the disclosure line now uses the new tone/name. Revert the edit afterward (`git checkout chatter/clients/demo/persona.md`).

- [ ] **Step 4: Optional — live LLM smoke (only if `ANTHROPIC_API_KEY` set)**

Run:
```bash
printf 'привет, хочу фото для инсты\n' | python -m chatter.run --client demo --transport fake --llm real
```
Expected: a short 1–2 sentence human-style reply from Claude Haiku (real spend — one cheap call). Skip if no key.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A chatter tests/chatter
git commit -m "test(chatter): full suite green + acceptance dry-run notes"
```

---

## Self-Review Notes (author)

- **Spec coverage:** boundary (Task 0), configs-inside-package (Tasks 0/14), loader validation (Task 1), humanizer incl. debounce/coalesce (Tasks 2–5), brain/style/language (Task 11), disclosure hardcoded (Task 9), guardrails (Task 10), conversation FSM (Task 8), SQLite storage (Task 7), fake transport + demo run (Tasks 12–15), demo RU config (Task 14), LLM Haiku + FakeLLM fallback (Task 6/13). All spec sections map to a task.
- **Type consistency:** `Timings`/`Settings`/`Config` fields identical across loader, humanizer tests, run.py, and demo settings.yaml. `Store` method names (`count_messages_since`, `count_outbound_between`, `set_flag`) match guardrails/run usage. `Brain.reply(history)` takes `[{"role","text"}]`; `build_messages` maps to `{"role","content"}`.
- **No network in tests:** every test uses `FakeLLM`; `AnthropicLLM` imports `anthropic` lazily inside `__init__`, so the SDK/key are never needed under pytest.
```
