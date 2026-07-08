# Phase E.2 Plan — Voice Polish

_Continuation of the voice-audit work started in `docs/jarvis_voice_audit.md` (commit `529d558`, May 19). This doc cross-references the audit findings with today's commits and ranks what remains for tomorrow._

## Section 1 — Findings already resolved today

| Audit finding | Today's commit | Notes |
|---|---|---|
| ✅ Critical #2 — Mojibake in `app/services/system_watchdog.py:115, 183, 187, 216, 220, 232, 242` (corrupted CP1251→UTF-8 strings in critical alerts) | `ad2299c` | Commit body lists "mojibake (9 strings)"; covers all of the audit's enumerated lines. |
| ✅ Moderate #4 — `app/telegram_bot.py:320` PowerShell `\`n` escape leaked into Python (`f"Событие создано:\`n{...}"`) | `ad2299c` | Single-line fix per commit body. |
| ✅ Moderate #7 — `NameError` in `app/services/jarvis_telegram_file_tools.py:262` (`len(rows)` references undefined name; would crash success path) | `ad2299c` | Commit body cites NameError. |
| ✅ Critical #1 — Raw exception text leaked to user. Audit counted 25+ instances in `smart_telegram_control.py` alone plus more across `telegram_bot.py` and `persona_handler.py` | `b336c80` + `f9bd5c4` + `f225d94` | New `error_translator` service is the chokepoint the audit recommended (Quick Win #1). 41 leak sites refactored across handlers; `DailyLimitExceeded` message preserved verbatim. Per follow-up #36, **~14 leaks remain in `smart_telegram_control.py`** — see Section 2. |

