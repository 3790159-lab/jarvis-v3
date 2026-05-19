# Jarvis V3 Voice Audit — Phase E.1 Discovery

**Generated:** 2026-05-19
**Scope:** All user-facing communication channels across Jarvis V3
**Mode:** Read-only audit. No source modified.

---

## Executive Summary

- **Subsystems found:** 16 distinct user-facing emitters across 4 categories (chat, alerts, generated content, generated automations).
- **Message-emitting code locations:** ~30 Python modules. The two biggest are `tools/jarvis_smart_telegram_control.py` (5,272 lines, ~50+ user-facing strings sampled) and `app/handlers/persona_handler.py` (~900 lines).
- **Recommended for rewriter (SAFE):** 11 subsystems — all interactive Telegram surfaces and system alerts.
- **Must NOT rewrite (STRUCTURAL):** 5 subsystems — Google Workspace (Gmail/Calendar/Sheets/Drive), n8n workflow JSON, Figma JSON, spreadsheet generators, InfluencerStudio API payloads.
- **Single best chokepoint:** `app/services/identity_core.py::get_system_prompt()` already exists and is wired through both `conversation_brain` and `quick_answer`. Adding personality there gives Jarvis a coherent voice for *every* LLM-generated reply in one edit. The harder problem is the ~200+ hand-written Russian strings scattered across handlers.

### Top 3 pain points

1. **Raw exception text dumped to the user.** Pattern `send(chat_id, f"Ошибка: {exc}")` appears 25+ times in `jarvis_smart_telegram_control.py` alone (lines 3455, 3592, 3634, 3739, 3772, 4230, 4246, 4262–4505, 4564). Users see `KeyError: 'figma_url'` style messages.
2. **Mojibake in critical alerts.** `app/services/system_watchdog.py` lines 115, 183, 187, 216–245 contain Russian strings stored as corrupted CP1251→UTF-8 bytes (`РџСЂРёС‡РёРЅР°` instead of `Причина`). The watchdog itself sends garbled Russian to the operator when the backend dies — exactly when clarity matters most.
3. **Inconsistent addressing.** Same handler file mixes "вы" (`Отправьте`, `Введите`, `Ответьте`) with "ты" (`Запусти`, `Сначала сгенерируй`, `Напиши`, `жди`). `persona_handler.py` line 374 (`Сначала сгенерируй фото`) sits 30 lines from line 388 (`Ответьте "да" / "нет"`). No system-wide convention.

### Top 3 quick wins

1. **Centralize error rendering.** Replace the `f"Ошибка: {exc}"` pattern with a `humanize_error(exc, context)` helper. One change collapses 25+ leaks into one place that can be voice-rewritten.
2. **Inject personality into `identity_core.JARVIS_CORE_IDENTITY`.** Current core prompt is utilitarian ("Be concrete, useful and action-oriented"). Add Stark-Jarvis traits: dry wit, formal address, deferential confidence. Every Ollama/OpenAI/Claude reply through `quick_answer` and `conversation_brain` inherits the new voice for free.
3. **Fix watchdog mojibake.** Rewrite the corrupted strings as proper UTF-8 (or English ASCII). System alerts are seen rarely but in high-stress moments — they're disproportionately important.

---

## Subsystem Inventory

### 1. Main Telegram bot — `app/telegram_bot.py`
- **Role:** Thin polling client. Forwards user text to `/api/respond`, sends backend's reply verbatim. Also handles `/start`, `/id`, `/health`, `/gmail`, `/calendar`, `/addevent`.
- **Send paths:** `bot.reply_to`, `bot.send_chat_action`.
- **Example messages:**
  - Help (line 127–133): `"Jarvis V3 online.\n\nКоманды:\n/id — показать chat id\n/health — проверить backend\n\nМожно писать обычные сообщения."`
  - Error (line 181): `"Backend слишком долго отвечает. Проверь /health и endpoint /api/respond."`
  - Error (line 187): `f"Ошибка запроса к backend:\n{type(e).__name__}: {e}"` ← raw exception leak
  - Empty input (line 166): `"Пустое сообщение."`
- **Tone:** Clinical, terse. Mixed addressing (`Проверь` ты + `Можно писать` neutral). Bug at line 320: `f"Событие создано:\`n{result.get('html_link')}"` — literal backtick-n instead of `\n` (PowerShell escape leaked into Python).

