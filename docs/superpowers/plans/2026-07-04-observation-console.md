# Пульт наблюдения (Jarvis Ступень 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin-only **read-only** пульт управления проектом из Telegram: `/git_status`, `/regress` (асинхронный pytest с вердиктом), `/logs_tail [N]`, `/health` — плюс новая admin-only категория меню 🔧 Наблюдение. **Ни одна команда не меняет файлы/git/процессы.**

**Architecture:** Новый чистый модуль `tools/jarvis_observe.py` держит read-only коллекторы (git-снимок, фильтр лог-шума, health-снимок, парсер pytest-сводки) — без сети, максимально тестируемо на моках/tmp. Тонкая обвязка в `tools/jarvis_smart_telegram_control.py`: 4 `if cmd == "/x":` блока в `handle_command` (5916). **Admin-only достигается ОМИССИЕЙ из `FRIEND_ALLOWED_COMMANDS`** — friend получает «🚫» в `handle()` (6963-6968) автоматически, без декораторов. `/regress` использует существующий паттерн daemon-потока (как `/animate`, `_swapbatch_run_phase`) + subprocess pytest с пониженным приоритетом и таймаутом. Новая `Category` в `tools/jarvis_menu.py` со всеми `friend=False` → авто-скрыта от friend (`_visible`).

**Tech Stack:** Python 3.11 (Windows/PowerShell), `subprocess`, `shutil.disk_usage`, `urllib`, `psutil` (доступен), pytest. Тесты только на моках/tmp, ноль реальных сетевых вызовов, ноль мутаций.

---

## Разведанное состояние (факты, `file:line`)

**Диспатч и admin-гейт:**
- `handle_command(chat_id, cmd, query, state)` `tools/jarvis_smart_telegram_control.py:5916` — плоская лестница `if cmd == "/x": … return` (примеры `/menu`:5921, `/status`:5931). Новая команда = вставить блок.
- Admin-гейт целиком в `handle()` `:6955` ДО `handle_command`: `role != "admin"` и `_cmd not in FRIEND_ALLOWED_COMMANDS` (`:6963-6968`) → «🚫 …» + return. **Команда admin-only ⇔ её НЕТ в `FRIEND_ALLOWED_COMMANDS` (`:7100-7123` frozenset).** Ничего больше не нужно.
- `ALLOWED_CHAT_ID` `:41`, `_role_for_chat` `:7131`, `_is_admin_id` `:7146`, `_menu_role` `:5911`.

**Паттерн фоновой задачи (для /regress):** локальная `def _run(): …` + `threading.Thread(target=_run, daemon=True, name="…").start()`; сообщение юзеру `send(chat_id, …)` из потока по завершении. Примеры: `/animate` `:2316`/`:2353`, `_swapbatch_run_phase` `:945`/`:1180`, videoref motion `:4036`. Не блокирует poll-loop.

**Меню-реестр:** `tools/jarvis_menu.py` — `MenuItem` `:28`, `Category` `:37`, `_mi` `:54`, `_visible(items, role)` `:44` (`role=="admin" or it.friend`), `MENU=[…]` `:203`, `render_root` `:207` (категория со всеми `friend=False` → невидима friend). `NATIVE_ADMIN_COMMANDS` `:254`, `NATIVE_FRIEND_COMMANDS` `:272`, `build_native_payloads` `:288`.