Net effect: 4 of the 10 enumerated pain points from the audit closed; the chokepoint architecture (audit's recommended Layer B) is now in place for tomorrow's cleanup pass.

## Section 2 — Remaining emitters with proposed fixes

Each entry: file:line · current text · audit verdict · proposed rewrite · est. time.

### HIGH priority — users see these often

**E2.1. `tools/jarvis_smart_telegram_control.py` — 14 raw `f"Ошибка: {exc}"` leaks** _(brief #36)_
- **Audit verdict:** Critical pain point #1 residual. Cited lines from audit: 3455, 3592, 3634, 3739, 3772, 3829, 4230, 4246, 4262, 4278, 4294, 4310, 4326, 4377, 4393, 4409, 4425, 4441, 4457, 4473, 4489, 4505, 4564 (subset of these still raw; verify in code).
- **Proposed fix:** Same mechanical refactor as `f9bd5c4` — wrap each leak in `error_translator.humanize(exc, context=...)`. The helper already exists.
- **Est. time:** 25-35 min. The first 41 sites took the bulk of that effort; the patterns repeat now.

**E2.2. `app/handlers/persona_handler.py` — addressing inconsistency** _(audit Critical #3)_
- **Audit verdict:** `:374` `"Сначала сгенерируй фото"` (ты) sits 14 lines from `:388` `"Ответьте \"да\" / \"нет\""` (вы). Audit also flagged `:172, 186` (вы) vs `:455` (ты).
- **Proposed fix:** Normalize to "вы" per the recommended voice profile (audit §"Recommended Voice Profile"). Mechanical rewrite of ~6 short strings.
- **Est. time:** 10 min.

**E2.3. `app/services/conversation_brain.py` — bypass-LLM canned replies leak router-internal phrasing**
- **Audit verdict:** Pain point #6. Lines 229, 238: `"Похоже на задачу. Такой текст лучше передать в supervisor goal-routing."` reads like internal docs to a user.
- **Proposed fix:** Replace with user-facing rewordings, e.g. `"Это похоже на задачу — переключаюсь в режим работы. Жду формулировку, что нужно сделать."` Drop the router-internal "goal-routing" jargon entirely.
- **Est. time:** 5-10 min.

### MEDIUM priority — visible but rarer

**E2.4. `app/services/self_healing.py` — English alerts in a Russian-everywhere stack** _(audit Low #8)_
- **Audit verdict:** Lines 292 (`"Disk low ({free_gb:.1f}GB free) — cleaned {cleaned} log files"`) and 299 (`"High memory: {mem_mb:.0f}MB — restart recommended"`) break the language convention.
- **Proposed fix:** Russian rewrite, e.g. `"Мало места ({free_gb:.1f} ГБ свободно) — почистил {cleaned} логов."`. Note: this is also one of the audit's open questions (#7) — confirm Russian over English before doing it.
- **Est. time:** 10 min once direction confirmed.

**E2.5. `app/handlers/face_swap_handler.py` — two residual exception leaks**
- **Audit verdict:** Low #9. Lines 217, 261: `f"❌ Ошибка swap: {exc}"`.
- **Proposed fix:** Same `error_translator` wrapping as E2.1.
- **Est. time:** 5 min.

**E2.6. `app/services/identity_core.py` — inject personality into `JARVIS_CORE_IDENTITY`** _(audit Quick Win #2, Step 1 in audit's "Wiring order")_
- **Audit verdict:** Highest-leverage single edit per the audit — propagates to `quick_answer`, `conversation_brain` (Ollama + OpenAI), `figma_brief_generator`, `restaurant_mode`, `party_mode`, `landing_content_generator`, `claude_helper`, `claude_api_call`.
- **Proposed fix:** Add the audit's recommended voice traits — formal "вы", dry wit on errors, confident success language, sparing emoji — to the system prompt. Roll out behind a `JARVIS_VOICE_BYPASS=1` env flag (audit open question #10).
- **Est. time:** 20-30 min (prompt drafting is the real cost). **Defer until at least #E2.1-E2.3 are merged** — easier to evaluate voice effect when the obvious leaks are fixed first.

### LOW priority — cosmetic / niche

**E2.7. `tools/jarvis_smart_telegram_control.py:3317` — internal filename leaked**
- **Audit verdict:** `"❌ Night Autonomy не активирована — нет scheduled_tasks.json"`.
- **Proposed fix:** Hide the filename: `"❌ Night Autonomy не активирована — конфигурация задач не найдена."`
- **Est. time:** 2 min.

**E2.8. `tools/jarvis_smart_telegram_control.py:3403` — vague error gives no concrete action**
- **Audit verdict:** Low #10. `"❌ Скрипт запуска backend не найден. Перезапусти вручную."` doesn't say what to run.
- **Proposed fix:** Add the actual command: `"❌ Скрипт запуска backend не найден. Запустите вручную: `.\\start_jarvis.ps1`"`.
- **Est. time:** 2 min.

**E2.9. `app/response_formatter.py:46-55` — hand-coded EN→RU word substitution table** _(audit Moderate #5)_
- **Audit verdict:** Hack workaround for LLM language drift. Voice layer should set language via system prompt instead.
- **Proposed fix:** Strictly downstream of E2.6 (identity_core personality). Once the system prompt enforces Russian, this table becomes dead code that can be deleted with confidence.
- **Est. time:** 15-20 min — but only after E2.6 lands and the table can be retired safely.

**E2.10. Brief #37 — BOM in `app/telegram_bot.py`**
- Not voice-audit work strictly, but lives nearby. Mechanical: strip the `﻿` from line 1.
- **Est. time:** 2 min.

## Section 3 — Suggested execution order for resume

Goal: **30-40 minutes of focused work closes 6-8 emitters** while staying low-risk.

| Order | Item | Time | Why this slot |
|---|---|---|---|
| 1 | E2.1 — 14 leaks in `smart_telegram_control.py` | 25-35 min | Highest user-visible impact. Mechanical refactor leveraging `error_translator` (already proven by `f9bd5c4`). Frees brain #36 from the open-followups list. |
| 2 | E2.5 — face_swap_handler residual leaks | 5 min | Same refactor pattern; tack it on. |
| 3 | E2.7 — hide `scheduled_tasks.json` filename | 2 min | Trivial. Closes one of the audit's named lines. |
| 4 | E2.8 — concrete restart command | 2 min | Trivial. |
| 5 | E2.2 — `persona_handler` ты/вы normalization | 10 min | Visible everywhere persona dialogs run; mechanical rewrite. |
| 6 | E2.10 — BOM strip (brief #37) | 2 min | Free win while in the file area. |
| 7 | E2.3 — `conversation_brain` canned reply rewordings | 5-10 min | Visible in the most-used chat flow. |
| ⏸ | **Stop here for the day** — checkpoint, commit, then choose: |  |  |
| 8 (optional) | E2.4 — `self_healing.py` Russian | 10 min | Defer until audit open question #7 is answered. |
| 9 (later session) | E2.6 — identity_core personality + bypass flag | 20-30 min | Big lever; do alone in a focused slot so the prompt can be tuned. |
| 10 (after #9) | E2.9 — retire `response_formatter` substitution table | 15-20 min | Strictly downstream of #9. |

Estimated cumulative time through item 7: **~55 min**. Tighter ~35-min window: items 1, 5, 7 alone close the highest-impact emitters and leave the rest for a follow-up.

**Risk note.** Items 1, 5, 7 each touch live handler code — run the relevant test suites (`tests/test_smart_telegram_control*`, `tests/test_persona_handler*`, `tests/test_conversation_brain*`) after each change rather than at the end. The `error_translator` regression suite (18 tests in `b336c80`) already covers item 1's underlying behavior; the test passes are confirming the wiring, not the translator.