### 2. Smart Telegram control — `tools/jarvis_smart_telegram_control.py`
- **Role:** The real "brain" bot — 5,272 lines wiring dozens of `cmd_*` and `_handle_*` handlers: `cmd_design`, `cmd_figma_queue`, `cmd_create_app`, `cmd_landing`, `cmd_smart_photo`, `cmd_pro_food`, `cmd_landing_brief`, `cmd_simple_game`, `_handle_n8n_command`, `_handle_remind_command`, `_handle_brief_command`, `_handle_logs_command`, `_handle_selfcheck_command`, `_handle_errors_command`, `_handle_improve_command`, `_handle_cowork_command`, and many more.
- **Send primitives:** Custom HTTP wrappers `send(chat_id, text)`, `send_with_feedback`, `send_with_keyboard`, `edit_message`, `_send_photo_url`, `_send_local_video`, `_send_local_photo`, `_send_local_media_group`.
- **Examples (line:string):**
  - 486: `"❌ Не удалось скачать прикреплённое фото."`
  - 1554: `"🧠 Анализирую задачу..."`
  - 1571: `f"✨ Готово!\n\n{final}"`
  - 1574: `f"❌ Smart Router ошибка: {e}\nПробую стандартный планировщик..."` ← exception leak
  - 1621: `"✅ Все шаги выполнены!\n\n" + plan.results_summary()`
  - 1956: `"🛠 Запускаю AI Engineer: анализ, риски, архитектура, план..."`
  - 3076: `f"📤 Задача отправлена в Cowork\nID: {task_id[:8]}...\n\nCowork ответит автоматически. Ожидай уведомления."`
  - 3317: `"❌ Night Autonomy не активирована — нет scheduled_tasks.json"` ← internal filename leaked
  - 3410: `"🔄 Перезапускаю бот через 5 секунд... Watchdog поднимет новый процесс."`
  - 3982: `f"🧠 Памяти о тебе: {count} сообщений сохранено."`
  - 4090: `f"🧠 Smart Router {'включён' if sub == 'on' else 'выключен'}."`
  - 4564: `f"❌ Photo Studio error: {_ps_err}"` ← raw exception
  - 4567: `"Не знаю такую команду. Напиши /smart_help"`
- **Tone:** Heavy emoji (🧠 ✨ ❌ ✅ ⚠️ 🌙 🔄 📊 📤 🛠 🎭), short imperatives, mixed Cyrillic+Latin (`Smart Router`, `Watchdog`, `Photo Studio`), bullet-style status reports.

### 3. Persona handler — `app/handlers/persona_handler.py`
- **Role:** Commands for AI persona creation, LoRA training, and image/video generation: `/create_persona`, `/cancel_persona`, `/train_lora`, `/lora_status`, `/list_loras`, `/persona_photo`, `/persona_video`, `/persona_redo`, `/persona_engine`, `/cancel_lora`, `/me_seed`, `/me_done`, `/me_swap_photo`, `/me_swap_video`.
- **Send primitives:** Injected `_send`, `_send_photo`, `_send_video`; wrapped in `_safe_send*` helpers.
- **Examples:**
  - 140: `"Создание новой AI-персоны.\n\n" + dialog.get_current_question()`
  - 224: `f"Персона «{dialog.name}» создана. ID: {dialog.persona_id}"`
  - 269: `f"Генерация: {done}/{total} фото готово..."` (progress callback every 5 photos)
  - 291: `f"Превышен дневной лимит расходов. {exc}"` ← exception leak
  - 298: `"Ошибка генерации. Попробуйте позже."` (good: generic, polite)
  - 317–320: training-confirmation block — `"Тренировка запущена!\nЯ уведомлю вас когда будет готово (~20 минут).\n\nПроверить статус: /lora_status {persona_id}"`
  - 374: `"Сначала сгенерируй фото: /create_persona"` ← ты-addressing
  - 388: `"\nЗапустить тренировку? Ответьте \"да\" / \"нет\""` ← вы-addressing (same handler, 14 lines apart)
- **Tone:** Mostly polite вы, but slips into ты several times. Long instructional blocks. Heavy use of CLI-like commands shown to user.

