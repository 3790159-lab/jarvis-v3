# Якорь: ферма процессов и наследство веток (сверено 2026-07-28)

**Тип:** не инцидент, а **якорь** — то, что должно быть под рукой ДО следующего
инцидента. Родился из двух случаев, где вреден был не баг, а незнание карты:

- **24.07** — рестарт chatter-гардиана по маске `*guardian_detached*` снёс
  ЗАОДНО главный бот (`taskkill /T` по всем совпадениям). Маска не знала, что
  под ней три разных сервиса.
- **28.07** — targeted-гейт сказал «дифф не маппится ни на один тест» на диффе,
  который ДОБАВЛЯЛ три тест-файла. Корень — `list_test_files` сканировал
  `tests/` плоско и не видел `tests/chatter/**`. Человека это вытолкнуло к
  единственной оставшейся кнопке — «мердж вслепую».

Общее у обоих: **инструмент действовал по карте, которая не совпадала с
территорией.** Ниже — территория, сверенная фактом (не по памяти).

---

## 1. Ферма: что живёт, чем поднимается, где смотреть

Все гардианы — Scheduled Tasks (S4U / RunLevel Highest), переживают logoff, обрыв
SSH и ребут. Схема одна: **PS-скрипт-гардиан → дочерний python-процесс.**

| Таск | Триггеры | Скрипт-гардиан | Что держит | Ритм / liveness |
|---|---|---|---|---|
| `JarvisBackendGuardian` | Boot, Logon, Time | `scripts\backend_guardian_detached.ps1` | `scripts\run_backend_detached.py` (:8010) | 15 с, проба порта |
| `JarvisBotGuardian` | Boot, Logon | `scripts\bot_guardian_detached.ps1` | `tools\jarvis_smart_telegram_control.py` (главный бот) | 30 с, heartbeat ≤180 с, debounce 3 |
| `JarvisChatterGuardian` | Boot, Logon, Time | `scripts\chatter_guardian_detached.ps1` | `chatter.telethon_run` (userbot клиента) | 30 с, heartbeat ≤180 с, debounce 3 |
| `JarvisOpsWatchdog` | Boot, Logon | `scripts\ops_watchdog_detached.ps1` | ничего (наблюдатель) | 30 с, DOWN → TG |

Не Running by design: `JarvisChatterCacheDigest` (ежедневно 21:05),
`JarvisIgTokenRefresh`, `JarvisSniperDetached` (**отставлен намеренно**,
триггеров нет, денег не тратит — не «чинить»).

### Где что лежит — две разные папки логов

Это отдельные грабли: **логи не в одном месте.**

| Сервис | Лог гардиана | Лог самого сервиса | Heartbeat | Lock |
|---|---|---|---|---|
| backend | `state\logs\backend_guardian.stdout.log` | `state\logs\backend_boot.{stdout,stderr}.log` | — (проба порта) | `state\locks\backend_guardian.pid` |
| бот | `state\logs\bot_guardian.stdout.log` | `state\logs\bot_boot.{stdout,stderr}.log` | `state\bot_heartbeat.txt` (свой у гардиана — `state\guardian_heartbeat.txt`) | `state\locks\bot_guardian.pid` |
| chatter | `logs\chatter_guardian.stdout.log` | `logs\chatter_telethon.log`, а под semidemo-флагом — `logs\chatter_volska.log` | `state\chatter_heartbeat.txt` (+ `state\chatter_guardian_heartbeat.txt`) | `state\locks\chatter_guardian.pid` |
| ops watchdog | `state\logs\ops_watchdog.stdout.log` | — | `state\ops_watchdog_heartbeat.txt` | `state\locks\ops_watchdog.pid` |