**Источники данных:**
- **git:** репо `C:\jarvis`; upstream есть (`origin/phase-4.0-unified-jarvis`, remote `origin`). Read-only: `git -C C:/jarvis status -sb` (ветка + ahead/behind + грязь одной командой), `git -C C:/jarvis rev-parse --short HEAD`. **Обязательно `-C C:/jarvis`** (cwd бота может отличаться).
- **pytest:** `C:/jarvis/pytest.ini` (только маркер `integration`), НЕТ Makefile/CI. Запуск `python -m pytest`. ⚠️ **baseline «130 failed» — фольклор, НЕ закоммичен** (MEMORY: ~31 pre-existing в `jarvis-preexisting-test-failures-techdebt.md`; в Арке 2 намерили 130 в сессии, файл не сохранён). → baseline замерять/хранить, не хардкодить. `psutil 7.2.2` есть. Приоритет: `subprocess.Popen(creationflags=BELOW_NORMAL_PRIORITY_CLASS=0x4000)`; таймаут `subprocess.run(timeout=)`.
- **логи:** реальный `C:/jarvis/logs/jarvis_bot.log` (RotatingFileHandler 5MB×5, `app/core/logging_setup.py:51`). ⚠️ существующий `/logs` (`:4844`) смотрит НЕ туда (`bot.log`/`jarvis_bot.log` в cwd) — наш `/logs_tail` берёт `ROOT/"logs"/"jarvis_bot.log"` (`ROOT=Path.cwd()` `:82`). Формат `TS | LEVEL | logger | msg` (`logging_setup.py:28`). **base64-шум:** гигантские DEBUG-строки логгеров `*._base_client` (anthropic/openai/x.ai) с `"Request options:"` (в текущем логе ровно 2 такие, одна 3.8MB). Фильтр: дропать строки где `_base_client` в поле логгера И `"Request options"` в msg, ИЛИ длина строки > 2000.
- **health:** PID `state/bot.pid` (`_PID_FILE` `:44`, `_check_single_instance` `:47`, Windows-liveness `tasklist /FI "PID eq …"` `:52`). Heartbeat `state/bot_heartbeat.txt` (`_HEARTBEAT_FILE` `:43`, пишется каждые 30с `_heartbeat_thread` `:7071`; **эталон чтения** `handle_self_status` `:3381` — `age=int(time.time()-last)`). Backend `BACKEND` `:75` (`BACKEND_BASE_URL`|`TELEGRAM_BACKEND_URL`|`http://127.0.0.1:8010`), проба `urllib.request.urlopen(f"{BACKEND}/health", timeout=5)` (`:3373`). Туннель `C:/jarvis/logs/cloudflared.log` (16KB, mtime-свежесть; ⚠️ имя службы разнится `cloudflared`/`Jarvis-cloudflared` → надёжнее stat mtime лог-файла). Диск `shutil.disk_usage("C:/jarvis")` (stdlib, read-only).

**Безопасность:** всё добавляется аддитивно (блоки в `handle_command` + опц. `Category` в MENU). Poll-loop доходит до команд только через role-гейт (`process_update`→`handle`→role-check→`handle_command`). read-only команда не в `FRIEND_ALLOWED_COMMANDS` → friend отсечён до хендлера.

---

## 📋 Допущения — подтвердить при ОК (разрешено самостоятельно, зафиксировано)

1. **`/regress` baseline замеряется/хранится, не хардкодится.** Читаем ожидаемое число падений из `state/regress_baseline.json` (`{"failed": N, "ts": "…"}`). Если файла нет → `/regress` докладывает сырые числа + «baseline не задан, обнови вручную». **Установка baseline — НЕ через read-only команду** (одноразовый ops-шаг: прогнать сюиту, записать файл; или будущая отдельная admin-write команда — вне этой read-only арки). Так `/regress` остаётся строго read-only (только читает baseline-файл). Подтвердить: ок хранить в `state/regress_baseline.json`?
2. **`/regress` спавнит pytest — это НЕ мутация проекта.** Запуск pytest = read-only наблюдение здоровья тестов: `python -m pytest tests/ -q -p no:cacheprovider --continue-on-collection-errors --tb=no` (без кеша → не пишет `.pytest_cache`; тесты изолируют tmp через conftest). Спавн процесса не трогает git/исходники/бота. Приоритет `BELOW_NORMAL_PRIORITY_CLASS` + таймаут `REGRESS_TIMEOUT_S=600` + single-flight (не запускать второй прогон, пока идёт первый).
3. **`/logs_tail [N]` дефолт N=40**, кап Telegram 3900 симв (обрезка снизу). base64-шум фильтруется всегда.
4. **Категория меню 🔧 «Наблюдение» (`cat_id="observe"`), все пункты `friend=False`** → авто-скрыта от friend. Пункты `exec` (сразу выполняют read-only команду). Добавляется в `MENU` + опц. в `NATIVE_ADMIN_COMMANDS` (admin-scope ☰). `/logs_tail` в native — как `logs_tail`.
5. **Строгая admin-only гарантия:** 4 команды НИКОГДА не добавляются в `FRIEND_ALLOWED_COMMANDS` и не помечаются `friend=True`. Отдельный зуб это фиксирует (обе стороны).
6. **cloudflared: свежесть по mtime лог-файла**, не по имени службы (имя неоднозначно). «Свежо» = mtime younger than `TUNNEL_STALE_S=300`.
7. **Health backend-проба с коротким таймаутом (5с)**, ошибка = «🔴 backend недоступен» (не роняет команду).