### 4. Face swap handler — `app/handlers/face_swap_handler.py`
- **Role:** `/swapbatch_source`, `/swapbatch_batch`, `/swapbatch_go`, `/swapbatch_animate_yes/no`, `/swapbatch_cancel`, `/swapbatch_status`.
- **Architecture:** **Transport-agnostic** — methods return a `HandlerReply` dataclass (text + photos + videos). The bot wiring in `tools/jarvis_smart_telegram_control.py` actually sends. **This is the best architectural pattern in the codebase for rewriter wiring.**
- **Examples:**
  - 58–72: `HELP_TEXT` — long structured help with command list + flow diagram
  - 106: `"📸 Жду фото с твоим лицом. Пришли одно фото — лицо должно быть чётко видно."`
  - 141: `f"📋 Состояние: {sess.status}..."` (status report)
  - 199: `format_cost_report_ru(...)` — `"📊 Оценка батча\nSource: {n} лицо ✅\nTargets: {m} фото..."`
  - 217: `f"❌ Ошибка swap: {exc}"` ← exception leak
  - 246: `f"✅ Swap завершён: {succeeded} успешно"`
- **Tone:** Concise, emoji-heavy, ты-addressing (`Пришли`, `пришли альбом`). Cost-report formatting is good (clear bullets, currency, ETA).

### 5. Notifications helper — `app/services/notifications.py`
- **Role:** Unified sync+async Telegram alert sender. Used by RunPod guardian and watchdog callers.
- **Default prefix:** `🔔 Jarvis` prepended to every alert. Returns `False` instead of raising on failure (best-effort).
- **No hardcoded user text** — pure transport layer. Whatever the caller passes becomes the alert.
- **Note:** Centralizing all admin alerts through this module would simplify rewriter wiring later.

### 6. System watchdog — `app/services/system_watchdog.py`
- **Role:** Health-check loop. Sends Telegram alert + restarts services on backend/disk/memory failure.
- **🚨 CRITICAL ISSUE — Mojibake.** The Russian strings on disk are corrupted (CP1251 source saved as Latin-1, then re-decoded UTF-8). Examples (raw on-disk):
  - 115: `f"рџљЁ Jarvis Watchdog:\n{text}"` (should be `🚨 Jarvis Watchdog:`)
  - 183: `"Bot heartbeat stale вЂ" РїРµСЂРµР·Р°РїСѓС‰РµРЅ С‡РµСЂРµР· start_jarvis.ps1"` (should be `Bot heartbeat stale — перезапущен через start_jarvis.ps1`)
  - 216–245: Multiple `РџСЂРёС‡РёРЅР°`, `Р’С‹СЃРѕРєРѕРµ`, `РџРµСЂРµР·Р°РїСѓС‰РµРЅ` etc.
- **Impact:** The operator gets unreadable garbled text *exactly* when something is broken.

### 7. Self-healing — `app/services/self_healing.py`
- **Role:** Disk cleanup, backups, memory monitoring; notifies admin on remediation actions.
- **Prefix:** `🔧 Self-Healing:` (line 247)
- **Examples:**
  - 292: `f"Disk low ({free_gb:.1f}GB free) — cleaned {cleaned} log files"` ← English
  - 299: `f"High memory: {mem_mb:.0f}MB — restart recommended"` ← English
- **Tone:** Clinical English. Inconsistent with the rest of Jarvis (which is mostly Russian).

### 8. Internet table builder — `app/services/jarvis_telegram_file_tools.py`
- **Role:** Builds a CSV/XLSX from a search query, then sends as a Telegram document with caption.
- **Send path:** Direct `sendDocument` multipart POST (line 34).
- **Example caption (line 262):** `f"Jarvis internet table\nQuery: {query}\nRows: {len(rows)}"` ← also contains a `NameError` bug (`rows` is undefined; should be `smart_rows` or `rows_count`).
- **Tone:** Terse, English, identifier-style.

### 9. Operator task center — `app/services/jarvis_operator_task_center.py`
- **Role:** Builds periodic operator status reports and sends them via Telegram (line 207).
- **Example template (line 166–192):**
  ```
  🤖 Отчёт Jarvis

  Активные задачи: {N}
  Завершённые задачи: {M}
  Ошибки: {K}

  Последняя night-сессия: {status}
  Успешных apply: {n}
  Итог: {operator_summary}

  Что дальше:
  • {item1}
  • {item2}
  ```
