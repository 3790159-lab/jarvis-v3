# Лента панели Джарвиса: четыре правки (мерж 1) — план реализации

> **Для агентных исполнителей:** ОБЯЗАТЕЛЬНЫЙ САБ-СКИЛЛ — `superpowers:subagent-driven-development`
> (рекомендуется) или `superpowers:executing-plans`. Шаги размечены чекбоксами (`- [ ]`).

**Цель:** лента событий панели Джарвиса перестаёт врать: показывает время, не
тонет в дебаунс-шуме сторожа, читает ту базу, которую реально обслуживает раннер,
не слурпает растущий вечно лог и не рисует кракозябры вместо имени базы.

**Архитектура:** четыре чистые функции в `app/services/jarvis_farm.py`
(`parse_log_ts`, `is_decision`, `read_tail`, `client_db_path`) плюс переписанная
`events()`, которая их складывает. Каждая функция тестируется без диска и без
сети; `events()` — на временных файлах. Панель (`jarvis_panel._events_table`)
меняется минимально: она уже умеет рисовать `ts`.

**Реализует:** `docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md`, §5
и §9 (мерж 1). Журнал (§2–§4) — отдельный план, отдельный мерж.

**Стек:** Python 3.14, `.venv\Scripts\python.exe`, pytest. Только stdlib + уже
используемый `pyyaml` (через `chatter.config.active`).

---

## Что важно знать до начала

**Прогон тестов — ТОЛЬКО так** (`python` в PATH — чужой venv):

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/... -q --no-header -p no:cacheprovider
```

**Живое дерево `C:/jarvis` — деплой-путь гардиана.** Он поднимает бэкенд из
того, что здесь лежит. Работаем в ветке, мержим только с отмашкой владельца.

**Кириллица в выводе.** Под Windows консоль cp1251 роняет чтение вывода pytest
на первом же кириллическом ассерте. Ставить `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`
перед командой, если нужен полный вывод.

**Панель Джарвиса — по-русски**, клиентская — по-украински
(`app/routers/panels_ui.py` держит украинский умолчанием). Все новые строки
ленты — русские.

---

## Структура файлов

| Файл | Ответственность | Действие |
|---|---|---|
| `app/services/jarvis_farm.py` | сбор фермы и источников ленты | **изменяется**: +4 функции, переписана `events()` |
| `app/routers/jarvis_panel.py` | разметка панели | **изменяется**: `_events_table` рисует источник «панель» |
| `scripts/chatter_guardian_detached.ps1` | гардиан раннера | **изменяется**: одна строка, `-Encoding utf8` |
| `tests/chatter/test_panel_feed.py` | сторожа ленты | **создаётся** |
| `scripts/mutate_panels_hierarchy.py` | мутационный гейт DEV-26 | **изменяется**: +8 мутаций |

Почему всё в `jarvis_farm.py`, а не в новом модуле: файл сегодня 330 строк и
имеет одну ответственность — «прочитать состояние фермы из живых источников».
Лента — такой же источник. Заводить пятый модуль ради четырёх функций значит
размазать одну ответственность по двум файлам.

---

### Task 1: Время из префикса строки гардиана

**Файлы:**
- Изменить: `app/services/jarvis_farm.py`
- Тест: `tests/chatter/test_panel_feed.py` (создать)

Строки гардиана несут время текстом (`2026-08-14 00:45:08 | runner DOWN - restarting`),
а код ставит `ts: None`. Без времени лента не сортируется и не читается.

- [ ] **Шаг 1: написать падающий тест**

Создать `tests/chatter/test_panel_feed.py`:

```python
"""Лента панели Джарвиса: время, фильтр решений, окно чтения, выбор базы.

Четыре дефекта, найденные на ЖИВЫХ данных 14.08 (спека
docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md, §5):
  1. база бралась литералом `.secrets/demo.db`, а раннер выводит её из
     первичного slug'а в active.yaml — при смене состава панель читала не ту
     базу и печатала «тихо»;
  2. время строк гардиана не разбиралось вовсе (`ts: None`);
  3. `read_text()` слурпал весь растущий лог ради последних 40 строк;
  4. 163 из 421 строки лога — дебаунс-шум сторожа, он же съедал окно.

Стенд намеренно кормит функции УРОДЛИВЫМИ данными: смешанные кодировки,
строки без префикса, лог длиннее окна. Опрятный стенд зеленел бы, ничего не
проверив, — весь дефект именно в уродливом.
"""
from __future__ import annotations

import time

from app.services import jarvis_farm as F


def test_the_guardian_timestamp_is_read_as_local_time():
    """Время в логе ЛОКАЛЬНОЕ и без зоны. Принять его за UTC значит сдвинуть
    события на три часа и получить «события из будущего» — поэтому сверяем
    круговым разбором через localtime, а не сравнением с константой."""
    ts = F.parse_log_ts("2026-08-14 00:45:08 | runner DOWN - restarting")
    assert ts is not None
    assert time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) == "2026-08-14 00:45:08"


