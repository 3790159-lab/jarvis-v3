# Spec — Vizir autonomy LOOP (приёмко-гейтед цикл над Hermes-шагом)

**Status:** DRAFT, awaiting Daniil's OK (spec→OK→TDD). Код ПОСЛЕ ОК.
**Worktree:** новый `vizir-loop` от прода `phase-4.0-unified-jarvis` (@ current). knee#1/#2 НЕ трогать (additive). Бот приоритет — живой процесс не перезапускается.
**Money:** спека $0. Обкатка под капом (центы), prod-леджер изолирован (`charge_logger=None`), live-прогон только с явным ОК Daniil — НЕ авто-loop.

---

## Ключевой принцип

Loop **не убирает контроль — он автоматизирует контроль.** Сегодня при провале приёмки
(`hermes_acceptance.py:86`) Vizir останавливается и Daniil **вручную** решает «повторить?».
Loop заменяет это ручное «ОК» набором **автоматических зубов**: жёсткий потолок попыток,
детектор «готово», детектор «не сходится», и общий cost-cap на ВЕСЬ цикл. Ниже — какой зуб
заменяет какое ручное решение.

| Ручное «ОК» (сегодня) | Автоматический зуб (loop) |
|---|---|
| «повторить попытку?» | внешний цикл `LoopController`, гейтед приёмкой |
| «ты решаешь продолжать» | `max_attempts` (жёсткий потолок, дефолт 3) |
| «результат готов?» | детектор завершения = `accept_hermes_chat` accepted → STOP |
| «хватит денег?» | reserve-before-attempt на ОБЩИЙ `budget_usd` цикла |
| «Hermes зациклился / не сходится?» | детектор no-progress (одинаковые reasons 2 витка → STOP) |
| «ты смотришь каждый шаг» | детерминированная приёмка КАЖДОГО витка (без платного LLM-судьи) |
| «агент ушёл не туда» | база-промт immutable + контракт-чек ловит goal-drift |
| финальный тупик | эскалация Daniil (`needs_approval`/stopped + отчёт) |

---

## 1. Loop-механика: НОВЫЙ внешний цикл над шагом (не расширение max_iterations)

Развилка (Daniil §1) решена: **два вложенных цикла, чёткое разделение.**

```
LoopController  (НОВЫЙ, app/services/vizir/loop.py)   ← внешний цикл: приёмко-гейтед retry
  └─ для attempt k=1..max_attempts:
       Coordinator.run(task_k, plan_k)                 ← существующий, coordinator.py:133 (НЕ трогаем)
            └─ ОДИН Hermes-шаг
                 └─ AIAgent.run_conversation(max_iterations=30)  ← внутренний цикл Hermes (НЕ трогаем)
       accept_hermes_chat(artifact)                    ← существующая приёмка, hermes_acceptance.py:86
```

- **Внутренний loop Hermes** (`max_iterations=30`) = «сделать ОДНУ попытку/артефакт своими tool-calls». Не трогаем.
- **Внешний loop Vizir** (`max_attempts≈3`) = «приёмка провалилась → повторить с фидбеком». Новое.