- **Tone:** Structured, professional, neutral — feels like a status dashboard rendered as chat. The 🤖 + "Отчёт" framing is one of the more "voice-able" surfaces.

### 10. n8n super agent — `app/services/jarvis_n8n_super_agent.py`
- **Role:** Generates n8n workflow JSON (not sends messages itself, but produces workflows that send Telegram messages).
- **Generated default Telegram text (line 268):** `'Hello from Jarvis n8n Super Agent'` (fallback when payload has no `text` or `message` field).
- **Generated workflow `note` fields (lines 248, 281, 305, 320):** `'External HTTP Request node executed successfully'`, `'Telegram message request completed'`, `'Safe probe receiver'`.
- **⚠️ Mixed risk:** the `note` fields go into JSON responses other systems parse — must NOT be rewritten. The `text`/`message` payload defaults *do* reach users — could be rewritten.

### 11. Photo studio Telegram — `tools/photo_studio_telegram.py` (1,117 lines)
- **Role:** Image generation pipeline with Telegram delivery. Did not deep-read; key surface is file download (`getFile`) at lines 1108, 1114 and the photo-studio bot integration.
- **Note:** Worth a dedicated audit later — large surface.

### 12. Task completion watcher — `scripts/jarvis_task_completion_watcher.py`
- **Role:** Background loop that polls execution artifacts and sends a Telegram message when a task completes.
- **Example template (line 119–151):**
  ```
  ✅ Задача завершена

  ID: {task_id}
  Статус: {status}

  Задача: {objective[:600]}

  Lane: {lane}
  n8n workflow: {workflow_id}
  Тип workflow: {workflow_kind}
  Run ID: {run_id}
  Google Sheet: {sheet_url}

  ⚠️ Проверка честности результата:
  {safe_message}

  Я сам сообщил о завершении, отдельно спрашивать не нужно.
  ```
- **Tone:** Last line is one of the few personality-bearing strings in the codebase (`"Я сам сообщил..."` reads like a competent assistant). Otherwise field-dump style.

### 13. Operator Telegram bridge — `scripts/jarvis_operator_telegram_bridge.py`
- **Role:** On-demand operator commands (n8n readiness, latest run, etc.). Sends formatted text reports.
- **Example (line 68–75):** `"🧩 n8n readiness\n\nСтатус: {status}\nBase URL: {url}\nAPI key: {есть|нет}\nHTTP root: {ok|fail}\nPublic API: {ok|fail}\n\nСледующий шаг: {next}"`

### 14. Conversation brain — `app/services/conversation_brain.py`
- **Role:** Backend's `/api/respond` reply generator. Routes to Ollama (local), OpenAI, or hand-written Russian fallback templates.
- **🎯 PRIMARY VOICE SURFACE.** Every casual chat message → here.
- **Fallback strings (line 74–113):** Hard-coded Russian replies for "что ты умеешь", "на каком мы этапе", "что улучшать дальше", "как успехи". Currently neutral-professional ("Сейчас я умею работать как Jarvis Supervisor: создавать goal..."). No personality.
- **Routes that bypass LLM (line 229, 238):** When intent is `task` or `status`, returns canned `"Похоже на задачу. Такой текст лучше передать в supervisor goal-routing."` style. These read as router-internal and should be reworded.

### 15. Quick answer — `app/services/quick_answer.py`
- **Role:** Direct Claude Haiku call for simple factual Q&A.
- **System prompt (line 72–94):** Already personality-aware (`"You are Jarvis V3 — Daniil Lapin's personal AI assistant"`). Anti-hallucination rules in English; user-facing answer follows the user's language. **Good base, no voice yet.**
- **Failure fallback:** Returns `None`; caller falls back to research path.

### 16. Identity core — `app/services/identity_core.py`
- **Role:** Single source of truth for system prompts. `get_system_prompt(role, lang, provider_hint)` returns the assembled prompt for every LLM call in the stack.
- **Currently:** Defines what Jarvis is *not* (not Perplexity, not Luxify) and structural rules ("respond as Jarvis", "explain real system status", "be concrete and action-oriented"). **Zero personality markers.**
- **🎯 This is THE chokepoint** for LLM-generated voice. One edit propagates everywhere.