def test_a_line_without_a_timestamp_gets_none_not_a_guess():
    """Прочерк честнее выдуманного времени: строка без префикса встанет в
    ленте с «—», а не притворится свежей."""
    assert F.parse_log_ts("runner DOWN - restarting") is None
    assert F.parse_log_ts("") is None
    assert F.parse_log_ts("2026-13-45 99:99:99 | битая дата") is None
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: module 'app.services.jarvis_farm' has no attribute 'parse_log_ts'`.

- [ ] **Шаг 3: минимальная реализация**

В `app/services/jarvis_farm.py` добавить `import re` к импортам вверху файла и
вставить перед функцией `events()`:

```python
# ─────────────────────── Лента: время строк гардиана ────────────────────────
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| ")


def parse_log_ts(line: str) -> float | None:
    """Время из префикса строки гардиана.

    ⚠️ Время в логе ЛОКАЛЬНОЕ и без зоны, поэтому `time.mktime` (он трактует
    struct_time как локальное), а НЕ `calendar.timegm`. Принять его за UTC
    значит сдвинуть всю ленту на три часа и получить события из будущего.

    Строка без разбираемого префикса получает None и рисуется прочерком:
    выдуманное время хуже отсутствующего — оно выглядит достоверным.
    """
    m = _LOG_TS_RE.match(line)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None
```

- [ ] **Шаг 4: убедиться, что тест зелёный**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `2 passed`.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add app/services/jarvis_farm.py tests/chatter/test_panel_feed.py
git commit -m "feat(panel): время строк гардиана разбирается как локальное"
```

---

### Task 2: Фильтр по решениям вместо супа из подстрок

**Файлы:**
- Изменить: `app/services/jarvis_farm.py`
- Тест: `tests/chatter/test_panel_feed.py`

Замер по живому логу (421 строка): 163 строки — 39% — это
`runner check failed (N/3) - debouncing, not relaunching yet`, по две на каждый
несостоявшийся инцидент. Нынешний фильтр (`"failed" in ln`) их пропускает, и в
последних 40 строках под него попадали 28, почти все шумовые.

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/chatter/test_panel_feed.py`:

```python
# Реальные виды строк из logs/chatter_guardian.stdout.log с их частотой на
# 14.08. Тест держит РЕШЕНИЕ по каждому виду, а не абстрактный «фильтр».
KEEP = [
    "2026-08-14 00:45:08 | runner DOWN - restarting",
    "2026-08-14 00:45:09 | launched chatter runner (PID 6864) -> C:\\jarvis\\logs\\chatter_volska.log",
    "2026-08-13 21:04:11 | chatter guardian started (PID 5724), heartbeat<=180s every 30s, debounce=3",
    "2026-08-12 09:10:00 | runner heartbeat NOT fresh after 45s - will retry next cycle",
    "2026-08-14 00:40:00 | Состав: CHATTER_PERSONAS=volska (файл), db=.secrets\\demo.db",
]
DROP = [
    "2026-08-14 00:44:08 | runner check failed (1/3) - debouncing, not relaunching yet",
    "2026-08-14 00:44:38 | runner check failed (2/3) - debouncing, not relaunching yet",
    "2026-08-14 00:45:10 | runner heartbeat fresh after ~0s",
    "2026-08-13 22:00:00 | runner alive",
]


def test_decisions_of_the_guardian_reach_the_feed():
    for line in KEEP:
        assert F.is_decision(line), f"решение выброшено из ленты: {line}"


def test_the_guardians_own_debounce_noise_never_reaches_the_feed():
    """163 из 421 строки лога — дебаунс. Он и съедал окно: настоящие
    DOWN/launched вытеснялись собственным шумом сторожа."""
    for line in DROP:
        assert not F.is_decision(line), f"шум попал в ленту: {line}"


def test_the_composition_line_survives_because_it_names_the_database():
    """Парный к дефекту выбора базы: строка «Состав: … db=…» — единственное
    место, где лог прямо называет активную базу. Выбросить её значит оставить
    слепое пятно ровно там, где мы его чиним."""
    line = "2026-08-14 00:40:00 | Состав: CHATTER_PERSONAS=volska (файл), db=.secrets\\demo.db"
    assert F.is_decision(line)
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: ... has no attribute 'is_decision'`.

- [ ] **Шаг 3: минимальная реализация**

В `app/services/jarvis_farm.py` сразу под `parse_log_ts`:

```python
# Что из лога гардиана считается СОБЫТИЕМ, а что — его собственным шумом.
#
# Замер 14.08 по живому логу (421 строка): 163 строки, то есть 39%, — это
# «runner check failed (N/3) - debouncing», по две на каждый несостоявшийся
# инцидент. Прежний фильтр искал подстроки `DOWN`/`launched`/`failed`, ловил
# дебаунс целиком, и в последних 40 строках под него попадали 28 — почти все
# шумовые. Настоящие падения вытеснялись с экрана шумом сторожа.
#
# Список ИМЕНОВАННЫЙ намеренно: новый вид строки не попадёт в ленту молча —
# его придётся добавить сюда осознанно. Суп из подстрок делал обратное.
GUARDIAN_DECISIONS = (
    "runner DOWN",                 # решение поднимать
    "launched chatter runner",     # подъём состоялся
    "chatter guardian started",    # рестарт самого сторожа
    "heartbeat NOT fresh",         # настоящий провал подъёма
    "CHATTER_PERSONAS=",           # строка «Состав: … db=…», называет активную базу
)
# Строки, которые под маску решения попадают, но событием не являются.
# Проверяется ПЕРВЫМ: «runner check failed» содержит и шум, и слово из маски.
GUARDIAN_NOISE = ("debouncing", "runner alive", "heartbeat fresh after")