---

## Файловая структура

- **Create** `tools/jarvis_observe.py` — чистые read-only коллекторы (нет мутаций, нет импорта основного файла):
  - `filter_log_noise(lines: list[str]) -> list[str]` — дроп base64-шума (чистая).
  - `tail_log(path, n=40, max_chars=3900) -> str` — прочитать хвост, отфильтровать, обрезать.
  - `git_status_text(repo="C:/jarvis", run=subprocess.run) -> str` — HEAD/ветка/грязь/ahead-behind (read-only git, `run` инъектируется для тестов).
  - `parse_pytest_summary(stdout: str) -> dict` — `{"failed": int, "passed": int, "errors": int}` из финальной строки (чистая).
  - `regress_verdict(summary: dict, baseline: dict|None) -> str` — вердикт vs baseline (чистая).
  - `health_snapshot(readers) -> str` — PID/heartbeat-age/backend/tunnel/disk; `readers` = инъектируемые функции (тестируется без I/O).
  - Константы путей: `ROOT`, `LOG_PATH`, `PID_PATH`, `HEARTBEAT_PATH`, `CLOUDFLARED_LOG`, `BASELINE_PATH`.
  - **Гарды read-only:** `_GIT_READONLY_VERBS = {"status","rev-parse","log","describe"}`; `git_status_text` собирает только их (зуб проверяет).
- **Modify** `tools/jarvis_smart_telegram_control.py` — 4 `if cmd == …:` блока в `handle_command` (5916); `/regress` через daemon-поток.
- **Modify** `tools/jarvis_menu.py` — новая `Category("observe", "🔧 Наблюдение", …)` в `MENU` (203); опц. `NATIVE_ADMIN_COMMANDS` (254).
- **Test:** `tests/test_observe_module.py` (чистый модуль), `tests/test_observe_wiring.py` (обвязка + admin-only зуб, по образцу `tests/test_menu_bot_wiring.py`).

> ⚠️ Line-номера — из разведки @da2312e; при исполнении сверять по именам (line-drift).

---

## Задачи (TDD)

### Task 1: Фильтр лог-шума + `tail_log` (чистые)

**Files:** Create `tools/jarvis_observe.py`; Test `tests/test_observe_module.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_observe_module.py
import tools.jarvis_observe as o

def test_filter_drops_base64_request_options():
    lines = [
        "2026-07-04 01:02:11 | INFO     | root                | ok",
        "2026-07-04 01:02:11 | DEBUG    | anthropic._base_client | Request options: {'data':'" + "A"*5000 + "'}",
        "2026-07-04 01:02:12 | INFO     | block_m             | done",
    ]
    out = o.filter_log_noise(lines)
    assert len(out) == 2 and all("Request options" not in x for x in out)

def test_tail_log_reads_last_n_filtered(tmp_path):
    p = tmp_path / "j.log"
    p.write_text("\n".join(f"2026 | INFO | root | line{i}" for i in range(100)), encoding="utf-8")
    txt = o.tail_log(str(p), n=10)
    assert "line99" in txt and "line89" in txt and "line80" not in txt
```

- [ ] **Step 2: Run — verify fails** — `pytest tests/test_observe_module.py -v` → FAIL (`No module named tools.jarvis_observe`).
- [ ] **Step 3: Implement** `filter_log_noise` (дроп если `_base_client` в строке И `"Request options"` в строке, ИЛИ `len(line) > 2000`) + `tail_log` (читать файл, взять последние n после фильтра, join, обрезать до max_chars снизу).
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(observe): pure log-noise filter + tail_log"`

### Task 2: `git_status_text` (read-only git, инъекция subprocess)

**Files:** Modify `tools/jarvis_observe.py`; Test `tests/test_observe_module.py`

- [ ] **Step 1: Failing test** — `run` замокан, отдаёт `status -sb`/`rev-parse` вывод; проверить, что текст содержит ветку, sha, ahead, «чисто/грязно». Зуб read-only: все вызванные git-подкоманды ∈ `_GIT_READONLY_VERBS`.