### Other surfaces (low priority / niche)

| Module | Role | Voice-affected? |
|---|---|---|
| `app/services/figma_brief_generator.py` | Claude-generated design brief + Russian preview (`format_brief_preview`) | Preview line 233–239 is user-facing — small surface |
| `app/services/restaurant_mode.py` | Claude-generated marketing captions for food photos | LLM-driven; rewriter would set tone via prompt |
| `app/services/party_mode.py` | Event invitations | LLM-driven |
| `app/services/landing_content_generator.py` | Landing-page copy | LLM-driven, not chat |
| `app/services/smart_prompts.py` | Prompt enhancement | Internal, not user-facing |
| `app/services/google_workspace_tools.py` | Gmail send / Calendar create | **STRUCTURAL** — emails go to real people, not Daniil; do not rewrite |
| `app/services/spreadsheet_service.py` | XLSX writers | **STRUCTURAL** — data only |
| `app/services/jarvis_v5_content_factory.py` | Drive uploads + summary JSON | **STRUCTURAL** — content files |
| `app/services/figma_client.py` | Figma JSON wireframes | **STRUCTURAL** |
| `app/response_formatter.py` | Post-processes LLM output before send | Has an oddity: English→Russian word substitution table at lines 46–55 — hacky translation layer; flag for review |

---

## Tone Analysis

### Overall

| Dimension | Observation |
|---|---|
| **Language** | ~85% Russian, ~15% English (system alerts, internal labels, some error strings) |
| **Emoji frequency** | Very high in `jarvis_smart_telegram_control` and face_swap; moderate in persona; low in conversation_brain; none in google/spreadsheet |
| **Addressing** | Inconsistent. Same file mixes `ты` (`Запусти`, `Проверь`, `Напиши`) and `вы` (`Отправьте`, `Введите`, `Ответьте`). No documented convention. |
| **Personality markers** | Almost none. Only `scripts/jarvis_task_completion_watcher.py:151` (`"Я сам сообщил о завершении, отдельно спрашивать не нужно."`) reads like a character. |
| **Jargon level** | High. Users see: `workflow_id`, `lane`, `Smart Router`, `decision_id`, `Cowork watcher`, `mesh execution`, `Engine: kling_v21`, raw exception class names, file paths (`scheduled_tasks.json`), API endpoint names (`/api/respond`). |
| **Length** | Bimodal — very short (`"Пустое сообщение."`) or very long structured reports (help texts, status dashboards). Few medium-length conversational replies. |
| **Formality** | Mostly neutral-professional. No warmth. No humor. No deference. No initiative. |
| **Consistency** | Each subsystem has its own micro-style. Heavy emoji vs. dry English alerts vs. polite Russian dialog — no unifying voice. |

### What "Stark-Jarvis" would add that's currently missing

- Formal "вы" + "сэр" address style (or "Daniil" by name)
- Anticipatory phrasing ("Я уже" / "Позвольте предложить")
- Calm dry wit on errors ("Похоже, RunPod снова решил подумать о вечном.")
- Confident success language ("Сделано." not "✅ Готово!")
- Less emoji, more verbal elegance
- Variety in error responses (not all `f"Ошибка: {exc}"`)

---

## Pain Points (with locations)

### Critical

1. **Raw exception text leaked to user.** 25+ instances in `jarvis_smart_telegram_control.py` (lines 3455, 3592, 3634, 3739, 3772, 3829, 4230, 4246, 4262, 4278, 4294, 4310, 4326, 4377, 4393, 4409, 4425, 4441, 4457, 4473, 4489, 4505, 4564); plus `app/telegram_bot.py:187,193`; `app/handlers/persona_handler.py:291, 342, 361, 411, 433, 451, 496, 565, 627, 693, 759, 809, 851`.
2. **Mojibake in watchdog.** `app/services/system_watchdog.py:115, 183, 187, 216, 220, 232, 242` — corrupted Russian in critical alerts.
3. **Inconsistent addressing inside one file.** `app/handlers/persona_handler.py:374` (ты) next to `:388` (вы); `:172, 186` (вы) vs `:455` (ты).

### Moderate