def is_decision(line: str) -> bool:
    """Решение гардиана против его же дебаунс-шума."""
    if any(noise in line for noise in GUARDIAN_NOISE):
        return False
    return any(mark in line for mark in GUARDIAN_DECISIONS)
```

- [ ] **Шаг 4: убедиться, что тест зелёный**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `5 passed`.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add app/services/jarvis_farm.py tests/chatter/test_panel_feed.py
git commit -m "feat(panel): лента берёт решения гардиана, а не его дебаунс-шум"
```

---

### Task 3: Ограниченное чтение лога + фолбэк кодировки

**Файлы:**
- Изменить: `app/services/jarvis_farm.py`
- Тест: `tests/chatter/test_panel_feed.py`

Сегодня `read_text()` слурпает **весь** файл на каждый запрос, а ротации у
гардиана нет вовсе — лог растёт вечно. Плюс часть строк он пишет в cp1251, и
панель рисует их кракозябрами.

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/chatter/test_panel_feed.py`:

```python
def test_a_utf8_log_is_read_as_utf8(tmp_path):
    """Парный сторож к фолбэку. cp1251 декодирует ЛЮБОЙ байт и никогда не
    бросит — поставь его первым, и нормальный utf-8 молча станет мусором,
    причём выглядеть это будет как «так и было в логе»."""
    p = tmp_path / "g.log"
    p.write_text("2026-08-14 00:45:08 | Состав: CHATTER_PERSONAS=volska\n",
                 encoding="utf-8")
    lines, truncated = F.read_tail(p)
    assert truncated is False
    assert "Состав: CHATTER_PERSONAS=volska" in lines[0]


def test_a_cp1251_log_is_still_readable(tmp_path):
    """-Encoding utf8 в гардиане чинит только БУДУЩИЕ строки. Прошлое
    чинится фолбэком при чтении — иначе строка, называющая активную базу,
    остаётся нечитаемой навсегда."""
    p = tmp_path / "g.log"
    p.write_bytes("2026-08-14 00:40:00 | Состав: CHATTER_PERSONAS=volska, db=.secrets\\demo.db\n"
                  .encode("cp1251"))
    lines, truncated = F.read_tail(p)
    assert "Состав: CHATTER_PERSONAS=volska" in lines[0], lines[0]
    assert "\ufffd" not in lines[0], "фолбэк не сработал, строка испорчена"


def test_a_log_longer_than_the_window_is_read_from_the_end(tmp_path):
    """Ротации у гардиана нет — лог растёт вечно, и чтение целиком дорожает
    каждый день. Читаем хвост и ГОВОРИМ, что файл длиннее окна."""
    p = tmp_path / "g.log"
    p.write_text("".join(f"2026-08-14 00:{i % 60:02d}:00 | строка {i}\n"
                         for i in range(4000)), encoding="utf-8")
    lines, truncated = F.read_tail(p, limit=2048)
    assert truncated is True
    assert len(lines) < 4000
    assert "строка 3999" in lines[-1]


def test_the_first_partial_line_of_the_window_is_dropped(tmp_path):
    """Срез по байтам рассекает строку посередине. Обрубок в ленте выглядит
    как настоящее событие с потерянным началом."""
    p = tmp_path / "g.log"
    p.write_text("A" * 3000 + "\n2026-08-14 00:45:08 | runner DOWN - restarting\n",
                 encoding="utf-8")
    lines, truncated = F.read_tail(p, limit=1024)
    assert truncated is True
    assert not any(set(line) == {"A"} for line in lines), lines


def test_a_missing_log_is_not_an_exception(tmp_path):
    """Лог может отсутствовать на машине без chatter. Это пустая лента, а не
    падение страницы."""
    assert F.read_tail(tmp_path / "нет-такого.log") == ([], False)
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: ... has no attribute 'read_tail'`.

- [ ] **Шаг 3: минимальная реализация**

В `app/services/jarvis_farm.py` сразу под `is_decision`:

```python
# Сколько байт хвоста лога читаем. Ротации у chatter_guardian_detached.ps1 нет
# ВООБЩЕ (проверено 14.08: ни Clear-Content, ни лимита), лог растёт вечно, и
# `read_text()` дорожал бы с каждым днём. 64 КБ — это порядка 700 строк лога,
# заведомо больше окна ленты и заведомо дёшево.
GUARDIAN_LOG_WINDOW = 64 * 1024


