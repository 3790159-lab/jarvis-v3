# Vizir `/task` — Output-contract + доставка открываемого артефакта

**Дата:** 2026-07-02 · **Статус:** ПРИНЯТА Daniil (spec→OK→план→TDD)
**Ветка:** `vizir-task-delivery` (worktree от прода `d4fd98c`)
**Движок:** Вариант A — промт-контракт, детерминированная доставка, $0 до живого.
**Критерий:** РАБОЧАЯ ФИЧА — результат приходит в Telegram **открываемым с телефона**, не «код в чате».

---

## 1. Проблема (фактом, живой прогон 2026-07-02)
Prod `/task` Hermes-конфиг `enabled_toolsets=[]` (нет тулов; дизайн «без тулов → артефакт
инлайн»). На «сделай игру» Hermes=Claude Code (Sonnet 4.6, file-агент по натуре)
**галлюцинирует «Готово, файл создан по адресу C:\…\index.html»** вместо кода. Приёмка
`accept_task` честно reject → эскалация (доказано вживую: крестики → `stopped_stalled`,
$0.2086/$0.90, артефакт не записан). Честно — но **фичи нет**. Источник (Hermes) сломан,
не доставка. Файл на десктопе PC бесполезен (Daniil на телефоне/ноуте).

## 2. Цель
Hermes отдаёт **полный результат ИНЛАЙН** → handler извлекает артефакт (снимает code-fence)
→ код игры заворачивается в **чистый `.html`** → Telegram-документ (`text/html`) → Daniil
**открывает с телефона, игра работает**. Текст-ответ («что умеешь») → **сообщением**, не
документом. Приёмка остаётся сетью безопасности (не сработало → честная эскалация, не ложь).

## 3. Ключевые грабли (найдены проверкой доставки фактом)
1. **Доставка УЖЕ подключена:** `run_task_phase`(accepted) пишет `.html` → возвращает
   `document_path` → бот `_task_apply_reply` шлёт `reply.text` + `_send_local_document`.
2. **⚠️ Handler пишет `final_response` СЫРЫМ** — а контракт попросит код **в fence** ```` ```html ````.
   Сырой fence в `.html` → файл начнётся с ```` ``` ```` → **не отрендерится в браузере**.
   → **Снятие fence обязательно.**
3. **⚠️ `_send_local_document` хардкодит MIME `application/zip`** — для надёжной
   открываемости `.html` шлём `text/html` (обратно-совместимо, zip-вызовы целы).
4. **⚠️ Goal-чистота:** преамбула содержит слова «создавать»/«код»/«файл» (= build-verb+
   artifact-noun). Если по ошибке передать преамбулу как приёмочный `goal`, «что умеешь»
   ложно-задетектится build-task → текст-ответ ложно-reject. → приёмочный `goal` ОСТАЁТСЯ
   сырым `base_prompt`; в Hermes уходит `преамбула + base_prompt`.

## 4. Изменение 1 — output-contract преамбула (источник)
В `app/handlers/vizir_task_handler.py` (арк-файл): константа `OUTPUT_CONTRACT` + хелпер
`_compose_hermes_prompt(base_prompt)`. В `run_task_phase`: `loop.run(task, hermes_prompt)`
с `hermes_prompt = _compose_hermes_prompt(base_prompt)`; приёмочный `goal=base_prompt`
(сырой). Loop держит преамбулу immutable через retry (core не трогаем).