4. **Bug at `app/telegram_bot.py:320`** — `f"Событие создано:\`n{...}"` — literal backtick-n (PowerShell escape leaked into Python source); user sees `\n` instead of newline.
5. **`response_formatter.py:46–55`** — a hand-coded English→Russian word substitution table runs on every reply. A workaround for LLM language drift; deserves attention from the voice layer (the rewriter should set language via prompt, not post-hoc substitution).
6. **Internal identifiers visible to user:** `task_id`, `workflow_id`, `decision_id`, `chat_id`, file paths, `scheduled_tasks.json`, class names. Acceptable for power-user mode; should be gated for the standard voice.
7. **`jarvis_telegram_file_tools.py:262`** — `f"Jarvis internet table\nQuery: {query}\nRows: {len(rows)}"` references undefined `rows`; will raise `NameError` at runtime, replacing the success message with a Python crash.

### Low

8. **`self_healing.py`** notifications are English while everything else is Russian.
9. **`face_swap_handler.py:217, 261`** — `f"❌ Ошибка swap: {exc}"` leaks exception (face_swap is otherwise the cleanest module).
10. **`jarvis_smart_telegram_control.py:3403`** — `"❌ Скрипт запуска backend не найден. Перезапусти вручную."` — gives an action but no concrete command.

---

## Risk Map

### ✅ SAFE TO REWRITE (chat-facing, no downstream parsers)

| Subsystem | File(s) | Notes |
|---|---|---|
| Main bot replies | `app/telegram_bot.py` | Forwards `reply` from backend; rewrite at backend reply layer instead |
| Smart Telegram control | `tools/jarvis_smart_telegram_control.py` | Largest surface. Many command handlers. |
| Persona handler | `app/handlers/persona_handler.py` | All `_safe_send` strings |
| Face swap handler | `app/handlers/face_swap_handler.py` | Transport-agnostic — easiest to wire |
| Watchdog alerts | `app/services/system_watchdog.py` | Fix mojibake while rewriting |
| Self-healing alerts | `app/services/self_healing.py` | Also harmonize EN→RU |
| Operator task center reports | `app/services/jarvis_operator_task_center.py` | `build_telegram_report` is one function |
| Task completion watcher | `scripts/jarvis_task_completion_watcher.py` | `format_msg` is one function |
| Operator Telegram bridge | `scripts/jarvis_operator_telegram_bridge.py` | Several formatter functions |
| Conversation brain fallback | `app/services/conversation_brain.py` | Hand-written replies + LLM system prompt |
| Identity core system prompt | `app/services/identity_core.py` | **Single best lever for LLM-driven voice** |

### 🚫 DO NOT REWRITE (structural / downstream consumers)

| Subsystem | Why |
|---|---|
| Google Workspace — Gmail/Calendar | Emails go to **real people**, not Daniil. Subject + body must stay as the user (or a tool) wrote them. |
| Google Sheets / spreadsheet_service | Cell values are data; downstream formulas + filters parse them. |
| Drive uploads (`jarvis_v5_content_factory`) | Filenames + `summary.json` keys are consumed by downstream tooling. |
| n8n workflow JSON node `note`/identifier fields | Other workflows + dashboards parse these. |
| Figma client wireframe JSON | Imported into Figma — names + types are structural. |
| InfluencerStudio API payloads | API contract — provider parses. |
| Backend API response keys (`reply`, `intent`, `mode`, `source`, `confidence`) | Telegram bot parses these. The *value* of `reply` is in scope; the key names are not. |
| LLM system prompts to non-personality models (vision, classifier) | Risk of changing classification behavior. Rewriter targets *output*, not classifier input. |

### ❓ UNCERTAIN — needs Daniil's input

- **n8n super agent generated Telegram text** (line 268, default `"Hello from Jarvis n8n Super Agent"`). The workflow is generated *by* Jarvis but sends *as* Jarvis to the same chat. Rewriter probably yes — but verify no external consumer keys on that exact string.
- **Figma brief preview** (`format_brief_preview`). Shown to Daniil in Telegram before queueing — rewriter yes. But the `project_name`, `style`, `palette` field labels go into Figma later — leave those.
- **Restaurant/party/landing content generation.** These produce *content for others to see* (restaurant marketing posts, party invitations, landing copy). Voice rewriter for "Jarvis explains what he did" — yes. Voice rewriter for the generated content itself — no, that has its own brand voice per use case.
- **YouTube channel management.** Daniil mentioned it; no code found in current repo. Likely manual/in-progress. Confirm whether YouTube comments/descriptions ever flow through Jarvis text generation.
- **English in `self_healing.py`** — keep English (technical alerts) or harmonize to Russian (consistency)?
- **Power-user mode.** Should `task_id`, `workflow_id`, file paths stay visible in some channels (e.g. operator reports) or always be hidden behind humanized phrasing?

