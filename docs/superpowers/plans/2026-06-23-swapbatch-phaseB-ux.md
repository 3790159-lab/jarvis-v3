# /swapbatch Фаза B + UX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать видео реалистичнее (анти-мультяшность), добавить движок Seedance с выбором движка и standalone `/animate`, заменить команды качества на кнопки, защитить промт от лимита символов — не ломая денежные страховки Фазы A.

**Architecture:** Логика промта выносится в чистый тестируемый модуль `prompt_assembly.py`; движок WaveSpeed получает управляемые поля (`enable_prompt_expansion`, `shot_type`) вместо хардкода; A/B-варианты реализма гоняются через env-флаги (без редеплоя между кадрами); Seedance — структурный близнец WaveSpeed-движка поверх Replicate httpx-паттерна (`lucataco_client.py`); выбор движка и качества — inline-кнопки поверх существующих хендлеров.

**Tech Stack:** Python 3.11, httpx (+ MockTransport в тестах), pytest (`@pytest.mark.asyncio`/`anyio`), Telegram Bot API (inline keyboards, callback_query).

**Денежные страховки (на КАЖДОМ этапе с тратами):** concurrency ≤2 (`SWAPBATCH_ANIMATE_CONCURRENCY`), money-safe retry (transient→sweep, terminal→никогда), маленький тест (1 видео) перед массой, **живой тест = ⛔ СТОП, зову пользователя смотреть кадры**, флаг `SWAPBATCH_ANIMATE_ENABLED` — рубильник.

**Ключевые факты кодовой базы (проверено 2026-06-23):**
- Движок: `app/services/block_m2_video/engines/wavespeed_spicy_engine.py` — payload на стр. 73-80; `enable_prompt_expansion: True` ЗАХАРДКОЖЕН (стр. 79); `negative_prompt` шлётся из `request.negative_prompt or ""` (стр. 78); `shot_type` НЕ шлётся.
- Протокол: `engines/engine_protocol.py` — `VideoRequest` (стр. 14-32) уже имеет `negative_prompt: str = ""` и `resolution`.
- Анти-мультяшный negative УЖЕ существует: `app/prompts/video_prompt_builder.py:21` `default_negative_prompt()` (содержит cartoon/anime/3d/doll/plastic skin), но **не используется** в swapbatch-пути.
- Мост (где строится VideoRequest): `tools/jarvis_smart_telegram_control.py:927-973`, функция `_animate_fn` (стр. 949). Сейчас: `_prompt = _motion or _DEFAULT_MOTION` (стр. 943), `negative_prompt` НЕ передаётся, expansion не управляется.
- Хендлеры: `app/handlers/face_swap_handler.py` — `handle_set_prompt` (258), `handle_animate_yes` (231), `handle_set_animate_quality` (268), `run_animate_batch_phase` (579).
- Состояние: `app/services/block_m2_face_swap/batch_orchestrator.py` `BatchSession` — `motion_prompt` (110), `video_engine` (109), `duration_sec`/`resolution`.
- Caps: `engines/capabilities.py` — `WAVESPEED_CAPS`, `SEEDANCE_CAPS` (готов), `caps_for(mode)`, `CAPS_BY_MODE`.
- Router: `engines/router.py` `EngineRouter.select(mode)` — есть `spicy/fast/hq/auto`, НЕТ `seedance`.
- Replicate httpx-паттерн для Seedance: `app/services/block_m_common/lucataco_client.py` (submit→poll, Token-auth, data-URI, 429/terminal split).
- Seedance recon (готов): модель `bytedance/seedance-1-pro-fast`, schema `prompt`(req)/`image`/`duration` 2-12/`resolution` {480,720,1080}/`fps`=24/`seed`; browser UA + data-URI; источник: `docs/superpowers/specs/2026-06-22-two-engine-video-animate-design.md`.
- Тесты: плоско в `C:\jarvis\tests\test_*.py`. Эталоны: `test_wavespeed_spicy_engine.py`, `test_engine_router.py`, `test_quality_settings.py`.
- Запуск тестов: из `C:\jarvis`, `python -m pytest tests/<file>::<test> -v`.

---

## Этап 0 — Проверка (без кода, ~$0.30) — ⛔ ЖИВОЙ ТЕСТ

**Цель:** доказать фактом, что общий промт уже применяется ко всем; снять baseline мультяшности.

### Task 0: Живая верификация set_prompt + baseline

- [ ] **Step 1: Поднять окружение**

Проверить, что флаг включён и бот жив:
```bash
# в окружении бота
echo $SWAPBATCH_ANIMATE_ENABLED   # ожидаем 1; если пусто — export SWAPBATCH_ANIMATE_ENABLED=1 и рестарт бота
```

- [ ] **Step 2: ⛔ СТОП — позвать пользователя.** Дальше живой тест с тратами (~$0.30). Не запускать без пользователя.

- [ ] **Step 3: Прогнать 1 видео со своим промтом (вместе с пользователем)**

В Telegram: `/swapbatch_source` → фото → `/swapbatch_batch` → 1 целевое фото → `/swapbatch_go` →
`/swapbatch_set_prompt тестовая сцена: женщина медленно поворачивает голову` →
`/swapbatch_set_quality duration=5 resolution=720p` → `/swapbatch_animate_yes` → `/swapbatch_animate_go`.

Ожидаем: в карточке `/swapbatch_animate_yes` строка «Промт: тестовая сцена…» (НЕ «дефолтный промт движения»).

- [ ] **Step 4: Зафиксировать факт**

Пользователь подтверждает: (а) применился МОЙ промт, не дефолт; (б) сохранить это видео как **baseline-мультяшность** (текущие настройки: expansion=on, negative="", без realism-добавок) для сравнения на Этапе 1.

- [ ] **Step 5: Развилка**

Если промт НЕ применился → это баг проброса `motion_prompt`; завести отдельную мини-правку перед Этапом 1. Если применился (ожидаемо) → Этап 0 закрыт, кода не требуется.

---

## Этап 1 — Реализм + защита промта

> **СТАТУС 2026-06-23: Stage-1 КОД ГОТОВ на моках (193 теста зелёных, ноль трат).** Реализовано
> Task 1-5 + расширение из диагностики — **wardrobe-режимы контроля одежды** `preserve|safe|spicy`
> (дефолт `safe`), т.к. wan-2.6-spicy дорисовывает бельё при пустом negative. `assemble_animate_prompt`
> теперь принимает `wardrobe=`; добавлены `BatchSession.wardrobe_mode`, `set_wardrobe`,
> `handle_set_wardrobe`, команда `/swapbatch_set_wardrobe`. Бридж шлёт wardrobe(из сессии)+env-флаги.
> Коммиты: bce0085, ecf5793, 15f9978, 7215eb8. Бот перезапущен на этом коде (чистый старт).
> **ОЖИДАЕТ: живой A/B-тест реализма (Task 6, ~$0.75) + проверка wardrobe — пользователь тестит.**