`LoopController` — **тонкая обёртка, НЕ параллельный мозг** (контракт vizir-arc.md §"controlled
executor, never a parallel brain"). Он НЕ ре-планирует динамически и НЕ изобретает шаги: он
пере-запускает **тот же одно-шаговый план** с дописанным фидбеком приёмки. Единственное «решение»
контроллера — детерминированная арифметика бюджета (reserve-before-attempt) и сравнение reasons.
Vizir остаётся единственным дирижёром; Coordinator/handler/приёмка не меняются.

### Один виток (attempt k)

1. **Build plan_k:** один Hermes-шаг, `prompt = base_prompt + feedback_k` (см. §4, впрыск).
2. **reserve-before-attempt** (§2): `remaining = budget_usd − loop_spent`; если `remaining < min_attempt_est` → STOP `stopped_budget`, **не стартуем виток, который не по карману**.
3. **Coordinator.run(task_k, plan_k)** → Report. Внутри — существующие money-зубы (pre-check + per-iteration reserve-before-spend + charge-after).
4. **loop_spent += report.total_cost** (сумма charged за виток).
5. **Приёмка:** `accept_hermes_chat(step_result)` → `AcceptanceResult(accepted, reasons)`.
6. **Развилка исхода** (§3): accepted → STOP `completed`; cost-cap breach в витке → STOP `stopped_cost_cap`; иначе проверить стоп-условия и, если можно, `feedback_{k+1} ← reasons`, `k+=1`.

---

## 2. Money-зубы на ВЕСЬ loop (главный риск автономности)

Требование Daniil: cost-cap держит **весь цикл**, не один шаг — иначе loop накрутит витки = деньги.
Ответ — **два уровня**, оба reserve-before-spend:

**Уровень A — loop (новое):**
- **Один ОБЩИЙ `Task.budget_usd` на все витки** (не per-attempt). `LoopController` ведёт `loop_spent` (сумма charged за все витки).
- **reserve-before-attempt:** перед витком k: если `budget_usd − loop_spent < min_attempt_est` → НЕ стартуем, STOP `stopped_budget`. Никогда не начинаем виток, который не можем оплатить целиком в пределах общего бюджета.
- **Проброс остатка вниз:** виток k запускается с `task_k.budget_usd = remaining`, чтобы существующий гейт Coordinator (`coordinator.py:171`, `_money_gate_allows`) физически не мог превысить общий бюджет даже при ошибке контроллера. Двойная страховка.

**Уровень B — attempt/step (существующее, coordinator.py:187–224, НЕ трогаем):**
- per-iteration `report_cost(delta)` reserve-before-spend; breach → `StepBudgetExceeded` → частичный charge `step_spent` + STOP.
- charge-after-success; refusal/fail НЕ списан (`coordinator.py:242`).

**«Loop не сходится и жжёт деньги виток за витком» → обрыв:**
- **Жёсткая верхняя граница траты = `budget_usd`.** Даже при вечной несходимости loop не может потратить больше `budget_usd` (reserve-before-attempt) и не сделать больше `max_attempts` витков — что раньше. Это математическая гарантия, не «надежда».
- **cost-cap breach внутри любого витка → STOP всего loop** (не retry). Money-событие → консервативно (та же асимметрия, что в vizir-arc.md: breach STOP, timeout CONTINUE). Виток, пробивший кап, не даёт «попробовать ещё раз за деньги».
- **Провальный виток без артефакта не списан** (charge-after-success): пустой/refusal виток стоит только реального LLM-спенда, уже смеченного per-iteration, никогда фантомного charge.

**Worst-case денег** = `min(budget_usd, max_attempts × max_attempt_cost)`, и всегда ≤ `budget_usd`. Для обкатки: центы.

---

## 3. Условия остановки (заменяют «ты решаешь продолжать»)

Loop останавливается по ПЕРВОМУ сработавшему. Каждый — детерминированный, со спай-зубом.

| # | Стоп-условие | Механизм | stopped_reason |
|---|---|---|---|
| 1 | **max_attempts** (жёсткий потолок, дефолт 3) | счётчик витков | `stopped_max_attempts` → эскалация |
| 2 | **завершено (accepted)** | `accept_hermes_chat().accepted is True` | `completed` (успех) |
| 3 | **бюджет исчерпан** | reserve-before-attempt (§2A) | `stopped_budget` → эскалация |
| 4 | **cost-cap breach в витке** | `StepBudgetExceeded` пойман в attempt | `stopped_cost_cap` → эскалация |
| 5 | **no-progress / не сходится** | reasons витка k == reasons витка k−1 (идентичная сигнатура провала) | `stopped_stalled` → эскалация |
| 6 | **loop wall-clock (backstop)** | суммарный дедлайн (сумма `timeout_s` уже ограничивает; опц. общий loop-deadline) | `stopped_timeout` → эскалация |

- **Детектор завершения (#2):** уже есть — `stopped_reason=="completed"` + детерминированный контракт-чек артефакта. Truncated-прогон (`max_iterations`/`cost_cap`/timeout) → НЕ accepted (`hermes_acceptance.py:92`), поэтому усечённый виток честно считается провалом, а не ложным «готово».
- **Детектор «Hermes зациклился» (#5):** если две попытки подряд дают **тот же набор reasons**, loop не сходится → STOP `stopped_stalled`. Ловит «крутит вхолостую» РАНЬШЕ, чем `max_attempts`, экономя деньги. (`max_attempts` — backstop, если reasons каждый раз чуть разные, но всё равно не accepted.)
- **Исход исчерпания (Daniil §3):** любой не-`completed` стоп → **эскалация Daniil** (не auto-report): loop помечает `needs_approval`/stopped и шлёт последний результат + reasons провала приёмки + сколько потрачено. Автономность = крутиться без ручного ОК на КАЖДОМ шаге, но финальный тупик показать человеку.

---

## 4. Behavior-зубы (заменяют «ты смотришь каждый шаг»)

- **Приёмка КАЖДОГО витка** — `accept_hermes_chat` (детерминированная, БЕЗ платного LLM-судьи). Не accepted → reasons → фидбек/эскалация. Уже есть; loop вызывает per-attempt.
- **Впрыск причин приёмки (Daniil §5, реком.):** база-промт **immutable**, фидбек **дописывается**:
  `feedback_{k+1} = "Предыдущая попытка провалила проверки: {reasons}. Исправь их, остальное сохрани."`
  Это даёт **направленную сходимость** (смысл loop), а не слепой повтор.
- **«Агент ушёл не туда» (goal-drift):** ловится двумя способами без нового кода-судьи:
  1. **База-промт immutable** — Hermes НЕ переписывает цель; фидбек только дописывается → дрейф не накапливается виток за витком.
  2. **Детерминированный контракт-чек** (`check_chat_acceptance`, 8 проверок) = и есть детектор дрейфа: переосмыслил задачу → артефакт не по контракту → приёмка красная → reasons → эскалация.
- **Изоляция (knee#2, уже в проде @002aaef)** держит исполнение в контейнере. Loop наследует её без изменений; для витков с исполнением guard блокирует шаг ДО спавна (Hermes не может уйти на хост). **Обкатку loop делаем на knee#1 (генерация HTML, без исполнения)** — не совмещаем два риска сразу; loop+knee#2 (исполнение) — отдельный поздний виток.
- **Reportable, never auto-resumed:** состояние loop (attempt, loop_spent, история reasons) персистится per-attempt; прерванный loop **репортится, не воскрешается молча** (контракт vizir-arc.md #5).
- **События:** loop эмитит свои `on_event` (`loop_attempt_started`, `loop_attempt_rejected{reasons}`, `loop_stopped{reason,spent}`) — драйвер (CC сейчас, Jarvis позже) рендерит. Transport-agnostic ядро сохранено.

---

## 5. Компоненты (этот виток арки)

- `app/services/vizir/loop.py` — `LoopController` + `LoopConfig(max_attempts, min_attempt_est, loop_deadline_s=None)` + `LoopReport(stopped_reason, attempts, loop_spent, last_result, accepted, reasons_history)`. Инъектируемые `run_coordinator_fn`/`accept_fn` для $0-тестов (как `run_fn` в handlers_hermes). Money-решения = чистая арифметика, не «мозг».
- Приёмка и Coordinator и handler — **переиспользуются как есть**, не меняются.
- (Опц.) тонкий loop-driver шов позже; сейчас CC-driver гоняет loop так же, как один task.

---

## 6. План обкатки (доказать, что loop ОСТАНАВЛИВАЕТСЯ, не разоряет, не убегает)

**TDD, спай-зубы ПЕРВЫМИ** (мутируем предохранитель → тест обязан покраснеть), $0 на моках:

- **A. Exit=completed:** задача, которую Hermes проходит с 1-й → STOP `completed`, ~1 виток, spent≈1 attempt.
- **B. Exit=max_attempts:** контракт невыполним → ровно `max_attempts` витков → STOP + **эскалация Daniil**; НЕ больше потолка.
- **C. Exit=budget:** крошечный `budget_usd` → reserve-before-attempt срабатывает → STOP `stopped_budget` ДО перетрат; loop_spent ≤ budget.
- **D. Exit=cost_cap:** форс breach в витке → STOP `stopped_cost_cap` немедленно, БЕЗ retry; частичный charge.
- **E. Exit=stalled:** одинаковые reasons два витка → STOP `stopped_stalled` раньше max_attempts.
- **Спай-зубы (каждый предохранитель):**
  - сломать reserve-before-attempt → перетрата поймана красным;
  - сломать no-progress детектор → тест ловит, что stall-exit НЕ сработал (backstop=max_attempts всё равно держит) — доказываем, что зуб реальный;
  - сломать «cost_cap → STOP» (сделать retry) → тест краснеет на лишнем витке за деньги;
  - сломать immutable-базу (позволить перезапись цели) → drift-тест краснеет.

**Live-обкатка (после зелёных моков, с явным ОК Daniil):**
- Задача = knee#1 генерация (без исполнения), `budget_usd ≈ $0.50` (маленький — loop точно не улетит на первом живом), `max_attempts=2–3`, prod-леджер изолирован (`charge_logger=None`).
- Доказать вживую: (1) loop сходится за ≤N витков ИЛИ честно эскалирует; (2) суммарная трата ≤ `budget_usd`; (3) ни один предохранитель не пробит; (4) `C:\jarvis` git clean, бот цел.
- НЕ авто-loop: запуск вручную, под наблюдением, один прогон.

---

## 7. Границы / rollback

- Новый worktree `vizir-loop`, additive, коммит на виток, phase-boundary коммит. Плохой виток = reset к шву.
- Изолированный модуль `loop.py` → минимальный blast radius. Coordinator/handler/приёмка/knee#1/#2 не редактируются.
- Бот приоритет: живой процесс не трогаем, prod-леджер изолирован во всех тестах и live-прогоне.
- $20 autonomy session cap над всем; sam-vs-ask граница сохранена (финальный тупик → человек).
