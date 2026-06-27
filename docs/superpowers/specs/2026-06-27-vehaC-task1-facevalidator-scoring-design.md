# Веха C / Задача 1 — FaceValidator frame-quality scoring

**Дата:** 2026-06-27
**Статус:** принято (ОК на спеку получен), реализация по TDD
**Стоимость:** $0 — локально, на хосте бота, 0 vision-вызовов

## Контекст

Веха C выбирает лучший кадр (из нарезки Вехи B / кадров Grok) **только**
локальным `FaceValidator` — бесплатно, объективно. Grok тратится отдельно
только на промт движения (Задача 3). Валидатор уже умеет детектить лица
(`has_face`, `count_faces`, `get_largest_face_bbox`), но не оценивает
качество кадра для свапа. Добавляем скоринг.

Заземление в коде: загрузка идёт с `allowed_modules=["detection"]` (SCRFD из
buffalo_l). Детектор уже возвращает `det_score` и `kps` (5 точек) на каждом
лице — отдельный landmark-модуль не нужен, просто перестаём выбрасывать поля
в новом пути детекции. OpenCV Haar fallback `kps`/`det_score` не даёт.

## API

```python
score_largest_face(image_path: Path) -> FaceScore | None
```

Нет лица → `None` (консистентно с `get_largest_face_bbox`). Несколько лиц →
скорим **largest по площади bbox** (целевое лицо свапа).

Реализуется через **новый** внутренний `_detect_scored()`. Существующие
`has_face` / `count_faces` / `get_largest_face_bbox` **не трогаются** —
отдельный путь, нулевой риск регресса Вехи B / свапа.

## FaceScore (frozen dataclass)

```python
@dataclass(frozen=True)
class FaceScore:
    composite: float        # итог 0..1, по нему сравниваются кадры (Задача 2)
    det_score: float        # сырая уверенность детектора 0..1 (0.0 если backend не даёт)
    area_fraction: float    # площадь bbox / площадь кадра, 0..1 (сырое)
    frontality: float       # 0..1, 1.0 = идеально фронтально (0.0 если недоступно)
    bbox: tuple[int, int, int, int]
    backend: str            # "insightface" | "opencv" — видимость деградации
```

## Сигналы (каждый нормируется в 0..1)

1. **det_score** — `face.det_score` от SCRFD, уже ~0..1. `clamp(0, 1)`.
   Сигнал чёткости/не-смаза лица.

2. **area_norm** — масштабо-инвариантно через долю кадра:
   `rel = bbox_area / (img_w * img_h)`;
   `area_norm = min(1.0, rel / TARGET_FACE_FRACTION)`,
   `TARGET_FACE_FRACTION = 0.10` (лицо ≥10% кадра = полный балл).
   `area_fraction` хранит сырое `rel`.

3. **frontality** из 5 kps `[left_eye, right_eye, nose, l_mouth, r_mouth]` —
   симметрия носа между глазами, масштабо-инвариантно:
   - `eye_center_x = (left_eye_x + right_eye_x) / 2`
   - `eye_dist = |right_eye_x - left_eye_x|`
   - `ratio = |nose_x - eye_center_x| / eye_dist`
   - `frontality = max(0.0, 1.0 - ratio / MAX_OFFSET_RATIO)`,
     `MAX_OFFSET_RATIO = 0.5`
   - guard: `eye_dist < 1px` (вырожденный kps) → сигнал недоступен.

## Композит (взвешенная сумма с ренормировкой по доступным сигналам)

```
composite = Σ(w_i * signal_i) / Σ(w_i по доступным сигналам)
```

Веса (module-level константы, тюнинг живьём):
- `W_DET   = 0.40`
- `W_FRONT = 0.35`
- `W_AREA  = 0.25`

- **insightface:** все три сигнала.
- **opencv (деградация):** доступен только area →
  `composite = area_norm`, `det_score = 0.0`, `frontality = 0.0`,
  `backend = "opencv"` (лог-warning). select_best_frame всё равно работает.
- **вырожденные kps:** frontality исключён из суммы и знаменателя (ренорм).

Композит — выпуклая комбинация сигналов из [0,1] → гарантированно в [0,1].

## Edge-cases

| Случай | Поведение |
|---|---|
| нет лица / backend "none" | `None` |
| несколько лиц | скорим largest по area |
| вырожденные kps (`eye_dist < 1`) | frontality исключён из композита (ренорм) |
| файл не найден / не читается | `ValueError` (как в `_detect`) |
| opencv backend | деградация: composite = area_norm |

## Тесты (RED→GREEN, InsightFace мок как в существующих тестах)

- профиль (kps асимметричны) → низкая frontality vs фронт → высокая
- мелкое лицо → низкий area_norm vs крупное → высокий
- смаз (низкий det_score) → ниже композит
- несколько лиц → скорим largest по area
- нет лица → None
- вырожденные kps → frontality исключён (ренорм, проверка формулы)
- opencv backend → composite=area_norm, det/front=0.0, backend="opencv"
- файл не читается / не найден → ValueError
- композит в границах [0,1] на экстремальных входах

## Регресс

После GREEN прогнать весь `face_validator` regress —
`has_face/count_faces/get_largest_face_bbox` не тронуты.

## Out of scope (следующие задачи)

- Задача 2: `select_best_frame` (сравнение FaceScore по composite).
- Задача 3: Grok motion-промт.