---

## Recommended Voice Profile

**Working name:** *Jarvis V3 — formal, dry, capable.*

| Trait | Recommendation |
|---|---|
| **Address** | "вы" + occasionally "Daniil" by name. Avoid "ты" entirely. |
| **Self-reference** | "Я" — first person, never "Jarvis говорит" or third-person. Confident assertion of agency. |
| **Tone** | Calm. Deferential confidence. Dry wit reserved for failure modes (where it lands best). Never apologetic-grovelling, never enthusiastic-bouncy. |
| **Emoji policy** | Sparing. One per message max, only when functional: ✅ for completion, ⚠️ for warning, 🚨 for critical. No 🧠 ✨ 🎭 🛠 decorative emoji. |
| **Error voice** | "Не получилось — {short human explanation}. Что попробуем дальше: {action}." Never raw exception. Vary phrasing. |
| **Success voice** | "Готово." or "Сделано. {1-line concrete result}." Avoid "✅ Готово!\n\n" boilerplate. |
| **Progress voice** | Quiet. "Работаю над этим." Reserve detailed progress for genuinely long jobs (>30s) and offer a status command. |
| **Help / instructions** | Numbered or bulleted only when there's genuine sequence. Otherwise prose. Always end with a single concrete next action. |
| **Length** | Short by default (2–3 sentences). Long allowed for explicit reports. Avoid medium-length filler. |
| **Identifiers** | Hide by default. Show on `/debug` or when user explicitly asks. |
| **Language** | Russian everywhere user-facing; English only for technical labels (status codes, model names, URLs). |
| **Humor budget** | One subtle observation per ~10 messages, and only on neutral or failure events. Never during success — confidence speaks louder. |

### Example rewrites

| Current | Proposed |
|---|---|
| `"❌ Не удалось скачать прикреплённое фото."` | `"Фото не дошло. Попробуйте отправить ещё раз — иногда Telegram теряет вложения."` |
| `"❌ Smart Router ошибка: {e}\nПробую стандартный планировщик..."` | `"Smart Router споткнулся. Перехожу на резервный планировщик."` |
| `"Backend слишком долго отвечает. Проверь /health и endpoint /api/respond."` | `"Backend задумался дольше обычного. Проверю состояние и вернусь."` |
| `"✅ Все шаги выполнены!\n\n" + summary` | `"Готово. " + summary` |
| `"Пустое сообщение."` | `"Я слушаю — что нужно?"` |
| `"Не знаю такую команду. Напиши /smart_help"` | `"Не узнал команду. `/smart_help` покажет, что я умею."` |

---

## Implementation Recommendation

### Architectural placement

Two complementary layers — pick the wiring point per subsystem:

**Layer A — LLM system prompt (cheap, high leverage).**
Edit `app/services/identity_core.py::JARVIS_CORE_IDENTITY` and `_ROLE_SUFFIX_RU` to include voice rules. Every reply from `quick_answer`, `conversation_brain` (Ollama + OpenAI paths), `figma_brief_generator`, `restaurant_mode`, `party_mode`, `landing_content_generator`, `claude_helper`, `claude_api_call` inherits the new voice. **One edit; ~10 modules affected.**

**Layer B — Hand-written string rewriter (expensive, but covers handlers).**
A new module `app/services/voice_rewriter.py` exposing one function:

```python
def humanize(text: str, *, kind: Literal["error", "success", "progress", "help", "info"]) -> str:
    """Rewrite a hand-written or template-generated message in Jarvis voice."""
```

Implementation options (pick when implementing):
- **Cheapest:** Static lookup table for common patterns (`"Ошибка: {x}"` → varied alternatives) + regex strip of internal identifiers.
- **Best quality:** Wrap with Haiku call using a focused system prompt; cache aggressively by message hash to avoid per-message latency cost.
- **Hybrid (recommended):** Static rules + Haiku fallback for unrecognized templates, with a `JARVIS_VOICE_BYPASS=1` env var for debug mode.