def read_tail(path, limit: int = GUARDIAN_LOG_WINDOW) -> tuple[list[str], bool]:
    """(строки хвоста, файл_длиннее_окна).

    КОДИРОВКА: utf-8, при провале — cp1251 на ВЕСЬ блок. Порядок не
    переставляется: cp1251 декодирует любой байт и никогда не бросает
    исключение, поэтому первым он молча превратил бы нормальный utf-8 в мусор,
    и выглядело бы это как «так и было в логе».

    Фолбэк на блок, а не на строку: смешанный файл прочтётся как cp1251
    целиком, и это лучше ровного ряда `�` от errors="replace" — cp1251 верно
    читает латиницу, цифры и пути, то есть `CHATTER_PERSONAS`, PID и db=.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > limit:
                f.seek(size - limit)
            raw = f.read()
    except OSError:
        return [], False

    truncated = size > limit
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")

    lines = text.splitlines()
    if truncated and lines:
        # Срез по байтам рассекает строку посередине: обрубок в ленте выглядит
        # как событие с потерянным началом.
        lines = lines[1:]
    return lines, truncated
```

- [ ] **Шаг 4: убедиться, что тест зелёный**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `10 passed`.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add app/services/jarvis_farm.py tests/chatter/test_panel_feed.py
git commit -m "feat(panel): хвост лога вместо слурпа + фолбэк utf-8 -> cp1251"
```

---

### Task 4: Путь к базе — из `active.yaml`, а не литералом

**Файлы:**
- Изменить: `app/services/jarvis_farm.py`
- Тест: `tests/chatter/test_panel_feed.py`

Раннер берёт `.secrets/<первичный slug>.db`, где первичный — первый в
`chatter/clients/active.yaml`. Панель прибила путь литералом `.secrets/demo.db`.
14.08 во время демо Ярины первичным был `yarina`; рядом до сих пор лежит
`.secrets/yarina.db` с нулём `control_events`.

Порядок приоритетов повторяет раннер (`chatter/config/active.py`,
`chatter/telethon_run.py:resolve_runtime_paths`): явный env > состав > вывод из slug'а.

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/chatter/test_panel_feed.py`:

```python
def _clients_dir(tmp_path, *slugs):
    d = tmp_path / "chatter" / "clients"
    d.mkdir(parents=True)
    (d / "active.yaml").write_text(
        "clients:\n" + "".join(f"  - {s}\n" for s in slugs), encoding="utf-8")
    return d


def test_the_database_follows_the_primary_slug(tmp_path, monkeypatch):
    """Первичный slug МЕНЯЕТСЯ: 14.08 во время демо Ярины он был `yarina`.
    Панель с прибитым литералом в такой момент читает не ту базу и печатает
    «тихо» вместо ленты — тишина, неотличимая от здоровья."""
    _clients_dir(tmp_path, "yarina", "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.delenv("TAMAPI_DB", raising=False)
    monkeypatch.delenv("CHATTER_DB", raising=False)
    monkeypatch.delenv("CHATTER_PERSONAS", raising=False)
    path, note = F.client_db_path()
    assert note == ""
    assert path.endswith("yarina.db"), path


def test_an_explicit_env_database_still_wins(tmp_path, monkeypatch):
    """На TAMAPI_DB стоит демо-стенд (scripts/panels_demo.py). Отобрать у него
    приоритет значит сломать стенд приёмки."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setenv("TAMAPI_DB", "state/panels_demo.db")
    assert F.client_db_path() == ("state/panels_demo.db", "")


