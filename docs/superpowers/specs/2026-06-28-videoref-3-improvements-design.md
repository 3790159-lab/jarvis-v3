# Videoref — 3 улучшения (реализм / длительность / smooth)

**Дата:** 2026-06-28
**Статус:** принято, реализация по очереди I1 → I2.1 → I2.2 → I3.1 → I3.2 (коммит+СТОП каждая)
**Контекст:** видео-арка работает живьём (Веха D). Три улучшения от Daniil поверх
готового /videoref свап+аним. Реализация по очереди (разный money-вес).

## I1 — Реализм (XS, $0)

КОРРЕКЦИЯ: realism-стек УЖЕ применяется в videoref (build_single_animate_request →
assemble_animate_prompt(add_realism=True, add_negative=True)). Значит УСИЛИВАЕМ,
не добавляем.
- `REALISM_SUFFIX` (prompt_assembly.py) += `lifelike, realistic lighting, true-to-life motion`
  (photorealistic / natural skin texture уже есть — не дублировать).
- `default_negative_prompt()` (app/prompts/video_prompt_builder.py) += `cgi, render,
  stylized, illustration` (anime / cartoon / 3d уже есть).
- Общий стек → улучшит и swapbatch (реализм нужен везде) — ОК.
- БЭКЛОГ (не I1): гипотеза — WaveSpeed `enable_prompt_expansion=True` стилизует
  промт; возможно ОНА причина анимешности. Проверить отдельным экспериментом
  (меняет движение). См. memory [[jarvis-prompt-expansion-hypothesis]].

## I2 — Длительность под референс (M, money-критично)

- Duration реф доступен из Telegram `video.duration` в `_videoref_intercept` (НЕ
  трогаем slice_video_to_frames). Стэшим в _VIDEOREF_PENDING → переносим в
  _VIDEOREF_SWAP_PENDING.
- Предложенная длина = `caps.snap_duration(ref)` (ближайшее 5/10/15, тай→меньшее).
  Fallback нет duration → 5с (дешёвый, money-safe).
- 🔒 MONEY: `_videoref_swapanim_est(seconds, resolution="720p")` — единственный
  источник. Выбранная длина в pending["seconds"]; кнопка-лейбл / check_limit /
  record_cost анимации ВСЕ читают est(seconds). swap=$0.02 от длины не зависит;
  сумма успешных = est(seconds). quoted==charged замкнут для любой длины.
- UX (одно-тап): после анализа — 3 кнопки-длины с ценой, предложенная ⭐
  (`🎬 5с ~$0.52 / ⭐10с ~$1.02 / 15с ~$1.52`). callback `vref:sa:<sec>` → пишет
  seconds в pending + армит face. Воркер → build_single_animate_request(seconds=).
- I2.1: захват duration + est(seconds) параметризация (money-ядро, spy-зубы на
  пересчёт). I2.2: UX-кнопки + selection→pending→воркер (spy: выбранная течёт в
  аним+record).

## I3 — Smooth (RIFE 24→48fps) (S-M, money-safe доплата)

- Переиспуем 1:1: `WaveSpeedRifeClient.interpolate(mp4, num_frames=1)`,
  `rife_surcharge_usd(count, seconds)` = count×max(1,sec)×$0.01/с, money-safe
  паттерн `_interpolate_batch._one` (фейл→оригинал, списать только успех).
- Опт-ин тоггл «🪶 Плавность 48fps» → pending["smooth"] (дефолт OFF).
- est(seconds, smooth) += RIFE если on → кнопка/гейт включают доплату заранее.
- Воркер после успешной анимации: smooth → interpolate в money-safe обёртке;
  успех → record_cost(RIFE)+сглаженное; фейл → оригинал, RIFE не списан.
- I3.1: est(seconds, smooth) + тоггл + флаг (spy: est с RIFE). I3.2: RIFE-вызов в
  воркере money-safe (spy-зубы: фейл→видео отдано, RIFE не списан).

## Переиспуем 1:1
realism-стек, caps.snap_duration/cost_for, swapbatch sbq:-keyboard паттерн,
WaveSpeedRifeClient.interpolate, rife_surcharge_usd, money-safe RIFE обёртка.
Новое — только videoref-склейка (pending-поля, vref:-кнопки, est-параметризация).