```python
def test_git_status_text_shape_and_readonly():
    calls = []
    def fake_run(args, **k):
        calls.append(args)
        import types
        joined = " ".join(args)
        if "rev-parse" in joined and "--short" in joined:
            return types.SimpleNamespace(stdout="da2312e\n", returncode=0)
        return types.SimpleNamespace(stdout="## phase-4.0-unified-jarvis...origin/phase-4.0-unified-jarvis [ahead 3]\n M brain_v2_state.json\n", returncode=0)
    txt = o.git_status_text(repo="C:/jarvis", run=fake_run)
    assert "phase-4.0-unified-jarvis" in txt and "da2312e" in txt and "ahead 3" in txt
    verbs = [a[2] if a[0]=="git" and a[1]=="-C" else a[1] for a in calls]  # verb after -C <repo>
    assert all(v in o._GIT_READONLY_VERBS for v in verbs)
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — `git_status_text` зовёт `run(["git","-C",repo,"status","-sb"])` + `run(["git","-C",repo,"rev-parse","--short","HEAD"])`; форматирует HEAD/branch/ahead-behind/dirty-count. Только read-only verbs.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** — добавить `run(["git","-C",repo,"add","."])` → read-only зуб RED; убрать → GREEN.
- [ ] **Step 6: Commit** — `git commit -am "feat(observe): read-only git_status_text"`

### Task 3: `parse_pytest_summary` + `regress_verdict` (чистые)

**Files:** Modify `tools/jarvis_observe.py`; Test `tests/test_observe_module.py`

- [ ] **Step 1: Failing test**

```python
def test_parse_pytest_summary():
    s = "130 failed, 3353 passed, 10 skipped, 101 warnings, 4 errors in 255.38s"
    assert o.parse_pytest_summary(s) == {"failed":130,"passed":3353,"errors":4}

def test_regress_verdict_vs_baseline():
    assert "✅" in o.regress_verdict({"failed":130,"passed":3353,"errors":4}, {"failed":130})
    assert "⚠️" in o.regress_verdict({"failed":134,"passed":3349,"errors":4}, {"failed":130})
    assert "baseline не задан" in o.regress_verdict({"failed":130,"passed":1,"errors":0}, None)
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — regex по финальной строке pytest (`(\d+) failed`, `(\d+) passed`, `(\d+) error`); `regress_verdict`: failed<=baseline → ✅ (не хуже), failed>baseline → ⚠️ (+N новых), baseline None → пометка.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(observe): pytest summary parse + baseline verdict"`

### Task 4: `health_snapshot` (инъекция ридеров)

**Files:** Modify `tools/jarvis_observe.py`; Test `tests/test_observe_module.py`

- [ ] **Step 1: Failing test** — `readers` dict инъектируется (pid_alive, heartbeat_age, backend_ok, tunnel_age, disk_free). Проверить сборку строки: PID жив/heartbeat 12с/backend ✅/tunnel свежий/диск.

```python
def test_health_snapshot_formats_all_sections():
    readers = {
        "bot": lambda: {"pid": 17048, "alive": True},
        "heartbeat_age": lambda: 12,
        "backend": lambda: {"ok": True, "code": 200},
        "tunnel_age": lambda: 40,
        "disk": lambda: {"free_gb": 12.9, "total_gb": 119.1},
    }
    txt = o.health_snapshot(readers)
    assert "17048" in txt and "12" in txt and "12.9" in txt
    assert "✅" in txt  # backend ok
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — `health_snapshot(readers)` собирает секции: 🤖 бот PID+alive, 💓 heartbeat age (>90с → ⚠️), 🔌 backend (ok→✅/иначе🔴), 🌐 туннель (age<TUNNEL_STALE_S→✅), 💽 диск free/total. Чистая относительно инъекции.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(observe): health_snapshot with injected readers"`

### Task 5: Обвязка 4 команд в `handle_command` + admin-only зуб

**Files:** Modify `tools/jarvis_smart_telegram_control.py` (5916); Test `tests/test_observe_wiring.py`

- [ ] **Step 1: Failing test** (по образцу `test_menu_bot_wiring.py`)