def test_a_broken_composition_says_so_instead_of_falling_back_silently(tmp_path, monkeypatch):
    """Тихий откат на demo.db И ЕСТЬ починяемый дефект: панель показала бы
    ленту чужой базы и назвала бы её текущей."""
    d = tmp_path / "chatter" / "clients"
    d.mkdir(parents=True)
    (d / "active.yaml").write_text("clients: [](((битый", encoding="utf-8")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.delenv("TAMAPI_DB", raising=False)
    monkeypatch.delenv("CHATTER_DB", raising=False)
    monkeypatch.delenv("CHATTER_PERSONAS", raising=False)
    path, note = F.client_db_path()
    assert path is None
    assert "не прочитан" in note, note
    assert "demo.db" not in note
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: ... has no attribute 'client_db_path'`.

- [ ] **Шаг 3: минимальная реализация**

В `app/services/jarvis_farm.py` сразу под `read_tail`:

```python
def client_db_path() -> tuple[str | None, str]:
    """(путь к базе клиентов, пояснение об ошибке).

    Путь ВЫВОДИТСЯ так же, как его выводит раннер, а не пишется литералом:
    `chatter.config.active.resolve_personas` → первый slug первичный →
    `.secrets/<slug>.db` (см. `chatter/telethon_run.py:derive_db_path`).

    Первичный slug МЕНЯЕТСЯ. 14.08 во время демо Ярины он был `yarina`, и
    рядом до сих пор лежит `.secrets/yarina.db` с нулём control_events. Панель
    с литералом в такой момент читает не ту базу и печатает «тихо» — тишину,
    неотличимую от здоровья.

    Импорт ЛЕНИВЫЙ и внутри функции — как в `tamapi_dashboard`: сломанное
    дерево chatter не имеет права уронить импорт панели фермы.

    Ошибка чтения состава — ЯВНАЯ строка, а не тихий откат на demo.db: тихий
    откат и есть починяемый дефект.
    """
    env_db = os.getenv("TAMAPI_DB") or os.getenv("CHATTER_DB")
    if env_db:
        return env_db, ""
    try:
        from chatter.config.active import resolve_personas
        slugs = resolve_personas(clients_dir=ROOT / "chatter" / "clients",
                                 env=os.environ)
    except Exception as exc:                       # noqa: BLE001 — источник внешний
        return None, (f"склад клиентов не прочитан ({type(exc).__name__}: {exc}) "
                      f"— какую базу читать, неизвестно")
    return str(ROOT / ".secrets" / f"{slugs[0]}.db"), ""
```

- [ ] **Шаг 4: убедиться, что тест зелёный**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `13 passed`.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add app/services/jarvis_farm.py tests/chatter/test_panel_feed.py
git commit -m "feat(panel): база ленты выводится из active.yaml, а не литералом"
```

---

### Task 5: Собрать `events()` из четырёх функций

**Файлы:**
- Изменить: `app/services/jarvis_farm.py` (функция `events`)
- Тест: `tests/chatter/test_panel_feed.py`

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/chatter/test_panel_feed.py`:

```python
def _log(tmp_path, text: str):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    p = logs / "chatter_guardian.stdout.log"
    p.write_text(text, encoding="utf-8")
    return p


def test_the_feed_sorts_by_time_and_carries_it(tmp_path, monkeypatch):
    """До правки строки гардиана шли с ts=None и вставали в конец кучей.
    Со временем лента наконец читается как лента."""
    _log(tmp_path, "2026-08-14 00:45:08 | runner DOWN - restarting\n"
                   "2026-08-14 00:45:09 | launched chatter runner (PID 6864) -> x.log\n")
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.delenv("TAMAPI_DB", raising=False)
    monkeypatch.delenv("CHATTER_DB", raising=False)
    monkeypatch.delenv("CHATTER_PERSONAS", raising=False)

    rows = F.events()
    guard = [r for r in rows if r["src"] == "гардиан"]
    assert len(guard) == 2, rows
    assert all(r["ts"] for r in guard), "лента снова без времени"
    assert guard[0]["ts"] >= guard[1]["ts"], "лента не отсортирована по времени"


def test_the_timestamp_prefix_is_stripped_from_the_detail(tmp_path, monkeypatch):
    """Время теперь отдельная колонка. Дублировать его в тексте значит
    занимать узкую колонку тем, что уже нарисовано рядом."""
    _log(tmp_path, "2026-08-14 00:45:08 | runner DOWN - restarting\n")
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    for var in ("TAMAPI_DB", "CHATTER_DB", "CHATTER_PERSONAS"):
        monkeypatch.delenv(var, raising=False)
    row = [r for r in F.events() if r["src"] == "гардиан"][0]
    assert row["detail"] == "runner DOWN - restarting", row["detail"]


def test_a_truncated_log_states_where_visibility_begins(tmp_path, monkeypatch):
    """Граница видимости, названная вслух, — не то же самое, что молча
    обрезанное окно. Это и есть починяемый дефект."""
    body = "".join(f"2026-08-14 00:{i % 60:02d}:00 | runner DOWN - restarting\n"
                   for i in range(3000))
    _log(tmp_path, body)
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setattr(F, "GUARDIAN_LOG_WINDOW", 2048)
    for var in ("TAMAPI_DB", "CHATTER_DB", "CHATTER_PERSONAS"):
        monkeypatch.delenv(var, raising=False)
    rows = F.events()
    assert any("видно с" in r["detail"] for r in rows), rows


def test_an_unreadable_composition_reaches_the_feed_as_a_row(tmp_path, monkeypatch):
    """DEV-18: провал не глотается. Пустая лента вместо объяснения — это
    тишина ровно там, где произошла авария конфигурации."""
    d = tmp_path / "chatter" / "clients"
    d.mkdir(parents=True)
    (d / "active.yaml").write_text("clients: [](((битый", encoding="utf-8")
    _log(tmp_path, "")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    for var in ("TAMAPI_DB", "CHATTER_DB", "CHATTER_PERSONAS"):
        monkeypatch.delenv(var, raising=False)
    rows = F.events()
    assert any(r["src"] == "панель" and "не прочитан" in r["detail"] for r in rows), rows
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: 4 падения — старая `events()` не даёт ни `ts`, ни строк «панель».