Текст (универсальный, код→код / текст→текст): «У тебя НЕТ инструментов file/terminal/web —
ты НЕ можешь создавать файлы на диске. Верни ПОЛНЫЙ результат ИНЛАЙН прямо в ответе. Если
задача просит код/артефакт — верни весь рабочий код одним блоком ```. Если задача просит
текст/ответ — просто ответь инлайн. НИКОГДА не пиши "файл создан по адресу …"/"сохранил
в …" — ты этого не можешь, это будет ложь.»

## 5. Изменение 2 — извлечение артефакта + выбор формата (доставка)
Хелпер `_extract_artifact(final_response) -> (kind, content)`:
- fenced-блок с HTML-маркером (`<!doctype`/`<html`/`<script`/`<body`/`<div`/`<style`) →
  `("html", <тело блока, обрезанное до HTML>)`;
- иначе сырой HTML без fence (legacy инлайн-HTML) → `("html", <срез HTML>)`;
- иначе → `("text", final_response.strip())`.

`_slice_html`: обрезает прозу вокруг — от первого `<!doctype`/`<html` до `</html>` (или конца).

В `run_task_phase`(accepted): `kind, content = _extract_artifact(final)`.
- `kind=="html"` → записать **чистый** `content` в `task_id.html` → `document_path=out`.
- `kind=="text"` → текст-ответ в `reply.text` (сводка + content), `document_path=None`
  (не плодить `.html` для «что умеешь»).

## 6. Изменение 3 — MIME `text/html` (бот-тул, обратно-совместимо)
`tools/jarvis_smart_telegram_control.py`:
- `_send_local_document(chat_id, path, caption="", mime="application/zip")` — использует
  `mime` в multipart. **Дефолт `application/zip` → existing zip-вызовы байт-в-байт целы.**
- `_task_apply_reply` при отправке документа передаёт `mime="text/html"` (`/task` шлёт только
  `.html`-документы).

## 7. Приёмка — страховка (без изменений)
`accept_task` не трогаем. Код инлайн → зуб E принимает → доставка. Hermes упёрся (снова
ссылка) → reject → честная эскалация. Ноль лжи в любом исходе.

## 8. Не сломать
JARVIS-чат (инлайн HTML — `_extract_artifact` берёт сырой HTML → `.html`); «что умеешь»
(текст → сообщение, приёмка accepted); **core** `app/services/vizir/*`/`accept_task` diff
ПУСТ; **existing** команды + `_send_local_document` zip-поведение целы.

## 9. Спай-зубы (TDD, мутацией, $0 моки)
- **Зуб 1 (преамбула в промте):** промт в Hermes содержит контракт; мутация (убрать) → красный.
- **Зуб 2 (fence→чистый .html, КРИТИЧНО):** `final_response` с ```` ```html…``` ```` →
  файл начинается с `<!doctype`/`<html`, **без** ```` ``` ````; мутация (не снимать fence) → красный.
- **Зуб 3 (код→документ):** build-task + инлайн HTML → `document_path` = `.html`, доставка вызвана.
- **Зуб 4 (текст→сообщение + goal-чистота):** «что умеешь» → accepted, текст в `reply.text`,
  `document_path is None`; мутация (goal=hermes_prompt) → «что умеешь» ложно-reject → красный.
- **Зуб 5 (галлюцинация→эскалация):** снова ссылка → `escalated`, `document_path is None`.
- **Зуб 6 (изоляция + MIME обратно-совместим):** `_send_local_document` без `mime` → `application/zip`
  (existing zip цел); `/task`-путь → `text/html`; core diff пуст; existing бот-тул тесты зелёные.

## 10. Критерий готовности (живой тест, отдельным ОК + рестарт)
`/task сделай мне простую веб игру в крестики нолики` → в Telegram приходит **`.html`-документ**
→ Daniil **открывает с телефона** → **игра работает**. НЕ «код в чате». Если Hermes упрётся/
доставка сбоит → честная эскалация (не ложь) + **сразу обсуждаем B** (file-тулсет + sandbox).

## 11. Файлы
- EDIT `app/handlers/vizir_task_handler.py` — преамбула + `_compose_hermes_prompt` +
  `_extract_artifact`/`_slice_html` + accepted-ветка (формат-выбор).
- EDIT `tools/jarvis_smart_telegram_control.py` — `mime`-параметр (обратно-совместимо) +
  `_task_apply_reply` передаёт `text/html`.
- NEW `tests/test_vizir_task_delivery.py` — зубы 1–6.
- НЕ ТРОГАТЬ: core `app/services/vizir/*`, `accept_task`, existing команды/логику бота.