### Task 1: Чистый модуль сборки промта `prompt_assembly.py`

**Files:**
- Create: `app/services/block_m2_video/prompt_assembly.py`
- Test: `tests/test_prompt_assembly.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_prompt_assembly.py
from app.services.block_m2_video.prompt_assembly import (
    assemble_animate_prompt, clamp_prompt, DEFAULT_MOTION, REALISM_SUFFIX,
)
from app.prompts.video_prompt_builder import default_negative_prompt


def test_user_motion_leads_realism_appended():
    prompt, negative = assemble_animate_prompt(
        "женщина поворачивает голову", add_realism=True, add_negative=True,
    )
    # пользовательский промт ведущий, идёт первым, не перетёрт
    assert prompt.startswith("женщина поворачивает голову")
    assert REALISM_SUFFIX in prompt
    assert negative == default_negative_prompt()


def test_empty_motion_falls_back_to_default():
    prompt, negative = assemble_animate_prompt("", add_realism=True, add_negative=True)
    assert prompt == DEFAULT_MOTION
    assert negative == default_negative_prompt()


def test_flags_off_isolate_variants():
    prompt, negative = assemble_animate_prompt("танец", add_realism=False, add_negative=False)
    assert prompt == "танец"
    assert negative == ""


def test_clamp_truncates_on_word_boundary_and_flags():
    text = "word " * 100  # 500 chars
    out, truncated = clamp_prompt(text, cap=50)
    assert truncated is True
    assert len(out) <= 50
    assert not out.endswith(" ")  # обрезка по границе слова, без хвостового пробела


def test_clamp_noop_when_within_cap():
    out, truncated = clamp_prompt("short", cap=50)
    assert out == "short"
    assert truncated is False
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `python -m pytest tests/test_prompt_assembly.py -v`
Expected: FAIL (`ModuleNotFoundError: prompt_assembly`).

- [ ] **Step 3: Реализовать модуль**

```python
# app/services/block_m2_video/prompt_assembly.py
# -*- coding: utf-8 -*-
"""Сборка промта/negative для managed-анимации. Чистые функции — без I/O.

Пользовательский motion-промт ВЕДУЩИЙ: realism-добавки и negative применяются
ПОВЕРХ него, не перетирая. Флаги add_realism/add_negative дают изолированные
A/B-варианты на Этапе 1 (управляются env в мосте).
"""
from __future__ import annotations

from app.prompts.video_prompt_builder import default_negative_prompt

DEFAULT_MOTION = (
    "gentle natural body movement, subtle motion, "
    "soft cinematic lighting, photorealistic"
)
REALISM_SUFFIX = (
    "photorealistic, realistic, cinematic, natural skin texture, detailed skin"
)


def assemble_animate_prompt(
    user_motion: str, *, add_realism: bool, add_negative: bool,
) -> tuple[str, str]:
    """Вернуть (prompt, negative_prompt) для VideoRequest."""
    motion = (user_motion or "").strip()
    if motion:
        prompt = f"{motion}, {REALISM_SUFFIX}" if add_realism else motion
    else:
        prompt = DEFAULT_MOTION  # уже содержит photorealistic
    negative = default_negative_prompt() if add_negative else ""
    return prompt, negative


def clamp_prompt(text: str, cap: int) -> tuple[str, bool]:
    """Обрезать промт до cap символов по границе слова. Вернуть (text, truncated)."""
    text = text or ""
    if len(text) <= cap:
        return text, False
    cut = text[:cap].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return cut, True
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `python -m pytest tests/test_prompt_assembly.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/prompt_assembly.py tests/test_prompt_assembly.py
git commit -m "feat(animate): pure prompt-assembly module (realism over user prompt, clamp)"
```

### Task 2: Поля `enable_prompt_expansion` и `shot_type` в VideoRequest

**Files:**
- Modify: `app/services/block_m2_video/engines/engine_protocol.py:30-32`
- Test: `tests/test_wavespeed_spicy_engine.py` (добавить тест дефолтов)

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_wavespeed_spicy_engine.py`:
```python
def test_video_request_has_expansion_and_shot_type_defaults():
    from pathlib import Path
    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    r = VideoRequest(
        persona_id="p", persona_name="n",
        input_image_path=Path("x.jpg"), prompt="move",
    )
    assert r.enable_prompt_expansion is True   # сохраняем текущее поведение по умолчанию
    assert r.shot_type is None
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_wavespeed_spicy_engine.py::test_video_request_has_expansion_and_shot_type_defaults -v`
Expected: FAIL (`AttributeError: ... 'enable_prompt_expansion'`).

- [ ] **Step 3: Добавить поля**

В `engine_protocol.py`, после строки `negative_prompt: str = ""  # WaveSpeed only; Seedance ignores`:
```python
    # WaveSpeed-only managed controls. Seedance ignores both.
    enable_prompt_expansion: bool = True  # True = текущее поведение; off = анти-аниме тест
    shot_type: str | None = None          # None | "single" | "multi" (WaveSpeed)
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_wavespeed_spicy_engine.py -v`
Expected: PASS (включая прежние тесты).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/engine_protocol.py tests/test_wavespeed_spicy_engine.py
git commit -m "feat(animate): VideoRequest gains enable_prompt_expansion + shot_type"
```

### Task 3: WaveSpeed-движок читает новые поля (вместо хардкода)

**Files:**
- Modify: `app/services/block_m2_video/engines/wavespeed_spicy_engine.py:73-82`
- Test: `tests/test_wavespeed_spicy_engine.py`

- [ ] **Step 1: Написать падающий тест (payload отражает поля)**

Добавить в `tests/test_wavespeed_spicy_engine.py`:
```python
@pytest.mark.asyncio
async def test_payload_reflects_expansion_negative_and_shot_type(tmp_path):
    import json
    seen = {}
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            seen.update(json.loads(req.content))
            return httpx.Response(200, json={"data": {"id": "v", "status": "created", "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/v/result"}}})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["https://cdn/x.mp4"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler, backoff_base=0.0)
    await eng.generate(VideoRequest(
        persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
        prompt="m", seconds=5, negative_prompt="anime, cartoon",
        enable_prompt_expansion=False, shot_type="single",
    ))
    assert seen["enable_prompt_expansion"] is False
    assert seen["negative_prompt"] == "anime, cartoon"
    assert seen["shot_type"] == "single"