- [ ] **Шаг 3: заменить функцию `events()` целиком**

Заменить существующую `events()` в `app/services/jarvis_farm.py` на:

```python
def events(limit: int = 40) -> list[dict]:
    """Лента: control_events клиента + решения гардиана chatter.

    Четыре правки 14.08 (спека §5):
      · база выводится из active.yaml, а не пишется литералом;
      · строки гардиана несут ВРЕМЯ, поэтому лента сортируется по нему;
      · читается хвост лога, а не весь растущий файл, и граница видимости
        называется вслух;
      · дебаунс-шум сторожа в ленту не попадает.

    «Посчитано ≠ доехало» — доставку алертов мы сегодня не журналируем, и это
    помечено как пробел в самой разметке.
    """
    out: list[dict] = []

    db, db_note = client_db_path()
    if db_note:
        out.append({"src": "панель", "kind": "склад клиентов",
                    "detail": db_note, "ts": None})
    elif db:
        try:
            from app.services.tamapi_metrics import _ro
            with _ro(db) as c:
                for r in c.execute(
                        "SELECT kind, contact_id, detail, ts FROM control_events "
                        "ORDER BY id DESC LIMIT ?", (limit,)):
                    out.append({"src": "chatter", "kind": r["kind"],
                                "detail": r["detail"] or r["contact_id"] or "",
                                "ts": r["ts"]})
        except Exception as exc:                   # noqa: BLE001 — источник внешний
            # DEV-18: провал источника виден В САМОЙ ленте, а не в тишине.
            out.append({"src": "панель", "kind": "база клиентов",
                        "detail": f"{db}: {type(exc).__name__}: {exc}", "ts": None})

    lines, truncated = read_tail(ROOT / "logs" / "chatter_guardian.stdout.log",
                                 GUARDIAN_LOG_WINDOW)
    kept = [ln for ln in lines if is_decision(ln)]
    for ln in kept:
        ts = parse_log_ts(ln)
        detail = _LOG_TS_RE.sub("", ln)            # время уехало в свою колонку
        out.append({"src": "гардиан", "kind": "раннер",
                    "detail": detail[:120], "ts": ts})
    if truncated:
        oldest = next((parse_log_ts(ln) for ln in kept if parse_log_ts(ln)), None)
        seen_from = (time.strftime("%d.%m %H:%M", time.localtime(oldest))
                     if oldest else "неизвестного момента")
        out.append({"src": "гардиан", "kind": "граница видимости",
                    "detail": f"лог длиннее окна: видно с {seen_from}",
                    "ts": oldest})

    # Строки без времени (ошибки источников) встают наверх: это НЕ события
    # прошлого, это состояние сейчас, и прятать его под вчерашние записи нельзя.
    out.sort(key=lambda e: (e["ts"] is not None, -(e["ts"] or 0.0)))
    return out[:limit]
```

- [ ] **Шаг 4: убедиться, что тесты зелёные**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_feed.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `17 passed`.

- [ ] **Шаг 5: прогнать сторожа панели целиком — регрессий быть не должно**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panels_hierarchy.py tests/chatter/test_panels_web.py tests/chatter/test_panels_stopall_ux.py tests/chatter/test_panel_event_loop.py tests/chatter/test_farm_self_match.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `143 passed`.

- [ ] **Шаг 6: коммит**

```bash
cd /c/jarvis && git add app/services/jarvis_farm.py tests/chatter/test_panel_feed.py
git commit -m "feat(panel): лента собрана из четырёх правок и отсортирована по времени"
```

---

### Task 6: `-Encoding utf8` в гардиане — чтобы будущие строки не требовали фолбэка

**Файлы:**
- Изменить: `scripts/chatter_guardian_detached.ps1`

Фолбэк из Task 3 чинит прошлое. Эта правка убирает причину.

- [ ] **Шаг 1: найти запись в лог**

```
cd /c/jarvis && grep -n "Tee-Object\|Out-File\|Add-Content" scripts/chatter_guardian_detached.ps1
```

- [ ] **Шаг 2: добавить кодировку в функцию записи лога**

В функции записи строки лога (та, что использует `Tee-Object -FilePath ... -Append`)
дописать явную кодировку. `Tee-Object` кодировку не принимает, поэтому запись
разделяется:

```powershell
function Write-G([string]$msg) {
    $line = ('{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    Write-Host $line
    # UTF-8 ЯВНО: по умолчанию Add-Content берёт системную ANSI (cp1251), и
    # строка «Состав: … db=…» — единственная, где лог прямо называет активную
    # базу, — приезжала в панель кракозябрами. Фолбэк при чтении
    # (jarvis_farm.read_tail) чинит УЖЕ написанное; это убирает причину.
    Add-Content -Path $gOut -Value $line -Encoding utf8
}
```