Wire into `tools/jarvis_smart_telegram_control.py::send()` as a single chokepoint — every text message in that file passes through `send()`.

### Wiring order (highest impact first)

| # | Step | Impact | Effort | Risk |
|---|---|---|---|---|
| 1 | Inject voice into `identity_core.JARVIS_CORE_IDENTITY` | All LLM replies + chat fallbacks | XS (1 file, one prompt) | Low — easy to roll back via env flag |
| 2 | Fix watchdog mojibake | Critical-moment readability | XS | None |
| 3 | Add `voice_rewriter.humanize()` skeleton + static rules | ~50% of handler error strings | S | Low — pure-Python rewrite |
| 4 | Wire `humanize()` into `jarvis_smart_telegram_control.send()` | The biggest handler file | M | Medium — must not break existing flows; needs bypass switch |
| 5 | Replace `f"Ошибка: {exc}"` pattern with a single helper | Eliminates raw exception leaks | M | Low — mechanical refactor |
| 6 | Wire `humanize()` into `persona_handler._safe_send` | Persona/LoRA dialog flows | S | Low |
| 7 | Add Haiku fallback path to `voice_rewriter` with caching | Polishes long-tail strings | M | Medium — adds latency; cache must work |
| 8 | Convert `self_healing.py` strings to Russian + voice | Consistency | XS | None |
| 9 | Replace `response_formatter` substitution table with proper voice prompt | Removes hack | S | Low |
| 10 | Audit `tools/photo_studio_telegram.py` (1,117 lines) | Niche but not yet inventoried | M | Low |

Total: roughly **2–4 engineering days** for steps 1–6 (covers ~85% of user-facing surface). Steps 7–10 are polish.

---

## Open Questions for User (Daniil)

1. **Address style: "вы" vs "ты" vs name?** Recommendation is "вы" + occasional "Daniil". Confirm.
2. **Emoji policy.** Recommendation: sparing, functional only. Confirm or override.
3. **English fallback.** When LLM is unavailable and Jarvis falls back to hand-written replies, should they stay Russian or switch to English? (Current: Russian.)
4. **Identifier visibility.** Hide `task_id`/`workflow_id`/file paths by default, expose under `/debug`? Or keep them visible to you as the operator?
5. **n8n-generated Telegram default `"Hello from Jarvis n8n Super Agent"`** — is this string parsed anywhere downstream, or safe to rewrite?
6. **YouTube subsystem.** No actual YouTube API integration found in the repo. Is there an out-of-tree script, an n8n workflow, or manual workflow? Voice may or may not apply.
7. **Watchdog alerts language.** Russian (with mojibake fixed) or English (technical-monitor convention)?
8. **Restaurant / party mode marketing copy.** The marketing output speaks to the *end customer*, not Daniil. Keep its current tone (energetic, emoji-friendly) and only rewrite "Jarvis explains what he did" messages?
9. **Voice latency budget.** OK to add ~200–400ms (cached Haiku call) to every outbound message, or strict zero-latency requirement (static rules only)?
10. **Bypass mechanism.** `JARVIS_VOICE_BYPASS=1` env var to disable voice layer for debugging — acceptable design?

---

## Appendix — Files touched during this audit (read-only)

```
app/telegram_bot.py
app/handlers/persona_handler.py
app/handlers/face_swap_handler.py
app/services/notifications.py
app/services/system_watchdog.py
app/services/self_healing.py
app/services/jarvis_telegram_file_tools.py
app/services/jarvis_operator_task_center.py
app/services/jarvis_n8n_super_agent.py
app/services/conversation_brain.py
app/services/quick_answer.py
app/services/claude_helper.py
app/services/identity_core.py
app/services/figma_brief_generator.py
app/services/figma_queue.py
app/services/figma_client.py
app/services/block_l_common.py
app/services/google_workspace_tools.py
app/services/jarvis_v5_content_factory.py
app/services/block_m2_face_swap/cost_estimator.py
app/response_formatter.py
tools/jarvis_smart_telegram_control.py
scripts/jarvis_task_completion_watcher.py
scripts/jarvis_operator_telegram_bridge.py
```