```python
# tests/test_observe_wiring.py
import importlib
mod = importlib.import_module("tools.jarvis_smart_telegram_control")

def test_observe_commands_are_admin_only_not_friend():
    for c in ("/git_status", "/regress", "/logs_tail", "/health"):
        assert c not in mod.FRIEND_ALLOWED_COMMANDS   # ← строгий admin-only зуб

def test_git_status_command_sends_text(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    import tools.jarvis_observe as o
    monkeypatch.setattr(o, "git_status_text", lambda **k: "HEAD da2312e")
    mod.handle_command("237616472", "/git_status", "", {})
    assert "da2312e" in sent["t"]
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — 4 блока в `handle_command`:
  - `/git_status` → `send(chat_id, jobserve.git_status_text())`.
  - `/logs_tail` → парс N из `query` (дефолт 40), `send(chat_id, jobserve.tail_log(LOG_PATH, n))`.
  - `/health` → собрать реальные ридеры (pid из `_PID_FILE`, heartbeat из `_HEARTBEAT_FILE` как `handle_self_status:3381`, backend `urlopen(f"{BACKEND}/health",5)`, tunnel mtime `logs/cloudflared.log`, `shutil.disk_usage`), `send(chat_id, jobserve.health_snapshot(readers))`.
  - `/regress` → см. Task 6 (async).
  - Импорт `from tools import jarvis_observe as jobserve` (лениво в блоках, как делают другие). **НЕ добавлять эти команды в `FRIEND_ALLOWED_COMMANDS`.**
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** — временно добавить `/git_status` в `FRIEND_ALLOWED_COMMANDS` → admin-only зуб RED; убрать → GREEN.
- [ ] **Step 6: Commit** — `git commit -am "feat(observe): wire /git_status /logs_tail /health (admin-only by omission)"`

### Task 6: `/regress` async + приоритет + таймаут + single-flight

**Files:** Modify `tools/jarvis_smart_telegram_control.py`; Test `tests/test_observe_wiring.py`

- [ ] **Step 1: Failing test** — мок subprocess (отдаёт сводку) + мок baseline-reader; проверить: (1) немедленный «🧪 Запускаю…»; (2) по завершении `send` с вердиктом (парс+baseline); (3) single-flight: второй `/regress` пока идёт первый → «уже идёт». Поток можно гонять синхронно в тесте (мок `threading.Thread` → вызывает target сразу, или вынести тело в `_regress_run(chat_id)` и тестировать его напрямую).

```python
def test_regress_reports_verdict(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "_regress_run_pytest",
        lambda: "130 failed, 3353 passed, 10 skipped, 4 errors in 255s")  # inject runner
    monkeypatch.setattr(mod, "_regress_baseline", lambda: {"failed": 130})
    mod._regress_run("237616472")
    assert any("✅" in s for s in sent)   # не хуже baseline
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** —
  - `_regress_run_pytest()`: `subprocess.run([sys.executable,"-m","pytest","tests/","-q","-p","no:cacheprovider","--continue-on-collection-errors","--tb=no"], cwd="C:/jarvis", capture_output=True, text=True, timeout=int(os.getenv("REGRESS_TIMEOUT_S","600")), creationflags=0x4000)` (BELOW_NORMAL_PRIORITY_CLASS); вернуть последнюю значимую строку stdout. Таймаут → «⏱ прогон превысил Nс».
  - `_regress_baseline()`: прочитать `state/regress_baseline.json` (или None).
  - `_regress_run(chat_id)`: single-flight флаг `_REGRESS_RUNNING`; `send("🧪 Запускаю…")`; парс+вердикт; `send(вердикт)`.
  - В `handle_command` блок `/regress`: если `_REGRESS_RUNNING` → «уже идёт»; иначе `threading.Thread(target=_regress_run, args=(chat_id,), daemon=True, name="regress").start()`.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** — снять single-flight → повторный запуск не блокируется (тест RED); вернуть → GREEN.
- [ ] **Step 6: Commit** — `git commit -am "feat(observe): async /regress with priority, timeout, single-flight, baseline verdict"`

### Task 7: Категория меню 🔧 Наблюдение (admin-only, авто-скрыта)

**Files:** Modify `tools/jarvis_menu.py`; Test `tests/test_jarvis_menu.py` (доп.)

- [ ] **Step 1: Failing test**

```python
def test_observe_category_admin_only_hidden_from_friend():
    ids = [c.cat_id for c in m.MENU]
    assert "observe" in ids
    # friend не видит категорию (все пункты friend=False)
    assert m.render_category("observe", "friend") is None
    _t, kb = m.render_category("observe", "admin")
    datas = [b["callback_data"] for row in kb for b in row]
    assert "menu:x:git_status" in datas and "menu:x:regress" in datas
    # friend root не содержит observe
    _t2, rk = m.render_root("friend")
    assert "menu:cat:observe" not in [b["callback_data"] for row in rk for b in row]
```