**Важно:** chatter пишет в `logs\`, остальные — в `state\logs\`. Искать «свежий
лог» одной командой по одной папке = не найти половину фермы.

Ещё: у chatter «настоящий» операционный лог — **stderr-файл** (`chatter_*.log`),
в `*.stdout.log` попадает лишь стартовый print. `Start-Process` требует РАЗНЫХ
файлов под stdout и stderr, иначе запуск молча падает (грабли cold-start).

### Два PID на сервис — это норма

Windows-venv `Scripts\python.exe` перезапускает базовый интерпретатор, поэтому
здоровый раннер виден как ДВА процесса (лаунчер + воркер). Один живой процесс +
свежий heartbeat = здоров.

## 2. Рестарт: как делать, чтобы не снести соседа

> **Правило (из инцидента 24.07): никогда не глушить по маске командной строки.
> Гасим КОНКРЕТНЫЙ PID дочернего процесса, гардиан поднимет сам.**

Скоупленный рестарт сервиса:

1. Найти дочерний PID: `Get-CimInstance Win32_Process -Filter "Name='python.exe'"`
   + фильтр по `CommandLine` **точным именем модуля/скрипта**
   (`chatter.telethon_run`, `jarvis_smart_telegram_control`), а не по `guardian`.
2. `taskkill /PID <дочерний> /T /F` — `/T` снимает его собственное дерево, но
   **не родителя-гардиана** (гардиан выше по дереву).
3. Ждать ~90 с: debounce 3 × 30 с, потом `runner DOWN - restarting` в логе
   гардиана. По пути прилетит 🔴 в TG и парный ✅ после подъёма — это норма, а
   не второй инцидент.
4. Проверить факт: новый PID + свежий heartbeat + строка `heartbeat fresh` в
   логе гардиана.

Рестарт САМОГО гардиана (нужен, когда меняется env в его скрипте — например
`CHATTER_OBLIGATIONS_SLOT`, `CHATTER_CLASSIFIER_CACHE`): `Stop-ScheduledTask` →
снять свой lock → `Start-ScheduledTask`. Просто перезапуск таска раннер НЕ
пересоздаст — гардиан увидит живого ребёнка и ничего не тронет.

### Что ещё знать перед рестартом

- **Прод = диск.** Гардиан запускает код из рабочего дерева `C:\jarvis`, поэтому
  мерж/`git checkout` — это уже деплой; в процессе он окажется на следующем
  рестарте. Дерево ВСЕГДА держим на `phase-4.0-unified-jarvis`.
- **`git stash` при живом боте стирает состояние** (`brain_v2_state.json` —
  tracked). Не стешить.
- **NTFS врёт про размер живого лога:** `dir`/`Get-ChildItem` показывают 0 байт у
  файла с открытым write-хэндлом. Проверять только `Get-Content` / `os.stat`.
- **Правки env в скрипте гардиана** читаются на СТАРТЕ раннера → требуют
  рестарта ТАСКА, а не только раннера.

## 3. Наследство: 21 worktree, 5 живых

Сверено 2026-07-28. `phase-4.0-unified-jarvis` = прод, `C:\jarvis`.

### Живое — не трогать

| Worktree | Ветка | Статус |
|---|---|---|
| `C:\jarvis` | `phase-4.0-unified-jarvis` | прод |
| `C:\jarvis-followup` | `chatter-followup` | НЕ смержена, ждёт ОК (Ф2) |
| `jarvis_worktrees\chatter-multi-instance` | `chatter-multi-instance` | НЕ смержена |
| `jarvis_worktrees\chatter-multiclient` | `arc/chatter-multiclient` | НЕ смержена |
| `jarvis_worktrees\money-hygiene` | `arc/money-hygiene` | НЕ смержена |
| `jarvis_worktrees\fix-guardian-oom` | `fix/backend-guardian-oom-resilience` | НЕ смержена |

### Отработавшее — ветка уже в проде, worktree остался

15 штук: `dashboard-spec`, шесть `devtask-*` (2a464f, d0201f, 487d08, 549d1e,
f9a99c, 0f24fd), четыре `security-*`, `sprint0-lead-memory`,
`sprint0-secrets-p1p2`, плюс два под `.config\superpowers\worktrees\jarvis\`
(`chatter-onboarding`, `reboot-alert`).

> **Но снести их пачкой нельзя.** В семи из пятнадцати лежат НЕзакоммиченные
> правки в tracked-файлах (проверено `git status --porcelain` по каждому):
> `devtask-2a464f` (3), `devtask-d0201f` (2 + 1 untracked), `devtask-487d08`
> (4 + 5), `devtask-549d1e` (1 + 1), `devtask-f9a99c` (3), `security-middleware`
> (1), `reboot-alert` (3). Смерженная ветка ≠ пустой worktree: правка могла
> появиться ПОСЛЕ мержа и нигде больше не существовать.

Порядок уборки (по одному, не пачкой):

1. `git -C <worktree> status --porcelain` — если пусто, `git worktree remove`.
2. Если грязно — `git -C <worktree> diff` и решить осознанно: закоммитить в
   ветку, перенести в новую или выбросить. Только потом удалять.
3. В конце `git worktree prune` + удаление смерженных локальных веток.

Чисты и удаляемы прямо сейчас: `dashboard-spec`, `devtask-0f24fd`,
`security-enforce`, `security-h1-h3`, `security-key-inject`,
`sprint0-lead-memory`, `chatter-onboarding`.

## 4. Уроки в виде правил

1. **Инструмент, который «не смог проверить», обязан предлагать способ
   проверить.** Если единственная оставшаяся кнопка — обход контроля, то это не
   выбор человека, а решение инструмента за него. (28.07, targeted-гейт.)
2. **Глушить процесс по маске командной строки — способ однажды снести
   соседа.** Скоуп = точный PID или точное имя модуля. (24.07, kill-паттерн.)
3. **Карта фермы протухает быстрее, чем кажется.** Перед операциями над
   процессами сверять факт (`Win32_Process`, лог гардиана), а не память: логи
   лежат в ДВУХ папках, у сервиса два PID, у chatter — свой набор путей.
4. **Смерженная ветка не значит пустой worktree.** Проверять `status` перед
   удалением каждого.
