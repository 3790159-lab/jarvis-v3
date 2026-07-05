# Dev-задача: починить импорт supervisor (IntakeRequest из app.models)

**Дата:** 2026-07-06
**Источник:** menu-audit нашёл `app.services.supervisor` в «битых импортах (наш код)».
**Класс:** тот же семейный сорт, что `/browse_check`-os и `time_brain.get_zone` —
модуль не поднимается, но не по причине отсутствия опц. зависимости.
**Статус:** нарезано как dev-задача (по решению Артёма). Пока в KNOWN_BROKEN_IMPORTS
baseline аудитора (зуб зелён), строку убрать ПОСЛЕ фикса → зуб снова сторожит.

## Симптом
```
app.services.supervisor:10
    from app.models import IntakeRequest, IntakeResponse, TaskRecord, TaskStatus
→ ImportError: cannot import name 'IntakeRequest' from 'app.models'
  (C:\jarvis\app\models\__init__.py)
```

## Установленный корень (подтвердить в задаче)
Коллизия «пакет vs модуль»: в дереве одновременно есть
- `app/models/__init__.py` — **пустой пакет**, и
- `app/models.py` — модуль, где реально определён `class IntakeRequest` (и, судя
  по grep, `IntakeResponse/TaskRecord/TaskStatus`).

Пакет `app/models/` **затеняет** модуль `app/models.py` при `import app.models`,
поэтому `from app.models import IntakeRequest` резолвится в пустой `__init__` →
ImportError. (Классы также встречаются в `app/models/schemas.py`,
`app/control_plane/models.py`, `app/schemas/execution.py` — проверить, какой
источник канонический.)

## Кто зависит (почему это важно)
`app.services.supervisor` импортируется бекендом: `app/api/routes.py`,
`app/api/missions.py`, `app/api/responses.py`, `app/routers/supervisor_*_router.py`.
Т.е. это может ронять supervisor-эндпоинты бэкенда (:8010), не только аудит.

## Варианты фикса (выбрать в задаче, TDD)
1. **Ре-экспорт из пакета** (наименьшее касание): в `app/models/__init__.py`
   добавить `from app.models.schemas import IntakeRequest, IntakeResponse,
   TaskRecord, TaskStatus` (или из того файла, где они канонические). Плюс: все
   существующие `from app.models import X` начинают работать. Риск: если
   `app/models.py` и `app/models/schemas.py` расходятся — определить единый
   источник правды.
2. **Точечный импорт в supervisor:** заменить строку 10 на импорт из реального
   модуля (`from app.models.schemas import ...` или `from app.control_plane.models
   import ...`). Плюс: локально. Минус: не лечит других потребителей
   `from app.models import ...`, если такие есть.
3. **Устранить коллизию:** решить, `app/models.py` или `app/models/` — legacy, и
   убрать/слить дубль. Самое чистое, но самое рискованное (широкое касание).

Рекомендация: начать с диагностики (какие имена где определены, кто что импортит),
затем Вариант 1, если `schemas.py` — канон.

## Acceptance (TDD)
- [ ] RED-зуб: `import app.services.supervisor` (реальный, без моков) — сейчас
      ImportError; после фикса импортится, `IntakeRequest` и др. доступны.
- [ ] Зуб на канонический источник: `from app.models import IntakeRequest` даёт
      тот же класс, что и из реального модуля-определения (нет двух разных
      IntakeRequest).
- [ ] Регресс полной сюиты NEW=0.
- [ ] Убрать `"app.services.supervisor"` из
      `app/services/audit/menu_audit.py::KNOWN_BROKEN_IMPORTS` → menu-audit зуб
      зелёный уже БЕЗ исключения (доказывает, что реально починено).
- [ ] `python scripts/audit_menu.py`: `import_broken=0`.

## Дисциплина
Изолированный worktree (как остальные /dev_task), TDD, СТОП перед мерджем,
регресс-гейт, прод изолирован.