- [ ] **Шаг 3: проверить синтаксис скрипта, не запуская его**

```
cd /c/jarvis && powershell -NoProfile -Command "[void][System.Management.Automation.Language.Parser]::ParseFile('C:\jarvis\scripts\chatter_guardian_detached.ps1',[ref]$null,[ref]$e); if($e){$e|ForEach-Object{$_.Message}}else{'синтаксис ок'}"
```

Ожидаемо: `синтаксис ок`.

- [ ] **Шаг 4: коммит**

```bash
cd /c/jarvis && git add scripts/chatter_guardian_detached.ps1
git commit -m "fix(chatter): гардиан пишет лог в utf-8, а не в системной cp1251"
```

---

### Task 7: Мутации DEV-26 на каждую правку

**Файлы:**
- Изменить: `scripts/mutate_panels_hierarchy.py`

Каждая мутация обязана покраснить названный тест. Пары обязательны: односторонняя
проверка зеленеет на выключенном пороге.

- [ ] **Шаг 1: добавить адрес нового файла сторожей**

Рядом с `FT = "tests/chatter/test_farm_self_match.py"` добавить:

```python
FD = "tests/chatter/test_panel_feed.py"
```

- [ ] **Шаг 2: добавить восемь мутаций в конец списка `MUTATIONS`**

```python
    # ═══════════ лента: четыре правки 14.08 (мерж 1) ═══════════════════════
    ("время строк гардиана снова считается UTC", FARM,
     [("        return time.mktime(time.strptime(m.group(1), \"%Y-%m-%d %H:%M:%S\"))",
       "        import calendar\n"
       "        return calendar.timegm(time.strptime(m.group(1), \"%Y-%m-%d %H:%M:%S\"))")],
     f"{FD}::test_the_guardian_timestamp_is_read_as_local_time"),

    ("строка без префикса получает выдуманное время", FARM,
     [("    m = _LOG_TS_RE.match(line)\n    if not m:\n        return None",
       "    m = _LOG_TS_RE.match(line)\n    if not m:\n        return _now()")],
     f"{FD}::test_a_line_without_a_timestamp_gets_none_not_a_guess"),

    ("дебаунс-шум снова попадает в ленту", FARM,
     [('GUARDIAN_NOISE = ("debouncing", "runner alive", "heartbeat fresh after")',
       'GUARDIAN_NOISE = ()')],
     f"{FD}::test_the_guardians_own_debounce_noise_never_reaches_the_feed"),

    # Парная: «выбросить шум» не имеет права стать «выбросить всё».
    ("фильтр решений выбросил и настоящие события", FARM,
     [('    return any(mark in line for mark in GUARDIAN_DECISIONS)',
       '    return False')],
     f"{FD}::test_decisions_of_the_guardian_reach_the_feed"),

    ("строка с именем базы выброшена из ленты", FARM,
     [('    "CHATTER_PERSONAS=",           # строка «Состав: … db=…», называет активную базу',
       '')],
     f"{FD}::test_the_composition_line_survives_because_it_names_the_database"),

    ("порядок фолбэка перевёрнут — utf-8 читается как cp1251", FARM,
     [('    try:\n        text = raw.decode("utf-8")\n    except UnicodeDecodeError:\n'
       '        text = raw.decode("cp1251", errors="replace")',
       '    text = raw.decode("cp1251", errors="replace")')],
     f"{FD}::test_a_utf8_log_is_read_as_utf8"),

    ("фолбэка на cp1251 больше нет", FARM,
     [('    try:\n        text = raw.decode("utf-8")\n    except UnicodeDecodeError:\n'
       '        text = raw.decode("cp1251", errors="replace")',
       '    text = raw.decode("utf-8", errors="replace")')],
     f"{FD}::test_a_cp1251_log_is_still_readable"),

    ("база ленты снова прибита литералом", FARM,
     [('    env_db = os.getenv("TAMAPI_DB") or os.getenv("CHATTER_DB")\n    if env_db:\n        return env_db, ""',
       '    return str(ROOT / ".secrets" / "demo.db"), ""')],
     f"{FD}::test_the_database_follows_the_primary_slug"),

    ("битый состав молча откатывается на demo.db", FARM,
     [('        return None, (f"склад клиентов не прочитан ({type(exc).__name__}: {exc}) "\n'
       '                      f"— какую базу читать, неизвестно")',
       '        return str(ROOT / ".secrets" / "demo.db"), ""')],
     f"{FD}::test_a_broken_composition_says_so_instead_of_falling_back_silently"),
```

- [ ] **Шаг 3: закоммитить перед прогоном — гейт требует чистого дерева**

```bash
cd /c/jarvis && git add scripts/mutate_panels_hierarchy.py
git commit -m "test(panel): мутации DEV-26 на четыре правки ленты"
```