@pytest.mark.asyncio
async def test_payload_omits_shot_type_when_none(tmp_path):
    import json
    seen = {}
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            seen.update(json.loads(req.content))
            return httpx.Response(200, json={"data": {"id": "v", "status": "created", "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/v/result"}}})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["https://cdn/x.mp4"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler, backoff_base=0.0)
    await eng.generate(VideoRequest(
        persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
        prompt="m", seconds=5,
    ))
    assert "shot_type" not in seen
    assert seen["enable_prompt_expansion"] is True  # дефолт сохраняет поведение
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_wavespeed_spicy_engine.py::test_payload_reflects_expansion_negative_and_shot_type -v`
Expected: FAIL (`enable_prompt_expansion` всегда True / `shot_type` отсутствует в payload).

- [ ] **Step 3: Заменить хардкод в payload**

В `wavespeed_spicy_engine.py`, блок `payload = {...}` (стр. 73-82) заменить на:
```python
        payload = {
            "image": self._data_uri(request.input_image_path),
            "prompt": request.prompt,
            "duration": seconds,
            "resolution": resolution,
            "negative_prompt": request.negative_prompt or "",
            "enable_prompt_expansion": request.enable_prompt_expansion,
        }
        if request.shot_type:
            payload["shot_type"] = request.shot_type
        if request.seed is not None:
            payload["seed"] = request.seed
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_wavespeed_spicy_engine.py -v`
Expected: PASS (все, включая прежние).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/wavespeed_spicy_engine.py tests/test_wavespeed_spicy_engine.py
git commit -m "feat(wavespeed): drive prompt_expansion/shot_type/negative from request"
```

### Task 4: Защита промта от лимита в `handle_set_prompt`

**Files:**
- Modify: `app/handlers/face_swap_handler.py:258-266`
- Test: `tests/test_swapbatch_handler.py` (добавить кейсы)

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_swapbatch_handler.py` (использует уже существующие фикстуры этого файла; если их нет — создать orchestrator c активным батчем как в соседних тестах файла):
```python
def test_set_prompt_truncates_overlong_and_warns(handler_with_batch):
    handler, chat_id = handler_with_batch
    long_text = "слово " * 600  # ~3600 символов
    reply = handler.handle_set_prompt(chat_id, long_text)
    sess = handler.orchestrator.get(chat_id)
    assert len(sess.motion_prompt) <= 1500
    assert "обрез" in reply.text.lower()  # пользователь предупреждён, не молчим


def test_set_prompt_within_limit_unchanged(handler_with_batch):
    handler, chat_id = handler_with_batch
    reply = handler.handle_set_prompt(chat_id, "короткий промт")
    sess = handler.orchestrator.get(chat_id)
    assert sess.motion_prompt == "короткий промт"
    assert "обрез" not in reply.text.lower()
```
Если в файле нет фикстуры `handler_with_batch`, добавить её, скопировав схему создания батча из существующего теста в этом же файле (поиск `set_motion_prompt`/`get(chat_id)` в `tests/test_swapbatch_handler.py`).

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_swapbatch_handler.py -k set_prompt -v`
Expected: FAIL (длинный промт сохраняется целиком, нет предупреждения).

- [ ] **Step 3: Реализовать клэмп в хендлере**

В `face_swap_handler.py` заменить тело `handle_set_prompt` (стр. 258-266) на:
```python
    def handle_set_prompt(self, chat_id: int, text: str) -> HandlerReply:
        """/swapbatch_set_prompt — store shared motion prompt for the batch.

        WaveSpeed имеет практический лимит длины промта; если текст длиннее
        WAVESPEED_PROMPT_MAX_CHARS, обрезаем по границе слова и явно сообщаем
        пользователю (не молчим). Лимит — env-настраиваемый (калибруется по API).
        """
        if self.orchestrator.get(chat_id) is None:
            return HandlerReply(text="⚠️ Нет активного батча.")
        from app.services.block_m2_video.prompt_assembly import clamp_prompt
        cap = int(os.getenv("WAVESPEED_PROMPT_MAX_CHARS", "1500"))
        clamped, truncated = clamp_prompt(text or "", cap)
        self.orchestrator.set_motion_prompt(chat_id, clamped)
        sess = self.orchestrator.get(chat_id)
        if not sess.motion_prompt:
            return HandlerReply(text="✅ Промт сброшен на дефолтный.")
        warn = ""
        if truncated:
            warn = (f"\n⚠️ Промт был длиннее лимита ({len(text)} > {cap} симв.) "
                    f"и обрезан до {len(clamped)} симв.")
        return HandlerReply(text=f"✅ Промт движения задан:\n«{sess.motion_prompt}»{warn}")
```
(`os` уже импортирован в файле — см. `_envf`.)

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_swapbatch_handler.py -k set_prompt -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/face_swap_handler.py tests/test_swapbatch_handler.py
git commit -m "feat(animate): clamp overlong motion prompt with explicit warning"
```

### Task 5: Мост — env-управляемые варианты реализма + проброс negative

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:940-961`

Мост — часть гигантского модуля, не покрывается юнит-тестом; логика уже вынесена и протестирована в Task 1-3. Здесь только проводка.

- [ ] **Step 1: Заменить сборку промта и VideoRequest**

В `jarvis_smart_telegram_control.py` блок (стр. 940-961) заменить:
```python
                _motion = str(getattr(_sess_q, "motion_prompt", "") or "")
                _DEFAULT_MOTION = ("gentle natural body movement, subtle motion, "
                                   "soft cinematic lighting, photorealistic")
                _prompt = _motion or _DEFAULT_MOTION
                _concurrency = int(_os.getenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2"))

                router = EngineRouter()
                engine = _aio.run(router.select(_engine_mode))  # spicy -> WaveSpeed

                async def _animate_fn(photos, cancel_check):
                    reqs = [
                        VideoRequest(
                            persona_id=f"swapbatch_{chat_id_int}",
                            persona_name="swapbatch",
                            input_image_path=ph,
                            prompt=_prompt,
                            seconds=_seconds,
                            resolution=_resolution,
                            generation_id=new_generation_id(),
                        )
                        for ph in photos
                    ]
```
на:
```python
                _motion = str(getattr(_sess_q, "motion_prompt", "") or "")
                _concurrency = int(_os.getenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2"))

                # Этап 1 анти-аниме: реализм-добавки/negative/expansion/shot_type
                # управляются env, чтобы гонять A/B-варианты БЕЗ редеплоя между кадрами.
                from app.services.block_m2_video.prompt_assembly import assemble_animate_prompt
                def _envbool(name, default):
                    return _os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")
                _add_realism = _envbool("SWAPBATCH_REALISM_SUFFIX", "1")
                _add_negative = _envbool("SWAPBATCH_NEGATIVE", "1")
                _expansion = _envbool("SWAPBATCH_PROMPT_EXPANSION", "1")
                _shot = (_os.getenv("SWAPBATCH_SHOT_TYPE", "").strip() or None)
                _prompt, _negative = assemble_animate_prompt(
                    _motion, add_realism=_add_realism, add_negative=_add_negative,
                )

                router = EngineRouter()
                engine = _aio.run(router.select(_engine_mode))  # spicy->WaveSpeed, seedance->Seedance

                async def _animate_fn(photos, cancel_check):
                    reqs = [
                        VideoRequest(
                            persona_id=f"swapbatch_{chat_id_int}",
                            persona_name="swapbatch",
                            input_image_path=ph,
                            prompt=_prompt,
                            seconds=_seconds,
                            resolution=_resolution,
                            negative_prompt=_negative,
                            enable_prompt_expansion=_expansion,
                            shot_type=_shot,
                            generation_id=new_generation_id(),
                        )
                        for ph in photos
                    ]
```