- [ ] **Step 2: Run — verify fails** → FAIL.
- [ ] **Step 3: Implement** — `_OBSERVE = Category("observe","🔧 Наблюдение",(_mi("/git_status","статус git","exec",False), _mi("/regress","прогон тестов","exec",False), _mi("/logs_tail","хвост логов","exec",False), _mi("/health","здоровье систем","exec",False)))`; добавить `_OBSERVE` в `MENU` (203). Опц.: добавить `("git_status","Статус git")`,`("regress","Прогон тестов")`,`("health","Здоровье систем")` в `NATIVE_ADMIN_COMMANDS`.
- [ ] **Step 4: Run — verify passes** → PASS.
- [ ] **Step 5: Мутация** — пометить любой пункт `friend=True` → «скрыта от friend» зуб RED; вернуть → GREEN.
- [ ] **Step 6: Commit** — `git commit -am "feat(observe): 🔧 admin-only menu category (auto-hidden from friend)"`

### Task 8: Полный регресс

- [ ] Baseline diff имён падений vs after (как в Арке 2). Флаки — изоляцией.
- [ ] Новые observe-зубы green; menu-регресс (Арка 2) не тронут.
- [ ] **Побочно (не блокер):** зафиксировать реальный baseline падений в `state/regress_baseline.json` для `/regress` (одноразово, ops-шаг вне read-only команды — см. Допущение №1).

---

## Риски

1. **`/regress` жрёт CPU vs живой бот.** Митигейт: daemon-поток (не блокирует poll-loop) + `BELOW_NORMAL_PRIORITY_CLASS` + таймаут + single-flight. Всё равно тяжёлый прогон (~4-5 мин) конкурирует за CPU — предупредить, что бот может отвечать медленнее во время прогона.
2. **Baseline-фольклор.** Не хардкодить 130; читать из файла, при отсутствии — сырые числа + пометка (Допущение №1).
3. **Утечка admin-команды во friend.** Единственная защита — омиссия из `FRIEND_ALLOWED_COMMANDS` + `friend=False` в меню. Зубы Task 5/Task 7 фиксируют обе стороны. НИКОГДА не добавлять эти 4 в friend-allowlist.
4. **Ложная read-only.** git-хелпер строго read-only verbs (зуб Task 2). `/regress` спавнит pytest (наблюдение, не мутация; `-p no:cacheprovider` не пишет кеш). Никаких `add/commit/checkout/kill/restart/rm`.
5. **cloudflared имя службы.** Не опрашивать службу по имени (разнится); свежесть по mtime лог-файла.
6. **Windows-специфика.** `-C C:/jarvis`/`cwd=` обязательны; `creationflags` — Windows-only (на не-Windows опустить/через psutil.nice). Пути `state/bot.pid`,`state/bot_heartbeat.txt`,`logs/*` относительно `C:\jarvis`.
7. **Существующий сломанный `/logs`** (`:4844`, смотрит не туда) — НЕ чиним в этой арке (отдельный техдолг); `/logs_tail` — новая корректная команда.

## Что НЕ трогаем

- Существующие команды и их логику (только добавляем 4 новых блока + 1 категорию).
- `FRIEND_ALLOWED_COMMANDS` — НЕ расширяем (эти команды admin-only навсегда).
- Существующий `/logs` (`:4844`) — не рефакторим (техдолг).
- Никаких мутаций git/файлов/процессов ни в одной команде. Никаких новых зависимостей (psutil/shutil/subprocess — stdlib/уже есть).
- Установка baseline-файла — ops-шаг, не read-only команда (не автоматизируем запись в этой арке).

---

## Self-review

- **Покрытие:** /logs_tail=T1, /git_status=T2+T5, /regress=T3+T6, /health=T4+T5, меню 🔧=T7, admin-only зуб=T5+T7, регресс=T8. ✔
- **Placeholder-скан:** код в шагах приведён; пути/env явны; line-номера помечены «сверить». ✔
- **Типы/имена:** `filter_log_noise/tail_log/git_status_text/parse_pytest_summary/regress_verdict/health_snapshot`, `_regress_run/_regress_run_pytest/_regress_baseline`, `Category("observe",…)`, callback `menu:x:git_status` — согласованы. ✔
- **Открытые вопросы для Daniil:** Допущение №1 (baseline-файл `state/regress_baseline.json`), №2 (pytest-спавн как read-only), №6 (tunnel по mtime). Подтвердить при ОК.