- [ ] **Шаг 4: прогнать гейт**

```
cd /c/jarvis && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/mutate_panels_hierarchy.py
```

Ожидаемо: `Все 66 мутаций пойманы.` Любая строка `[СЛЕП]` — это слепой сторож,
и чинить надо ТЕСТ, а не мутацию. Строка `МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ` означает, что
фрагмент разошёлся с исходником: поправить фрагмент, иначе мутация ничего
не проверила.

---

### Task 8: Приёмка на ЖИВОМ логе

Стенд `panels_demo.py` сеет опрятные данные, а весь дефект именно в уродливых:
163 дебаунс-строки, cp1251 вперемешку с utf-8, время без зоны, база, зависящая
от состава. Зелёная приёмка на стенде означала бы, что проверили не то.

- [ ] **Шаг 1: снять поведение ДО деплоя**

```
cd /c/jarvis && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -c "
from app.services import jarvis_farm as F
rows = F.events()
print('строк:', len(rows))
print('без времени:', sum(1 for r in rows if not r['ts']))
print('дебаунс:', sum(1 for r in rows if 'debouncing' in r['detail']))
print('база:', F.client_db_path())
"
```

Записать вывод — это «до».

- [ ] **Шаг 2: мержить ТОЛЬКО с отмашкой владельца**

```bash
cd /c/jarvis && git checkout phase-4.0-unified-jarvis && git merge --ff-only arc/panel-feed-fixes
```

- [ ] **Шаг 3: ЯВНЫЙ рестарт бэкенда по точным PID**

Мерж ≠ деплой. Узнать PID владельца порта, убить точечно, дождаться гардиана:

```
cd /c/jarvis && PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "
import psutil, time, urllib.request
pids = [c.pid for c in psutil.net_connections(kind='tcp')
        if c.laddr and c.laddr.port == 8010 and c.status == 'LISTEN' and c.pid]
targets = []
for pid in pids:
    p = psutil.Process(pid); targets += [p.pid, p.ppid()]
for pid in targets:
    p = psutil.Process(pid)
    assert 'run_backend_detached' in ' '.join(p.cmdline()), p.cmdline()
    p.kill(); print('убит', pid)
t0 = time.time()
while time.time() - t0 < 90:
    try:
        if urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3).status == 200:
            print('поднялся за %.0f с' % (time.time() - t0)); break
    except Exception: time.sleep(2)
else: print('НЕ ПОДНЯЛСЯ')
"
```

- [ ] **Шаг 4: снять поведение ПОСЛЕ, с живого бэкенда**

Повторить команду из шага 1 и сверить: строк без времени — только строки
источников, дебаунс-строк ноль, база совпадает с первичным slug'ом из
`chatter/clients/active.yaml`.

- [ ] **Шаг 5: глазами — строка «Состав» читается словами**

```
cd /c/jarvis && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -c "
from app.services import jarvis_farm as F
for r in F.events():
    if 'CHATTER_PERSONAS' in r['detail']: print(r['detail'])
"
```

Ожидаемо: `Состав: CHATTER_PERSONAS=… db=…` без символов `�`.

- [ ] **Шаг 6: смена состава**

Переставить порядок в `chatter/clients/active.yaml`, повторить шаг 1, вернуть
файл как было. База обязана поехать за первичным slug'ом.

⚠️ Раннер читает этот файл при старте — не перезапускать его во время проверки,
иначе смена состава уедет в прод. Вернуть файл ДО любого рестарта.

- [ ] **Шаг 7: скриншоты приёмки**

```
cd /c/jarvis && ./.venv/Scripts/python.exe scripts/panels_demo.py --seed --port 8099 &
sleep 12 && PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/panels_screenshots.py
```

Проверить `docs/panels-screens/09-jarvis-fold-unfolded-707.png`.

---

## Самопроверка плана против спеки

| Требование спеки | Задача |
|---|---|
| §5.1 путь к базе из `active.yaml`, env выигрывает, битый состав — явная строка | Task 4, Task 5 |
| §5.2 `ts` из префикса, локальное время, неразобранное → прочерк | Task 1, Task 5 |
| §5.2 фильтр именованным списком решений, дебаунс выброшен | Task 2 |
| §5.3 хвост вместо слурпа, граница видимости вслух | Task 3, Task 5 |
| §5.4 фолбэк utf-8 → cp1251 при чтении | Task 3 |
| §5.4 `-Encoding utf8` в гардиане | Task 6 |
| §6 ловушка 7 (локальное время) | Task 1 |
| §6 ловушка 8 (порядок фолбэка) | Task 3, мутация в Task 7 |
| §7 сторожа ленты, все семь пунктов | Tasks 1–5 |
| §7 мутации в `mutate_panels_hierarchy.py` | Task 7 |
| §8 приёмка ленты на живом логе, пункты 1–4 | Task 8 |

Требований §5 без задачи не осталось. §2–§4 (журнал) — второй план, второй мерж.