- [ ] **Step 2: Smoke-импорт (модуль грузится без синтаксических ошибок)**

Run: `python -c "import ast; ast.parse(open(r'tools/jarvis_smart_telegram_control.py', encoding='utf-8').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Полный прогон затронутых тестов**

Run: `python -m pytest tests/test_wavespeed_spicy_engine.py tests/test_prompt_assembly.py tests/test_swapbatch_handler.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(animate): env-driven realism/negative/expansion/shot_type in bridge"
```

### Task 6: ⛔ ЖИВОЙ A/B-тест реализма (~$0.75) — СТОП, пользователь смотрит кадры

**Files:** нет (только env + бот).

- [ ] **Step 1: ⛔ СТОП — позвать пользователя.** Дальше живые траты (~3×$0.25). Не запускать без пользователя.

- [ ] **Step 2: Прогнать варианты на ОДНОМ и том же фото (5с/720p)**

Порядок вариантов (решение пользователя — expansion off + negative первым):
- **V1 (expansion off + negative):** `SWAPBATCH_PROMPT_EXPANSION=0 SWAPBATCH_NEGATIVE=1 SWAPBATCH_REALISM_SUFFIX=0 SWAPBATCH_SHOT_TYPE=` → рестарт бота → 1 видео.
- **V2 (+ realism-добавки):** `SWAPBATCH_PROMPT_EXPANSION=0 SWAPBATCH_NEGATIVE=1 SWAPBATCH_REALISM_SUFFIX=1 SWAPBATCH_SHOT_TYPE=` → рестарт → 1 видео.
- **V3 (+ shot_type single):** как V2 + `SWAPBATCH_SHOT_TYPE=single` → рестарт → 1 видео.

Каждый прогон: то же исходное фото, `/swapbatch_set_quality duration=5 resolution=720p`, тот же `/swapbatch_set_prompt`.

- [ ] **Step 3: Пользователь выбирает глазами**

Сравнить V1/V2/V3 с baseline (Этап 0). Пользователь называет победителя по реализму.

- [ ] **Step 4: Закрепить победителя дефолтом**

Прописать выбранные значения env как дефолт окружения бота (постоянно). Если победил, например, V2 — зафиксировать `SWAPBATCH_PROMPT_EXPANSION=0`, `SWAPBATCH_REALISM_SUFFIX=1`, `SWAPBATCH_NEGATIVE=1`, `SWAPBATCH_SHOT_TYPE=` в env-файле бота. Кода не трогаем (значения дефолтов в коде сохраняют обратную совместимость).

- [ ] **Step 5: Записать вывод в память**

Обновить заметку `jarvis-video-animate-wavespeed-state` фактом: какой вариант реалистичнее, какие env закреплены.

---

## Этап 2 — Seedance + выбор движка

### Task 7: Движок `ReplicateSeedanceEngine`

**Files:**
- Create: `app/services/block_m2_video/engines/replicate_seedance_engine.py`
- Test: `tests/test_replicate_seedance_engine.py`

Структурный близнец WaveSpeed-движка (submit→poll→download, transient/terminal split), но Replicate-API (Token-auth, endpoint модели), как в `lucataco_client.py`. Использует `SEEDANCE_CAPS`. Игнорирует negative/expansion/shot_type.

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_replicate_seedance_engine.py
from pathlib import Path
import json
import httpx
import pytest

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)
from app.services.block_m2_video.engines.replicate_seedance_engine import (
    ReplicateSeedanceEngine, ReplicateSeedanceTransientError, ReplicateSeedanceEngineError,
)


def _img(tmp_path) -> Path:
    p = tmp_path / "src.jpg"; p.write_bytes(b"\xff\xd8\xff\xe0FAKE"); return p


def _engine(handler, dl_handler=None, **kw) -> ReplicateSeedanceEngine:
    return ReplicateSeedanceEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl_handler or (lambda r: httpx.Response(200, content=b"MP4"))),
        **kw,
    )


def test_errors_subclass_shared_bases():
    assert issubclass(ReplicateSeedanceTransientError, TransientVideoError)
    assert issubclass(ReplicateSeedanceEngineError, TerminalVideoError)


@pytest.mark.asyncio
async def test_generate_success_and_cost(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["input"]["prompt"] == "move"        # prompt required
            assert body["input"]["image"].startswith("data:")  # data-URI
            assert body["input"]["duration"] == 5
            assert body["input"]["resolution"] == "1080p"
            return httpx.Response(201, json={"id": "p1", "status": "starting"})
        return httpx.Response(200, json={"status": "succeeded", "output": "https://cdn/x.mp4"})

    eng = _engine(handler, backoff_base=0.0)
    res = await eng.generate(VideoRequest(
        persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
        prompt="move", seconds=5, resolution="1080p", mode="seedance",
    ))
    assert res.output_path.exists()
    assert res.cost_usd == pytest.approx(0.25)   # SEEDANCE_CAPS[("1080p",5)]
    assert res.engine == "replicate_seedance"


@pytest.mark.asyncio
async def test_429_exhausted_is_transient(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "rate"})
    eng = _engine(handler, max_retries=2, backoff_base=0.0)
    with pytest.raises(ReplicateSeedanceTransientError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
            prompt="m", seconds=5, mode="seedance"))


@pytest.mark.asyncio
async def test_4xx_terminal_not_retried(tmp_path):
    posts = {"n": 0}
    def handler(req: httpx.Request) -> httpx.Response:
        posts["n"] += 1
        return httpx.Response(422, json={"detail": "bad"})
    eng = _engine(handler, max_retries=5, backoff_base=0.0)
    with pytest.raises(ReplicateSeedanceEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
            prompt="m", seconds=5, mode="seedance"))
    assert posts["n"] == 1   # MONEY: 4xx (≠429) никогда не ретраится


@pytest.mark.asyncio
async def test_failed_status_terminal(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(201, json={"id": "p", "status": "starting"})
        return httpx.Response(200, json={"status": "failed", "error": "boom"})
    eng = _engine(handler, backoff_base=0.0)
    with pytest.raises(ReplicateSeedanceEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
            prompt="m", seconds=5, mode="seedance"))
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_replicate_seedance_engine.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать движок**

```python
# app/services/block_m2_video/engines/replicate_seedance_engine.py
# -*- coding: utf-8 -*-
"""Seedance (bytedance/seedance-1-pro-fast) image-to-video — Replicate, SFW/censored.

Структурный близнец WaveSpeedSpicyEngine: submit→poll→download, transient/terminal
split (429/network = transient→retry/sweep; completed-failed / 4xx≠429 = terminal,
billable→never retry). Replicate Token-auth + base64 data-URI + browser UA
(анти-tarpit). Cost/snap из SEEDANCE_CAPS. Никогда не логирует токен.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .capabilities import SEEDANCE_CAPS
from .engine_protocol import VideoRequest, VideoResult, new_generation_id
from .errors import TerminalVideoError, TransientVideoError

logger = logging.getLogger(__name__)

_BASE = "https://api.replicate.com/v1"
_MODEL = "bytedance/seedance-1-pro-fast"
_SUBMIT = f"{_BASE}/models/{_MODEL}/predictions"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


class ReplicateSeedanceEngineError(TerminalVideoError):
    """Terminal: prediction failed/canceled, or 4xx (≠429). Billable, never retry."""


class ReplicateSeedanceTransientError(TransientVideoError):
    """429/network exhausted; no prediction succeeded -> safe to retry."""


class ReplicateSeedanceEngine:
    engine_name = "replicate_seedance"

    def __init__(
        self,
        api_token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        max_retries: int = 8,
        backoff_base: float = 1.0,
    ) -> None:
        self._token = (api_token or os.getenv("REPLICATE_API_TOKEN", "")).strip()
        if not self._token:
            raise ReplicateSeedanceEngineError("REPLICATE_API_TOKEN not set")
        self._headers = {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }
        self._transport = transport
        self._dl_transport = download_transport
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def is_available(self) -> bool:
        return bool(self._token)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start = time.monotonic()
        gen_id = request.generation_id or new_generation_id()
        seconds = SEEDANCE_CAPS.snap_duration(request.seconds)
        resolution = SEEDANCE_CAPS.snap_resolution(request.resolution)
        if not request.input_image_path.exists():
            raise ReplicateSeedanceEngineError(f"input image not found: {request.input_image_path}")

        payload = {"input": {
            "image": self._data_uri(request.input_image_path),
            "prompt": request.prompt,
            "duration": seconds,
            "resolution": resolution,
        }}
        if request.seed is not None:
            payload["input"]["seed"] = request.seed

        pred_id = await self._submit_with_retry(payload)
        video_url = await self._poll(pred_id)
        out_path = await self._download(video_url, request.persona_id, gen_id)

        return VideoResult(
            generation_id=gen_id, persona_id=request.persona_id, output_path=out_path,
            engine=self.engine_name, model=_MODEL,
            seed=request.seed if request.seed is not None else -1,
            cost_usd=SEEDANCE_CAPS.cost_for(seconds, resolution),
            duration_sec=time.monotonic() - start, timestamp=datetime.now(timezone.utc),
            prompt=request.prompt, seconds=seconds,
            extra={"video_url": video_url, "resolution": resolution},
        )

    @staticmethod
    def _data_uri(path: Path) -> str:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"

    async def _submit_with_retry(self, payload: dict) -> str:
        last: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                    r = await c.post(_SUBMIT, headers=self._headers, json=payload)
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=r.request, response=r)
                if 400 <= r.status_code < 500:
                    raise ReplicateSeedanceEngineError(f"Replicate rejected ({r.status_code}): {r.text[:300]}")
                r.raise_for_status()
                pid = r.json().get("id")
                if not pid:
                    raise ReplicateSeedanceEngineError(f"no prediction id: {r.json()}")
                return pid
            except ReplicateSeedanceEngineError:
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                code = exc.response.status_code if exc.response is not None else 0
                logger.warning("seedance submit %d/%d failed: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    wait = (10.0 + 2 ** attempt + random.uniform(0, 5)) if code == 429 else 2 ** attempt
                    await asyncio.sleep(wait * self._backoff_base)
            except Exception as exc:  # noqa: BLE001 — network, retryable, not billed
                last = exc
                logger.warning("seedance submit %d/%d error: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise ReplicateSeedanceTransientError(f"seedance submit failed after {self._max_retries}: {last}")

    async def _poll(self, pred_id: str, max_wait: int = 600) -> str:
        poll_url = f"{_BASE}/predictions/{pred_id}"
        deadline = time.monotonic() + max_wait
        interval = 5
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as c:
            while time.monotonic() < deadline:
                try:
                    r = await c.get(poll_url, headers=self._headers)
                    if r.status_code == 429 or r.status_code >= 500:
                        logger.warning("seedance poll %d, re-polling", r.status_code)
                        await asyncio.sleep(interval * self._backoff_base)
                        continue
                    r.raise_for_status()
                    d = r.json()
                except ReplicateSeedanceEngineError:
                    raise
                except Exception as exc:  # noqa: BLE001 — re-poll, no re-bill
                    logger.warning("seedance poll error, re-polling: %s", exc)
                    await asyncio.sleep(interval * self._backoff_base)
                    continue
                status = d.get("status")
                if status == "succeeded":
                    out = d.get("output")
                    if isinstance(out, list):
                        out = out[0] if out else None
                    if not out:
                        raise ReplicateSeedanceEngineError(f"succeeded but no output: {d}")
                    return out
                if status in ("failed", "canceled"):
                    raise ReplicateSeedanceEngineError(f"prediction {status}: {d.get('error')}")
                await asyncio.sleep(interval * self._backoff_base)
        raise ReplicateSeedanceEngineError(f"poll timed out after {max_wait}s")

    async def _download(self, url: str, persona_id: str, gen_id: str) -> Path:
        out_dir = Path("state/personas/videos") / persona_id / gen_id
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "output.mp4"
        last: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=300, transport=self._dl_transport) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                return dest
            except Exception as exc:  # noqa: BLE001
                last = exc
                await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise ReplicateSeedanceEngineError(f"download failed: {last}")
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_replicate_seedance_engine.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/replicate_seedance_engine.py tests/test_replicate_seedance_engine.py
git commit -m "feat(seedance): ReplicateSeedanceEngine (structural twin, money-safe)"
```

### Task 8: Router маршрутит `seedance`

**Files:**
- Modify: `app/services/block_m2_video/engines/router.py:17-55`
- Test: `tests/test_engine_router.py`

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_engine_router.py`:
```python
@pytest.mark.anyio
async def test_mode_seedance_returns_seedance_engine():
    seed = _make_engine("replicate_seedance", available=True)
    router = EngineRouter(seedance=seed)
    chosen = await router.select("seedance")
    assert chosen is seed
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_engine_router.py::test_mode_seedance_returns_seedance_engine -v`
Expected: FAIL (`EngineRouter` не принимает `seedance=`, нет ветки).

- [ ] **Step 3: Добавить движок и ветку**

В `router.py`: в `__init__` добавить параметр `seedance` и поле; добавить ленивый геттер и ветку в `select`:
```python
    def __init__(
        self,
        *,
        replicate: VideoGenerator | None = None,
        runpod: VideoGenerator | None = None,
        wavespeed: VideoGenerator | None = None,
        seedance: VideoGenerator | None = None,
    ) -> None:
        self._replicate = replicate
        self._runpod = runpod
        self._wavespeed = wavespeed
        self._seedance = seedance
```
```python
    def _get_seedance(self) -> VideoGenerator:
        if self._seedance is None:
            from .replicate_seedance_engine import ReplicateSeedanceEngine
            self._seedance = ReplicateSeedanceEngine()
        return self._seedance
```
В `select`, перед веткой `if mode == "spicy":`:
```python
        if mode == "seedance":
            logger.info("EngineRouter: mode=seedance -> ReplicateSeedanceEngine")
            return self._get_seedance()
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_engine_router.py -v`
Expected: PASS (все).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/router.py tests/test_engine_router.py
git commit -m "feat(router): route mode=seedance to ReplicateSeedanceEngine"
```

### Task 9: Inline-меню выбора движка после свапа

**Files:**
- Modify: `app/handlers/face_swap_handler.py` (новый метод-строитель клавиатуры + установка движка)
- Modify: `tools/jarvis_smart_telegram_control.py` (callback-роутинг `sbeng:`)
- Test: `tests/test_swapbatch_handler.py`

Решение пользователя: Seedance показывать ВСЕГДА, но **с пометкой** «censored, для SFW/одетых». Без эвристик NSFW.

- [ ] **Step 1: Написать падающий тест (установка движка + клавиатура)**

Добавить в `tests/test_swapbatch_handler.py`:
```python
def test_engine_keyboard_lists_both_engines_with_seedance_label(handler_with_batch):
    handler, chat_id = handler_with_batch
    kb = handler.build_engine_keyboard()
    flat = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    labels = " ".join(b["text"] for row in kb["inline_keyboard"] for b in row)
    assert "sbeng:spicy" in flat
    assert "sbeng:seedance" in flat
    assert "sbeng:none" in flat
    assert "censored" in labels.lower() or "sfw" in labels.lower()  # пометка


def test_set_engine_updates_session_and_snaps_quality(handler_with_batch):
    handler, chat_id = handler_with_batch
    reply = handler.handle_set_engine(chat_id, "seedance")
    sess = handler.orchestrator.get(chat_id)
    assert sess.video_engine == "seedance"
    # 15с недоступно у Seedance → дефолтная длительность снапается в допустимую
    assert sess.duration_sec in (5, 10)
    assert sess.resolution in ("480p", "720p", "1080p")
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_swapbatch_handler.py -k "engine" -v`
Expected: FAIL (`build_engine_keyboard`/`handle_set_engine` не существуют).

- [ ] **Step 3: Реализовать в `face_swap_handler.py`**

Добавить методы в класс хендлера (рядом с `handle_set_animate_quality`):
```python
    @staticmethod
    def build_engine_keyboard() -> dict:
        """Inline-меню выбора движка после свапа. Seedance с пометкой censored."""
        from app.services.block_m2_video.engines.capabilities import (
            WAVESPEED_CAPS, SEEDANCE_CAPS,
        )
        return {"inline_keyboard": [
            [{"text": f"🎬 {WAVESPEED_CAPS.display_name}", "callback_data": "sbeng:spicy"}],
            [{"text": f"🎬 {SEEDANCE_CAPS.display_name} · censored (SFW)",
              "callback_data": "sbeng:seedance"}],
            [{"text": "🚫 Без анимации", "callback_data": "sbeng:none"}],
        ]}

    def handle_set_engine(self, chat_id: int, engine_mode: str) -> HandlerReply:
        """Установить движок батча и снапнуть качество в его caps."""
        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="⚠️ Нет активного батча.")
        from app.services.block_m2_video.engines.capabilities import caps_for
        caps = caps_for(engine_mode)
        snapped_dur = caps.snap_duration(sess.duration_sec)
        snapped_res = caps.snap_resolution(sess.resolution)
        self.orchestrator.set_video_engine(chat_id, engine_mode)
        self.orchestrator.set_animate_quality(
            chat_id, duration=snapped_dur, resolution=snapped_res,
        )
        sess = self.orchestrator.get(chat_id)
        note = "" if not caps.censored else "\n⚠️ Censored: подходит для SFW/одетых сцен."
        return HandlerReply(text=(
            f"✅ Движок: {caps.display_name}. Качество: {sess.duration_sec}с, "
            f"{sess.resolution}, fps {caps.native_fps}.{note}\n"
            f"Дальше: /swapbatch_animate_yes → /swapbatch_animate_go."
        ))
```

- [ ] **Step 4: Добавить сеттер в orchestrator (если отсутствует)**

Проверить наличие `set_video_engine` в `batch_orchestrator.py`:
Run: `python -c "from app.services.block_m2_face_swap.batch_orchestrator import BatchOrchestrator as B; print(hasattr(B,'set_video_engine'))"`
Если `False` — добавить рядом с `set_animate_quality` (стр. ~396):
```python
    def set_video_engine(self, chat_id: int, engine_mode: str) -> None:
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                return
            sess.video_engine = engine_mode
            self._touch(sess)
```

- [ ] **Step 5: Запустить — проходит**

Run: `python -m pytest tests/test_swapbatch_handler.py -k "engine" -v`
Expected: PASS.

- [ ] **Step 6: Подключить callback-роутинг `sbeng:` в боте**

В `tools/jarvis_smart_telegram_control.py`, в `handle_callback_query` (стр. 2625+), рядом с другими prefix-ветками добавить:
```python
    if data.startswith("sbeng:"):
        choice = data.split(":", 1)[1]
        _hq, _ = _swapbatch_get_handler()
        if choice == "none":
            answer_callback_query(cq_id, "Без анимации")
            _swapbatch_dispatch(chat_id, "animate_no")
            return
        reply = _hq.handle_set_engine(int(chat_id), choice)
        answer_callback_query(cq_id, "Движок выбран")
        _swapbatch_apply_reply(chat_id, reply)
        return
```
И показать меню там, где сейчас swap завершается (после `/swapbatch_go` → состояние SWAP_DONE): отправить `_hq.build_engine_keyboard()` вместе с текстом «Выбери движок анимации». (Найти место отправки финального reply свапа в этом же модуле и добавить `reply_markup=...`.)

- [ ] **Step 7: Smoke-импорт + тесты**

Run: `python -c "import ast; ast.parse(open(r'tools/jarvis_smart_telegram_control.py', encoding='utf-8').read()); print('ok')"`
Run: `python -m pytest tests/test_swapbatch_handler.py tests/test_swapbatch_orchestrator.py -v`
Expected: `ok` + PASS.

- [ ] **Step 8: Commit**

```bash
git add app/handlers/face_swap_handler.py app/services/block_m2_face_swap/batch_orchestrator.py tools/jarvis_smart_telegram_control.py tests/test_swapbatch_handler.py
git commit -m "feat(animate): engine-choice inline menu (WaveSpeed/Seedance censored/none)"
```

### Task 10: ⛔ ЖИВОЙ тест цензуры Seedance (~$0.05) — СТОП, пользователь смотрит

**Files:** нет.

- [ ] **Step 1: ⛔ СТОП — позвать пользователя.** Дешёвый, но живой тест.

- [ ] **Step 2: Прогнать Seedance на коже/откровенном кадре (480p/5с — самый дешёвый, ~$0.05)**

Через меню: свап → кнопка Seedance → `/swapbatch_set_quality duration=5 resolution=480p` → animate_yes → animate_go.

- [ ] **Step 3: Зафиксировать факт цензуры**

Пользователь смотрит: реально чёрные кадры или просто хуже/мягче? Подтвердить, что пометка «censored, для SFW» соответствует факту. Если Seedance НЕ чернит (просто SFW-стиль) — уточнить формулировку пометки.

- [ ] **Step 4: Обновить память**

Записать в `jarvis-video-animate-wavespeed-state`: факт поведения Seedance на коже (чёрные кадры / деградация) + финальная формулировка пометки.

### Task 11: Standalone `/animate`

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (команда `/animate` + приём одного фото → меню движка → генерация одного видео)
- Modify: `app/handlers/face_swap_handler.py` при необходимости (переиспользовать существующий путь)
- Test: `tests/test_swapbatch_handler.py` (юнит на сборку запроса одиночного видео, без сети)

Реализовать как тонкую одиночную обёртку поверх уже существующего движкового пути: одно фото → то же меню `sbeng:` → один `VideoRequest` через `animate_batch(engine, [req], concurrency=1)`. Без нового движкового кода.

- [ ] **Step 1: Написать падающий тест (сборка одиночного запроса)**

Если в хендлере появляется чистый строитель — тестировать его; иначе тест на то, что `/animate`-поток ставит сессию в состояние ожидания одного фото. Минимально:
```python
def test_animate_single_request_uses_session_engine_and_quality(handler_with_batch):
    handler, chat_id = handler_with_batch
    handler.handle_set_engine(chat_id, "seedance")
    # строитель одиночного запроса (добавляется в Step 3)
    req = handler.build_single_animate_request(chat_id, image_path="x.jpg",
                                               motion="смотрит в камеру")
    assert req.mode == "seedance"
    assert req.prompt.startswith("смотрит в камеру")
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_swapbatch_handler.py -k "single_request" -v`
Expected: FAIL (`build_single_animate_request` не существует).

- [ ] **Step 3: Реализовать строитель в хендлере**

```python
    def build_single_animate_request(self, chat_id: int, *, image_path, motion: str):
        """Собрать один VideoRequest для standalone /animate из настроек сессии."""
        from pathlib import Path
        from app.services.block_m2_video.engines.engine_protocol import (
            VideoRequest, new_generation_id,
        )
        from app.services.block_m2_video.prompt_assembly import assemble_animate_prompt
        sess = self.orchestrator.get(chat_id)
        engine_mode = sess.video_engine if sess else "spicy"
        seconds = sess.duration_sec if sess else 5
        resolution = sess.resolution if sess else "720p"
        prompt, negative = assemble_animate_prompt(
            (sess.motion_prompt if sess else "") or motion,
            add_realism=True, add_negative=True,
        )
        return VideoRequest(
            persona_id=f"animate_{chat_id}", persona_name="animate",
            input_image_path=Path(image_path), prompt=prompt, seconds=seconds,
            resolution=resolution, negative_prompt=negative, mode=engine_mode,
            generation_id=new_generation_id(),
        )
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_swapbatch_handler.py -k "single_request" -v`
Expected: PASS.

- [ ] **Step 5: Подключить команду `/animate` в боте**

В диспетчере команд `tools/jarvis_smart_telegram_control.py` (рядом с регистрацией `/swapbatch_*`, стр. ~4800): добавить `/animate` → перевести чат в режим ожидания одного фото; на фото — показать `build_engine_keyboard()`; по выбору движка → `build_single_animate_request` → `animate_batch(engine, [req], concurrency=1)` → отправить видео. Переиспользовать `EngineRouter().select(mode)` и `_progress`. Денежные страховки те же (terminal не ретраится — внутри движка).

- [ ] **Step 6: Smoke-импорт + тесты**

Run: `python -c "import ast; ast.parse(open(r'tools/jarvis_smart_telegram_control.py', encoding='utf-8').read()); print('ok')"`
Run: `python -m pytest tests/test_swapbatch_handler.py -v`
Expected: `ok` + PASS.

- [ ] **Step 7: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py app/handlers/face_swap_handler.py tests/test_swapbatch_handler.py
git commit -m "feat(animate): standalone /animate single-photo flow (engine menu reused)"
```

### Task 12: ⛔ ЖИВОЙ тест двух движков + /animate (~$0.30) — СТОП

- [ ] **Step 1: ⛔ СТОП — позвать пользователя.**
- [ ] **Step 2:** Прогнать через меню: WaveSpeed (5с/720p) и Seedance (5с/1080p); затем `/animate` с одним фото на каждом движке. Малый тест (по 1 видео) перед любым 100-прогоном.
- [ ] **Step 3:** Пользователь подтверждает: оба движка работают через меню, биллинг считает оба, `/animate` отдаёт одиночное видео.

---

## Этап 3 — UX-кнопки длины/качества

### Task 13: Inline-кнопки длины и качества

**Files:**
- Modify: `app/handlers/face_swap_handler.py` (строитель клавиатуры качества)
- Modify: `tools/jarvis_smart_telegram_control.py` (callback `sbq:`)
- Test: `tests/test_swapbatch_handler.py`

Кнопки caps-aware: показывают только допустимые для текущего движка значения; тап → строка `duration=.. resolution=..` → существующий `handle_set_animate_quality`.

- [ ] **Step 1: Написать падающий тест**

```python
def test_quality_keyboard_respects_engine_caps(handler_with_batch):
    handler, chat_id = handler_with_batch
    handler.handle_set_engine(chat_id, "seedance")  # нет 15с
    kb = handler.build_quality_keyboard(chat_id)
    durations = [b["callback_data"] for row in kb["inline_keyboard"] for b in row
                 if b["callback_data"].startswith("sbq:dur:")]
    assert "sbq:dur:15" not in durations          # Seedance не предлагает 15с
    assert "sbq:dur:5" in durations and "sbq:dur:10" in durations


def test_quality_callback_applies_via_existing_handler(handler_with_batch):
    handler, chat_id = handler_with_batch
    reply = handler.handle_quality_button(chat_id, "dur", "10")
    sess = handler.orchestrator.get(chat_id)
    assert sess.duration_sec == 10
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_swapbatch_handler.py -k "quality_keyboard or quality_callback" -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать в хендлере**

```python
    def build_quality_keyboard(self, chat_id: int) -> dict:
        """Caps-aware кнопки длины и разрешения для текущего движка."""
        from app.services.block_m2_video.engines.capabilities import caps_for
        sess = self.orchestrator.get(chat_id)
        caps = caps_for(sess.video_engine if sess else "spicy")
        dur_row = [{"text": f"{d}с", "callback_data": f"sbq:dur:{d}"}
                   for d in caps.allowed_durations]
        res_row = [{"text": r, "callback_data": f"sbq:res:{r}"}
                   for r in caps.allowed_resolutions]
        return {"inline_keyboard": [
            dur_row, res_row,
            [{"text": "✅ Готово", "callback_data": "sbq:done"}],
        ]}

    def handle_quality_button(self, chat_id: int, kind: str, value: str) -> HandlerReply:
        """Тап кнопки → собрать аргумент → существующий handle_set_animate_quality."""
        arg = f"duration={value}" if kind == "dur" else f"resolution={value}"
        return self.handle_set_animate_quality(chat_id, arg)
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_swapbatch_handler.py -k "quality_keyboard or quality_callback" -v`
Expected: PASS.

- [ ] **Step 5: Callback-роутинг `sbq:` в боте**

В `handle_callback_query`:
```python
    if data.startswith("sbq:"):
        parts = data.split(":")
        _hq, _ = _swapbatch_get_handler()
        if parts[1] == "done":
            answer_callback_query(cq_id, "Готово")
            _swapbatch_apply_reply(chat_id, _hq.handle_animate_yes(int(chat_id)))
            return
        reply = _hq.handle_quality_button(int(chat_id), parts[1], parts[2])
        answer_callback_query(cq_id, f"{parts[1]}={parts[2]}")
        _swapbatch_apply_reply(chat_id, reply)
        return
```
И показать `build_quality_keyboard(chat_id)` после выбора движка (в `sbeng:` ветке — после `handle_set_engine`, прикрепить клавиатуру качества к reply).

- [ ] **Step 6: Smoke-импорт + полный прогон**

Run: `python -c "import ast; ast.parse(open(r'tools/jarvis_smart_telegram_control.py', encoding='utf-8').read()); print('ok')"`
Run: `python -m pytest tests/test_swapbatch_handler.py -v`
Expected: `ok` + PASS.

- [ ] **Step 7: Commit**

```bash
git add app/handlers/face_swap_handler.py tools/jarvis_smart_telegram_control.py tests/test_swapbatch_handler.py
git commit -m "feat(ux): caps-aware inline buttons for duration/resolution"
```

### Task 14: ⛔ ЖИВОЙ UX-smoke (~$0.25) — СТОП

- [ ] **Step 1: ⛔ СТОП — позвать пользователя.**
- [ ] **Step 2:** Полный путь только тапами: свап → кнопка движка → кнопки длины/качества → «Готово» → стоимость → go. 1 видео.
- [ ] **Step 3:** Пользователь подтверждает: команды качества больше не нужны, кнопки уважают caps движка, fallback-команды по-прежнему работают.

---

## Этап 4 — Плавность: ffmpeg-интерполяция кадров (ЗАПЛАНИРОВАНО, после «доступа для друга»)

**Контекст (решено 2026-06-23):** fps на managed-движках НЕ управляется (WaveSpeed/Seedance шлют
только image/prompt/duration/resolution; модели выдают фикс. частоту 30/24). Бесплатный рычаг —
медленный промт — ПРОВЕРЕН и ПОМОГ (закреплён: `DEFAULT_MOTION` плавный + подсказка в cost-gate,
commit 5fa1bbf). Но промт НЕ заменяет интерполяцию: она добавляет РЕАЛЬНЫЕ кадры между
сгенерёнными. Поэтому Этап 4 = обязателен, но ПОСЛЕ фичи «доступ для друга».

**Цель:** постобработка скачанного MP4 → удвоение кадровки (Seedance 24→48, WaveSpeed 30→60) для
видимой плавности, без трат на API.

**Подход (решено): ffmpeg `minterpolate`** (motion-compensated) — бесплатно, локально, без API.
- ⚠️ ffmpeg на машине НЕ установлен (проверено 2026-06-23: нет в PATH, нет bundled, нет imageio_ffmpeg).
  Шаг 0 = разовая установка ffmpeg (~5 мин, free).
- Альтернативы (НЕ берём по умолчанию): Replicate-RIFE модель (~$0.01-0.05/ролик, +API-шаг);
  локальный RIFE torch/GPU (тяжёлая установка). Брать только если ffmpeg-качество разочарует.
- Артефакты `minterpolate` на резком/перекрывающемся движении — ожидаемы; для плавного движения ок.

**Проводка (эскиз, детали — при планировании фичи):**
- Общий util `smooth_video(in_path, target_fps) -> out_path` (subprocess ffmpeg `-vf minterpolate=fps=N`).
- Хук после `_download` в каждом движке ИЛИ в bridge/`animate_batch` (один раз, движко-независимо).
- Флаг-каркас `quality_settings.fps_interpolation_enabled` УЖЕ существует (сейчас OFF) — переиспользовать.
- Per-engine целевой fps из `EngineCapabilities.native_fps × 2`.
- TDD на моках (мокнуть subprocess; проверить команду/частоту/замену файла), без реального ffmpeg в CI.

**Оценка:** ~полдня кода (util + хук + флаг + тесты) + разовая установка ffmpeg.

---

## Финальная проверка

- [ ] **Полный прогон тестов:**

Run: `python -m pytest tests/test_prompt_assembly.py tests/test_wavespeed_spicy_engine.py tests/test_replicate_seedance_engine.py tests/test_engine_router.py tests/test_swapbatch_handler.py tests/test_swapbatch_orchestrator.py tests/test_video_capabilities.py tests/test_quality_settings.py -v`
Expected: all PASS.

- [ ] **Ротация ключа:** проверить, что утёкший WaveSpeed-ключ ротирован (заметка `jarvis-video-animate-wavespeed-state`).

- [ ] **Память:** обновить `jarvis-video-animate-wavespeed-state` финальным состоянием Фазы B (движки live, выбранный realism-вариант, поведение Seedance, кнопки).

---

## Соответствие спеке (self-review)

| Требование спеки | Задачи |
|---|---|
| Этап 0: проверка set_prompt + baseline | Task 0 |
| Этап 1: анти-аниме (expansion/negative/realism/shot_type) | Task 1-3, 5, 6 |
| Этап 1: кастомный промт ведущий, добавки поверх | Task 1 (`test_user_motion_leads_realism_appended`) |
| Этап 1: лимит символов, не молчать | Task 4 |
| Этап 1: несколько живых вариантов, выбор глазами | Task 6 (V1/V2/V3) |
| Этап 2: SeedanceEngine | Task 7 |
| Этап 2: роутер seedance | Task 8 |
| Этап 2: меню выбора движка, Seedance с пометкой | Task 9 |
| Этап 2: проверка цензуры фактом ($0.05) | Task 10 |
| Этап 2: standalone /animate | Task 11 |
| Этап 3: кнопки длины/качества caps-aware | Task 13 |
| Страховки на каждом этапе с тратами | STOP-гейты Task 0, 6, 10, 12, 14 |
