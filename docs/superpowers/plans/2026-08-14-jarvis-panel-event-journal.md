# Журнал событий панели Джарвиса (мерж 2) — план реализации

> **Для агентных исполнителей:** ОБЯЗАТЕЛЬНЫЙ САБ-СКИЛЛ — `superpowers:subagent-driven-development`
> (рекомендуется) или `superpowers:executing-plans`. Шаги размечены чекбоксами (`- [ ]`).

**Цель:** панель отвечает на вопрос «что случилось, пока меня не было»: показывает
переходы фермы за 72 часа, сгруппированные по суткам, с разделителем на месте
последнего рестарта бэкенда — и никогда не выдаёт молчание сломанного писателя
за тишину здоровой фермы.

**Архитектура:** `ops_watchdog` уже вычисляет нужный закрытый список переходов
внутри `evaluate()`, но отдаёт их текстами. Вниз выделяется чистая
`transitions()`, `evaluate()` становится тонкой обёрткой над ней (её сигнатура и
поведение не меняются — она под мутационным гейтом). Переходы дозаписываются в
`state/panel_events.jsonl`, раз в цикл трогается маркер живости
`state/panel_events.heartbeat`. Панель только читает.

**Реализует:** `docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md`,
§2–§4 и §9 (мерж 2). Мерж 1 (лента) — отдельный план, выполняется первым.

**Стек:** Python 3.14. `scripts/ops_watchdog.py` — **standalone и stdlib-only**,
это жёсткое ограничение, не стиль.

---

## Что важно знать до начала

**⚠️ Главное ограничение.** `scripts/ops_watchdog.py` обязан уметь сообщить о
смерти бэкенда именно тогда, когда мёртв бэкенд и всё, что делит с ним
питон-окружение. Писатель журнала **не имеет права импортировать** ничего из
`app/`, `chatter/` или сторонних пакетов. Нарушение проявится не красным
тестом, а тишиной сторожа в худший момент.

**Мерж 1 (лента) должен быть смержен до старта этого плана** — оба меняют
`jarvis_farm.py`, и последовательный порядок дешевле разбора конфликтов.

**Прогон тестов:**
```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/... -q --no-header -p no:cacheprovider
```

**Ничего не мержить без отмашки владельца.**

---

## Структура файлов

| Файл | Ответственность | Действие |
|---|---|---|
| `scripts/ops_watchdog.py` | независимый сторож; теперь ещё и писатель журнала | **изменяется**: +`transitions()`, +`journal_trim()`, +`journal_append()`, +`touch_beat()`, правка `main()` |
| `app/services/jarvis_farm.py` | сбор состояния фермы для панели | **изменяется**: +чтение журнала, +схлопывание `suppressed` |
| `app/routers/jarvis_panel.py` | разметка панели | **изменяется**: +блок «Что изменилось» |
| `tests/test_panel_event_journal.py` | сторожа писателя (stdlib-сторона) | **создаётся** |
| `tests/chatter/test_panel_journal_view.py` | сторожа вида (панель) | **создаётся** |
| `scripts/mutate_ops_watchdog.py` | гейт DEV-26 для сторожа | **изменяется**: +6 мутаций |
| `scripts/mutate_panels_hierarchy.py` | гейт DEV-26 для панелей | **изменяется**: +6 мутаций |

Сторожа вида — отдельный файл, а не дописка в `test_panels_hierarchy.py`
(спека §7 допускала и то, и другое): тот файл уже 800 строк и держит иерархию и
типографику обеих панелей. Журнал — своя ответственность, и смешивать их значит
получить файл, который никто не держит в голове целиком.

---

### Task 1: `transitions()` под `evaluate()`

**Файлы:**
- Изменить: `scripts/ops_watchdog.py:147-207`
- Тест: `tests/test_panel_event_journal.py` (создать)

`evaluate()` отдаёт **тексты**, журналу нужна **структура**. Сигнатуру менять
нельзя — она покрыта тестами и мутационным гейтом. Значит логика уезжает вниз, а
`evaluate()` становится обёрткой.

- [ ] **Шаг 1: написать падающий тест**

Создать `tests/test_panel_event_journal.py`:

```python
# -*- coding: utf-8 -*-
"""Журнал событий панели: писатель (сторона ops_watchdog).

Спека: docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md, §2-§3.

⚠️ Тесты здесь НЕ имеют права тянуть app/ или chatter/: ops_watchdog standalone
и stdlib-only by design — он обязан работать, когда мёртво окружение бэкенда.
Импорт тяжёлого пакета в тесте не сломает прод, но скроет нарушение границы в
самом сторожевом коде.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ops_watchdog_under_test", ROOT / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_spec)
sys.modules["ops_watchdog_under_test"] = ow
_spec.loader.exec_module(ow)


def _probe(ok, reason="r", detail="d"):
    return {"ok": ok, "detail": detail, "reason": reason}


def test_a_down_transition_carries_structure_not_text():
    """Журналу нужны check/kind/reason/ts, а не готовая фраза с эмодзи:
    по тексту нельзя ни отсортировать, ни сгруппировать, ни схлопнуть."""
    st = {"backend": {"fail": 1, "alerted": False}}
    trs, _new = ow.transitions(st, {"backend": _probe(False, "no_response", "нет ответа")},
                               debounce=2)
    assert len(trs) == 1, trs
    t = trs[0]
    assert t["check"] == "backend"
    assert t["kind"] == "down"
    assert t["reason"] == "no_response"
    assert t["detail"] == "нет ответа"
    assert isinstance(t["ts"], float)


def test_evaluate_still_returns_the_same_alert_texts():
    """`evaluate()` под мутационным гейтом: её поведение обязано остаться
    байт-в-байт тем же, иначе рефакторинг заплатит алертами владельцу."""
    st = {"backend": {"fail": 1, "alerted": False}}
    alerts, _new = ow.evaluate(st, {"backend": _probe(False, "no_response", "нет ответа")},
                               debounce=2)
    assert alerts == ["🚨 DOWN: BACKEND (:8010 /health). нет ответа"]


def test_a_suppressed_fall_is_a_transition_but_not_an_alert():
    """Загрузочное окно: считаем, но молчим. В журнал это ОБЯЗАНО попасть —
    событие, о котором владельцу не сообщили, и есть то, ради чего журнал
    заводится (DEV-18: подавление не молчит)."""
    st = {"backend": {"fail": 1, "alerted": False}}
    probes = {"backend": _probe(False, "no_response")}
    trs, _new = ow.transitions(st, probes, debounce=2, suppress_down=True)
    assert [t["kind"] for t in trs] == ["suppressed"]

    alerts, _new2 = ow.evaluate(st, probes, debounce=2, suppress_down=True)
    assert alerts == [], "о подавленном падении отправлен алерт"


def test_recovered_and_changed_are_distinct_kinds():
    """Три разных перехода — три разных вида записи. Слить `changed` с `down`
    значит вернуть дефект 2026-08-11: вторая причина не звучала вовсе."""
    alerted = {"web": {"fail": 3, "alerted": True, "alerted_reason": "old"}}
    trs, _ = ow.transitions(alerted, {"web": _probe(True)}, debounce=2)
    assert [t["kind"] for t in trs] == ["recovered"]

    trs2, _ = ow.transitions(alerted, {"web": _probe(False, "new")}, debounce=2)
    assert [t["kind"] for t in trs2] == ["changed"]
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/test_panel_event_journal.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: module 'ops_watchdog_under_test' has no attribute 'transitions'`.

- [ ] **Шаг 3: выделить `transitions()`, `evaluate()` сделать обёрткой**

В `scripts/ops_watchdog.py` заменить тело `evaluate()` (строки 147-207) на:

```python
def transitions(prev_state: dict, probes: dict, debounce: int = DEBOUNCE,
                suppress_down: bool = False, now=None):
    """Чистое ядро: свернуть пробы этого цикла в состояние и вернуть ПЕРЕХОДЫ.

    Отделено от `evaluate()` 14.08, когда переходы понадобились журналу панели
    структурой, а не текстом: по фразе с эмодзи нельзя ни отсортировать, ни
    сгруппировать, ни схлопнуть пару «подавлено → подтверждено». Второго
    анализатора состояний в системе при этом не появилось — `evaluate()` стала
    тонкой обёрткой, и вся логика дебаунса и дедупа живёт здесь.

    Возвращает `(transitions: list[dict], new_state: dict)`. Переход:
    `{"ts", "check", "kind", "reason", "detail"}`, kind ∈
    {down, recovered, changed, suppressed}. Проверки, отсутствующие в
    `probes`, сохраняют прежнее состояние дословно (заморожены, никогда не
    «восстанавливаются» сами).

    ДЕДУП ПО ПРИЧИНЕ, а не по факту (дефект найден фактом 2026-08-11): чек
    worktree простоял красным 1669 циклов из-за законной правки тумблера, и
    приехавший следом недеплоенный код второго алерта уже НЕ дал бы. Причина —
    грубый стабильный ключ от пробы, а НЕ `detail`: в тексте живут гигабайты и
    секунды, дедуп по нему давал бы алерт раз в 30 секунд.
    """
    now = time.time() if now is None else now
    new_state = {k: dict(v) for k, v in prev_state.items()}
    out = []

    def fire(check, kind, res, reason):
        out.append({"ts": now, "check": check, "kind": kind,
                    "reason": reason, "detail": res.get("detail", "")})

    for check, res in probes.items():
        st = dict(new_state.get(check, {"fail": 0, "alerted": False}))
        reason = str(res.get("reason") or check)
        if res.get("ok"):
            if st.get("alerted"):
                fire(check, "recovered", res, reason)
            # Причина забывается вместе с алертом: оставить её значило бы
            # промолчать о следующем падении по той же причине.
            st = {"fail": 0, "alerted": False}
        else:
            st["fail"] = st.get("fail", 0) + 1
            if suppress_down:
                # Окно загрузки: считаем, но молчим. Ни `alerted`, ни причину не
                # ставим — поэтому (а) после окна не поднявшийся сервис немедленно
                # даст 🚨 (debounce уже набран), (б) поднявшийся не даст ✅ о том,
                # о чём владельцу не сообщали, и (в) причина, о которой не
                # сказали, не считается объявленной.
                #
                # В ЖУРНАЛ это попадает: падение, о котором не сообщили, — ровно
                # то событие, ради которого журнал и заводится.
                if st["fail"] >= debounce:
                    fire(check, "suppressed", res, reason)
            elif not st.get("alerted"):
                if st["fail"] >= debounce:
                    fire(check, "down", res, reason)
                    st["alerted"] = True
                    st["alerted_reason"] = reason
            elif "alerted_reason" not in st:
                # Стейт с диска старого формата. Причину принимаем МОЛЧА: иначе
                # первый же цикл после выкатки разошлёт 🚨 по каждой красной
                # проверке — шторм ровно за то, что мы здесь чиним.
                st["alerted_reason"] = reason
            elif st["alerted_reason"] != reason:
                fire(check, "changed", res, reason)
                st["alerted_reason"] = reason
        new_state[check] = st
    return out, new_state


# Виды переходов, о которых владельцу СООБЩАЮТ. `suppressed` сюда не входит по
# определению: это падение в загрузочном окне, о котором мы намеренно молчим.
ALERTING_KINDS = ("down", "recovered", "changed")


def evaluate(prev_state: dict, probes: dict, debounce: int = DEBOUNCE,
             suppress_down: bool = False):
    """Тексты алертов из переходов. Обёртка над `transitions()`; сигнатура и
    поведение неизменны — на них стоят тесты и мутационный гейт."""
    trs, new_state = transitions(prev_state, probes, debounce, suppress_down)
    alerts = [build_alert(t["check"], t["kind"], t["detail"])
              for t in trs if t["kind"] in ALERTING_KINDS]
    return alerts, new_state
```

- [ ] **Шаг 4: убедиться, что новые тесты зелёные И старые не сломались**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/test_panel_event_journal.py tests/test_ops_watchdog.py tests/test_ops_watchdog_chatter.py tests/test_ops_watchdog_secrets.py tests/test_ops_watchdog_selfheal.py tests/test_ops_watchdog_tree.py -q --no-header -p no:cacheprovider
```

Ожидаемо: все зелёные, включая 4 новых.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add scripts/ops_watchdog.py tests/test_panel_event_journal.py
git commit -m "refactor(watchdog): transitions() под evaluate(), переходы структурой"
```

---

### Task 2: Обрезка журнала — «30 суток ИЛИ 5000 записей», обе с маркером

**Файлы:**
- Изменить: `scripts/ops_watchdog.py`
- Тест: `tests/test_panel_event_journal.py`

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/test_panel_event_journal.py`:

```python
DAY = 86400.0


def _rec(ts, check="backend", kind="down"):
    return {"ts": ts, "check": check, "kind": kind, "reason": "r", "detail": "d"}


def test_records_older_than_thirty_days_are_dropped():
    now = 1_000_000.0
    recs = [_rec(now - 40 * DAY), _rec(now - 31 * DAY), _rec(now - 1 * DAY)]
    kept, dropped, why = ow.journal_trim(recs, now)
    assert len(kept) == 1 and dropped == 2
    assert "сут" in why, why


def test_the_record_ceiling_catches_a_restart_storm():
    """Чистые 30 суток безопасны при обычном темпе, но шторм рестартов набьёт
    тысячи строк за сутки — предохранитель по объёму на этот случай."""
    now = 1_000_000.0
    recs = [_rec(now - 60.0) for _ in range(5400)]
    kept, dropped, why = ow.journal_trim(recs, now)
    assert len(kept) == ow.JOURNAL_MAX_RECORDS
    assert dropped == 400
    assert "записей" in why, why
    assert kept[-1]["ts"] == recs[-1]["ts"], "обрезали новые вместо старых"


def test_trimming_nothing_reports_nothing():
    """Парный сторож: маркер, который пишется на каждой дозаписи, — это шум,
    а не сигнал. Ротация без потерь обязана молчать."""
    now = 1_000_000.0
    kept, dropped, why = ow.journal_trim([_rec(now - 60.0)], now)
    assert dropped == 0 and why == ""
    assert len(kept) == 1
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/test_panel_event_journal.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: ... has no attribute 'journal_trim'`.

- [ ] **Шаг 3: реализация**

В `scripts/ops_watchdog.py` добавить `import os` к импортам вверху файла (нужен
`os.replace` в Task 3) и вставить после `ALERTING_KINDS`:

```python
# ── Журнал переходов для панели Джарвиса (спека 2026-08-14, §2) ────────────
#
# Панель отвечает «что сейчас» и не умеет ответить «что случилось, пока меня не
# было»: падение, поднятое гардианом за минуту, для неё неотличимо от того, что
# не случалось. Переходы уже вычислены выше — журнал лишь перестаёт их забывать.
JOURNAL_PATH = ROOT / "state" / "panel_events.jsonl"
JOURNAL_BEAT = ROOT / "state" / "panel_events.heartbeat"
JOURNAL_MAX_AGE_S = 30 * 86400          # 30 суток
JOURNAL_MAX_RECORDS = 5000              # предохранитель на шторм рестартов


def journal_trim(records: list, now: float,
                 max_age_s: float = JOURNAL_MAX_AGE_S,
                 max_records: int = JOURNAL_MAX_RECORDS):
    """(оставшиеся, сколько отброшено, чем именно резали).

    Два предохранителя, «что раньше»: возраст — естественная единица для
    вопроса «что было с прошлого раза», объём — защита от шторма рестартов,
    который набьёт тысячи строк за сутки.

    Пустое `why` означает «ничего не отброшено», и маркер тогда НЕ пишется:
    маркер на каждой дозаписи — шум, а не сигнал.
    """
    kept = [r for r in records if (now - float(r.get("ts") or 0.0)) <= max_age_s]
    by_age = len(records) - len(kept)
    by_count = max(0, len(kept) - max_records)
    if by_count:
        kept = kept[by_count:]           # режем СТАРЫЕ, хвост новых сохраняем
    dropped = by_age + by_count
    if not dropped:
        return kept, 0, ""
    parts = []
    if by_age:
        parts.append("%d старше %d сут" % (by_age, int(max_age_s // 86400)))
    if by_count:
        parts.append("%d сверх потолка в %d записей" % (by_count, max_records))
    return kept, dropped, " и ".join(parts)
```

> ⚠️ **КОД ВЫШЕ НЕВЕРЕН В ДВУХ МЕСТАХ — не воспроизводить.** Оба дефекта
> воспроизведены фактом, и оба НЕВИДИМЫ трём плановым сторожам Task 2:
> они зелёные на этой реализации.
>
> 1. **Потолок режет СВЕЖИЕ, а не старые.** `kept[by_count:]` отрезает первые
>    *по файлу*, что равно «самым старым» только на отсортированном входе.
>    Журнал дозаписывается конкурентно, гарантии сортировки нет ниоткуда.
>    На входе `[свежая 0, свежая 1, свежая 2, старая 0, старая 1]` с потолком 3
>    плановая функция оставила `[свежая 2, старая 0, старая 1]` — выбросила две
>    свежие и сохранила обе древние. Резать надо по ВРЕМЕНИ.
> 2. **`float(r.get("ts") or 0.0)` роняет цикл сторожа.** `ts` строкой
>    (`"вчера"`) даёт `ValueError`, огромное целое — `OverflowError`;
>    `main()` их не ловит, а обёртка `ops_watchdog_detached.ps1` пишет heartbeat
>    ДО питона и глушит traceback в `catch`. Итог: петля жива, heartbeat свеж,
>    все наблюдатели видят здоровый сторож, алертов нет НИКОГДА. Худшая форма
>    DEV-18 — не проглоченное исключение, а молчаливая слепота.
>    Отдельно: `NaN` проходит `<= max_age_s` как False и уезжает с маркером
>    «старше 30 сут» — маркер ВРЁТ ровно там, ради чего §2.4 написана.
>
> Фактическая реализация — коммит `8a9feb88` и правки по ревью: время читает
> отдельный щит (конечное число или `None`), потолок сортирует по `(ts, позиция)`,
> а нечитаемое время названо ТРЕТЬЕЙ причиной потери (см. врезку к §2.4 спеки).

- [ ] **Шаг 4: убедиться, что тесты зелёные**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/test_panel_event_journal.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `7 passed`.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add scripts/ops_watchdog.py tests/test_panel_event_journal.py
git commit -m "feat(watchdog): обрезка журнала по возрасту и объёму, оба с причиной"
```

---

### Task 3: Дозапись, маркер ротации и маркер живости

> ⚠️ **Найдено ревью Task 2 — маркеры вытесняют события на потолке.**
> Маркер ротации дописывается В ТОТ ЖЕ файл и занимает слот потолка. Маркеры —
> самые свежие записи, а потолок режет самые старые, поэтому они не вымываются,
> а копятся по одному за дозапись. Воспроизведено: журнал, стоящий на потолке,
> на каждой дозаписи теряет ДВЕ настоящие записи ради одной новой, и за восемь
> циклов в файле оказалось 8 маркеров.
>
> Итог: ровно в шторме рестартов — сценарии, ради которого потолок и заведён, —
> журнал деградирует к «журнал обрезан ×N» вместо событий, а §7 спеки при этом
> требует, чтобы маркер не становился шумом каждой дозаписи.
>
> Обязательно к решению в этой задаче: считать потолок по НЕ-`rotated` записям
> либо схлопывать подряд идущие маркеры в один (обновлять последний вместо
> дозаписи нового). Плюс парный сторож: «на потолке журнал не превращается в
> маркеры».

**Файлы:**
- Изменить: `scripts/ops_watchdog.py`
- Тест: `tests/test_panel_event_journal.py`

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/test_panel_event_journal.py`:

```python
import json


def _read(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_append_writes_one_line_per_transition(tmp_path):
    p = tmp_path / "j.jsonl"
    ok = ow.journal_append([_rec(1.0), _rec(2.0)], path=p, now=3.0)
    assert ok is True
    assert len(_read(p)) == 2


def test_rotation_leaves_a_marker_in_the_journal(tmp_path):
    """Молчаливой обрезки не бывает: мы чиним ровно этот класс дефекта —
    окно, из которого события уезжали без следа."""
    p = tmp_path / "j.jsonl"
    now = 1_000_000.0
    ow.journal_append([_rec(now - 40 * DAY)], path=p, now=now)
    ow.journal_append([_rec(now)], path=p, now=now)
    recs = _read(p)
    marks = [r for r in recs if r["kind"] == "rotated"]
    assert len(marks) == 1, recs
    assert "старше 30 сут" in marks[0]["detail"], marks[0]


def test_the_liveness_marker_is_touched_after_a_successful_write(tmp_path):
    """Свежий маркер + пустой журнал = настоящая тишина. Без маркера эти два
    случая на экране неразличимы."""
    beat = tmp_path / "beat"
    assert ow.touch_beat(path=beat) is True
    assert beat.exists()


def test_a_failed_write_does_not_refresh_the_marker(tmp_path):
    """Провал записи обязан показывать себя протухающим маркером, а не тонуть
    в тишине. Touch делается ТОЛЬКО после успеха."""
    unwritable = tmp_path / "нет" / "такого" / "каталога" / "j.jsonl"
    # Родителя намеренно не создаём и запрещаем создание.
    ok = ow.journal_append([_rec(1.0)], path=unwritable, now=2.0, mkdir=False)
    assert ok is False
    assert not (tmp_path / "beat").exists()


def test_a_corrupt_journal_line_does_not_stop_the_writer(tmp_path):
    """Панель читает файл, который дописывает другой процесс. Битая строка не
    имеет права остановить ни писателя, ни ротацию."""
    p = tmp_path / "j.jsonl"
    p.write_text('{"ts": 1.0, "kind": "down"}\nне-json\n', encoding="utf-8")
    assert ow.journal_append([_rec(2.0)], path=p, now=3.0) is True
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/test_panel_event_journal.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: ... has no attribute 'journal_append'`.

- [ ] **Шаг 3: реализация**

В `scripts/ops_watchdog.py` под `journal_trim`:

```python
def journal_read(path) -> list:
    """Записи журнала. Битые строки пропускаются молча: панель читает файл,
    который в этот момент дописывает этот процесс, и последняя строка может
    быть без `\\n`. Она приедет целиком через секунду."""
    out = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def journal_append(records: list, *, path=None, now=None, mkdir: bool = True,
                   max_age_s: float = JOURNAL_MAX_AGE_S,
                   max_records: int = JOURNAL_MAX_RECORDS) -> bool:
    """Дозаписать переходы и при необходимости обрезать журнал.

    True — записали (или писать было нечего); False — не смогли. Возврат важен:
    маркер живости обновляется ТОЛЬКО при True, иначе провал записи утонул бы
    в тишине (§2.5 спеки).

    Обрезка идёт только в момент дозаписи, а дозапись — редкое событие (единицы
    в сутки), поэтому она ничего не стоит в обычном цикле.
    """
    now = time.time() if now is None else now
    if not records:
        return True
    p = Path(path or JOURNAL_PATH)
    try:
        if mkdir:
            p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        print("[ops_watchdog] журнал не записан: %s" % exc, file=sys.stderr)
        return False

    try:
        kept, dropped, why = journal_trim(journal_read(p), now, max_age_s, max_records)
        if dropped:
            kept.append({"ts": now, "check": "_journal", "kind": "rotated",
                         "reason": "trim", "detail": "отброшено " + why})
            tmp = p.with_name(p.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for rec in kept:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(tmp, p)           # атомарно в пределах тома
    except OSError as exc:
        print("[ops_watchdog] журнал не обрезан: %s" % exc, file=sys.stderr)
        return False
    return True


def touch_beat(path=None) -> bool:
    """Маркер живости писателя. Раз в цикл, В КОНЦЕ и только при успехе.

    Пустой журнал двусмыслен: «переходов не было» и «писатель молчит» выглядят
    одинаково. Свежий маркер + пустой журнал = настоящая тишина; протухший
    маркер = писатель мёртв или не пишет.

    Существующий `state/ops_watchdog_heartbeat.txt` для этого не годится: его
    пишет PowerShell-обёртка, то есть он доказывает живость ОБЁРТКИ, а не то,
    что питоновский цикл дошёл до конца.
    """
    p = Path(path or JOURNAL_BEAT)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(int(time.time())), encoding="ascii")
        return True
    except OSError as exc:
        print("[ops_watchdog] маркер живости не обновлён: %s" % exc, file=sys.stderr)
        return False
```

- [ ] **Шаг 4: убедиться, что тесты зелёные**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/test_panel_event_journal.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `12 passed`.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add scripts/ops_watchdog.py tests/test_panel_event_journal.py
git commit -m "feat(watchdog): дозапись журнала, маркер ротации и маркер живости"
```

---

### Task 4: Подключить писателя к циклу, не потеряв ребут

**Файлы:**
- Изменить: `scripts/ops_watchdog.py` (функция `main`)
- Тест: `tests/test_panel_event_journal.py`

Ребут алертится **вне** `evaluate()`. Журнал, написанный только из неё, потеряет
ровно то событие, ради которого заводился DEV-24.

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/test_panel_event_journal.py`:

```python
def test_a_reboot_becomes_a_journal_record():
    """DEV-24 живёт ВНЕ evaluate(): ребут не зависит от здоровья сервисов —
    наоборот, они уже подняты гардианами, и именно поэтому раньше он проходил
    незаметно. Журнал без него потерял бы главное событие суток."""
    rec = ow.reboot_record(boot_time=1_000_000.0, now=1_000_060.0)
    assert rec["kind"] == "reboot"
    assert rec["check"] == ow.BOOT_KEY
    assert rec["ts"] == 1_000_060.0
    assert "перезагрузилась" in rec["detail"]
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/test_panel_event_journal.py::test_a_reboot_becomes_a_journal_record -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: ... has no attribute 'reboot_record'`.

- [ ] **Шаг 3: реализация**

В `scripts/ops_watchdog.py` под `touch_beat`:

```python
def reboot_record(boot_time: float, now: float) -> dict:
    """Ребут как запись журнала. Отдельной функцией, потому что приходит он
    ВНЕ `transitions()` — и журнал, собранный только из неё, потерял бы ровно
    то событие, ради которого заводился DEV-24."""
    return {"ts": now, "check": BOOT_KEY, "kind": "reboot", "reason": "boot_id",
            "detail": reboot_alert_text(boot_time, now)}
```

Затем заменить хвост `main()` (от `alerts, state = evaluate(...)` до `return 0`) на:

```python
    trs, state = transitions(state, probes, suppress_down=in_boot_grace)
    alerts = [build_alert(t["check"], t["kind"], t["detail"])
              for t in trs if t["kind"] in ALERTING_KINDS]

    journal = list(trs)
    if reboot_text and boot_time is not None:
        journal.insert(0, reboot_record(boot_time, time.time()))

    if reboot_text:
        _send_tg(reboot_text)
    for text in alerts:
        _send_tg(text)
    _write_state(state)

    # Маркер живости — В КОНЦЕ и только при успехе: провал записи обязан
    # показывать себя протухающим маркером, а не тонуть в тишине (§2.5).
    if journal_append(journal):
        touch_beat()
    else:
        _send_tg("🚨 Журнал панели не пишется — списку событий верить нельзя")
    return 0
```

- [ ] **Шаг 4: убедиться, что всё зелёное**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/test_panel_event_journal.py tests/test_ops_watchdog.py tests/test_ops_watchdog_chatter.py tests/test_ops_watchdog_secrets.py tests/test_ops_watchdog_selfheal.py tests/test_ops_watchdog_tree.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `13 passed` в новом файле, остальные без изменений.

- [ ] **Шаг 5: живой смоук — ОДИН цикл руками, до того как звать человека**

```
cd /c/jarvis && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/ops_watchdog.py && ls -la state/panel_events.heartbeat && cat state/panel_events.heartbeat
```

Ожидаемо: маркер создан, время свежее. Журнала может не быть — если переходов
не случилось, это правильно.

- [ ] **Шаг 6: коммит**

```bash
cd /c/jarvis && git add scripts/ops_watchdog.py tests/test_panel_event_journal.py
git commit -m "feat(watchdog): цикл пишет журнал панели, ребут не теряется"
```

---

### Task 5: Панель читает журнал и различает тишину от молчания

> ⚠️ **Найдено ревью Task 2 — код ниже роняет ПЕРВЫЙ ЭКРАН на той же записи.**
> В шаге реализации стоит `float(rec.get("ts") or 0.0)` — тот самый разбор
> времени, который в Task 2 пришлось заменить щитом: `ts` строкой даёт
> `ValueError`, огромное целое — `OverflowError`, `NaN`/`±inf` проходят молча и
> ломают сортировку. Здесь цена другая и выше: у писателя падение прячется за
> живым heartbeat, а у панели ляжет вся страница фермы.
>
> Читать время тем же способом, что и писатель (конечное число или `None`), и
> завести сторожа. То же относится к `after_s` в Task 6.
>
> ⚠️ **Второе, найдено исполнением Task 3: `splitlines()` режет журнал по
> U+2028/U+2029/U+0085.** `json.dumps(ensure_ascii=False)` пишет эти символы
> СЫРЫМИ, а `splitlines()` считает их концом строки — одна запись с таким
> символом в `detail` читается как две битых, и событие теряется. Писатель уже
> переведён на `split("\n")`; читатель панели обязан читать так же. В плановом
> коде Task 5 стоит `splitlines()` — не воспроизводить.
>
> Туда же: BOM в начале файла и обрывок последней строки без перевода —
> писатель их лечит (`strip` с U+FEFF и шов перед дозаписью), читатель
> обязан быть не слабее.

**Файлы:**
- Изменить: `app/services/jarvis_farm.py`
- Тест: `tests/chatter/test_panel_journal_view.py` (создать)

- [ ] **Шаг 1: написать падающий тест**

Создать `tests/chatter/test_panel_journal_view.py`:

```python
"""Журнал событий на панели: чтение, инвариант живости, схлопывание, вид.

Спека: docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md, §4.
"""
from __future__ import annotations

import json
import time

from app.services import jarvis_farm as F

DAY = 86400.0


def _journal(tmp_path, records, *, beat_age: float | None = 5.0):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    j = state / "panel_events.jsonl"
    j.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                 encoding="utf-8")
    beat = state / "panel_events.heartbeat"
    if beat_age is not None:
        beat.write_text("x", encoding="ascii")
        import os
        stamp = time.time() - beat_age
        os.utime(beat, (stamp, stamp))
    return j


def _rec(ts, check="chatter_runner", kind="down", detail="процесс не найден"):
    return {"ts": ts, "check": check, "kind": kind, "reason": "r", "detail": detail}


def test_a_fresh_marker_with_an_empty_journal_means_real_silence(tmp_path, monkeypatch):
    """Утверждение, а не пустота: переходов действительно не было."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, [], beat_age=5.0)
    records, note = F.journal()
    assert records == []
    assert note == "", note


def test_a_stale_marker_means_the_writer_is_silent_not_the_farm(tmp_path, monkeypatch):
    """Молчание сломанного писателя не имеет права читаться как тишина
    здоровой фермы — ровно тот класс, что «ещё не прочитаны» у медленного
    кэша."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, [_rec(time.time() - 60)], beat_age=10_000.0)
    records, note = F.journal()
    assert note, "панель молчит о молчащем писателе"
    assert "не пишет" in note or "молчит" in note, note


def test_a_missing_marker_is_treated_as_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, [], beat_age=None)
    _records, note = F.journal()
    assert note, "отсутствующий маркер принят за свежий"


def test_a_corrupt_last_line_does_not_break_the_read(tmp_path, monkeypatch):
    """Гонка чтения и дозаписи: последняя строка может быть без `\\n`."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    j = _journal(tmp_path, [_rec(time.time() - 60)], beat_age=5.0)
    with open(j, "a", encoding="utf-8") as f:
        f.write('{"ts": 1.0, "kind": "do')
    records, note = F.journal()
    assert len(records) == 1
    assert note == ""


def test_records_older_than_the_window_are_not_shown(tmp_path, monkeypatch):
    """72 часа — это окно ЭКРАНА, а не хранения: в файле записи живут 30 суток."""
    monkeypatch.setattr(F, "ROOT", tmp_path)
    now = time.time()
    _journal(tmp_path, [_rec(now - 5 * DAY), _rec(now - 1 * DAY)], beat_age=5.0)
    records, _note = F.journal(now=now)
    assert len(records) == 1
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_journal_view.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: module 'app.services.jarvis_farm' has no attribute 'journal'`.

- [ ] **Шаг 3: реализация**

В `app/services/jarvis_farm.py` добавить `import json` вверху и вставить перед
`snapshot_fast()`:

```python
# ─────────────────── Журнал переходов (заход 2, §4) ─────────────────────────
#
# Пишет `scripts/ops_watchdog.py`, панель ТОЛЬКО читает. Формат и пути
# продублированы там и здесь: сторож stdlib-only и standalone, импортировать из
# `app/` он не имеет права, а импортировать `scripts/` в бэкенд — значит тащить
# в него сторожевой модуль целиком. Разъедутся — журнал станет пустым молча,
# поэтому путь к источнику записан рядом. Правку делать в ОБОИХ местах.
JOURNAL_PATH_NAME = "panel_events.jsonl"       # scripts/ops_watchdog.py:JOURNAL_PATH
JOURNAL_BEAT_NAME = "panel_events.heartbeat"   # scripts/ops_watchdog.py:JOURNAL_BEAT
# Порог свежести маркера. Цикл сторожа 30 с, но одна итерация может занять до
# минуты (HTTP-пробы по 8 с, git по 10 с). 180 — тот же порог, что у гардианов
# (HeartbeatMaxAgeSec=180): разные пороги превращают двух сторожей в спорящих.
JOURNAL_BEAT_FRESH = 180.0
JOURNAL_WINDOW_S = 72 * 3600                   # сколько показываем на экране


def journal(*, now: float | None = None, window_s: float = JOURNAL_WINDOW_S):
    """(записи окна, пояснение). Пустое пояснение = списку можно верить.

    ИНВАРИАНТ: пустой список означает «переходов не было» ТОЛЬКО если писатель
    жив. Различитель — маркер `panel_events.heartbeat`, а не догадка: молча
    показать ноль значит соврать ровно в том случае, ради которого панель
    существует.

    Состояние `ops_watchdog` в `guardians()` остаётся отдельным сигналом: он
    говорит «процесс сторожа жив», маркер — «цикл дошёл до конца и записал».
    Живой процесс с протухшим маркером это не одно и то же.
    """
    now = _now() if now is None else now
    beat_age = None
    try:
        beat_age = now - (ROOT / "state" / JOURNAL_BEAT_NAME).stat().st_mtime
    except OSError:
        pass
    if beat_age is None or beat_age > JOURNAL_BEAT_FRESH:
        when = "маркера нет" if beat_age is None else f"{int(beat_age // 60)} мин назад"
        return [], (f"Писатель журнала молчит: ops_watchdog не пишет ({when}). "
                    f"Списку событий верить нельзя.")

    out = []
    try:
        text = (ROOT / "state" / JOURNAL_PATH_NAME).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return [], ""            # маркер свежий, файла нет — переходов не было
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue             # хвост дозаписи: приедет целиком через секунду
        if isinstance(rec, dict) and (now - float(rec.get("ts") or 0.0)) <= window_s:
            out.append(rec)
    out.sort(key=lambda r: float(r.get("ts") or 0.0))
    return out, ""
```

- [ ] **Шаг 4: убедиться, что тесты зелёные**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_journal_view.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `5 passed`.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add app/services/jarvis_farm.py tests/chatter/test_panel_journal_view.py
git commit -m "feat(panel): чтение журнала + инвариант молчащего писателя"
```

---

### Task 6: `suppressed` + исход схлопываются в одну строку

**Файлы:**
- Изменить: `app/services/jarvis_farm.py`
- Тест: `tests/chatter/test_panel_journal_view.py`

В журнале это честно две записи (писатель фиксирует факты), но на экране один
инцидент обязан занимать один визуальный элемент — первый экран мы только что
чистили от одиннадцати одинаковых грязных деревьев.

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/chatter/test_panel_journal_view.py`:

```python
def test_suppressed_then_down_is_one_line_with_the_confirmation_delay():
    now = 1_000_000.0
    recs = [_rec(now - 600, kind="suppressed"), _rec(now - 300, kind="down")]
    rows = F.collapse_suppressed(recs)
    assert len(rows) == 1, rows
    assert rows[0]["kind"] == "suppressed"
    assert rows[0]["outcome"] == "down"
    assert rows[0]["ts"] == now - 600, "время взято от подтверждения, а не от падения"
    assert rows[0]["after_s"] == 300.0


def test_suppressed_then_recovered_says_it_rose_by_itself():
    now = 1_000_000.0
    recs = [_rec(now - 600, kind="suppressed"), _rec(now - 300, kind="recovered")]
    rows = F.collapse_suppressed(recs)
    assert len(rows) == 1 and rows[0]["outcome"] == "recovered"


def test_a_suppressed_fall_with_no_outcome_yet_says_so():
    rows = F.collapse_suppressed([_rec(1.0, kind="suppressed")])
    assert len(rows) == 1 and rows[0]["outcome"] is None


def test_other_checks_are_not_swallowed_by_the_collapse():
    """Парный сторож: схлопывание по ОДНОЙ пробе. Схлопнуть соседнюю значит
    спрятать чужой инцидент."""
    now = 1_000_000.0
    recs = [_rec(now - 600, check="backend", kind="suppressed"),
            _rec(now - 500, check="bot_heartbeat", kind="down"),
            _rec(now - 300, check="backend", kind="down")]
    rows = F.collapse_suppressed(recs)
    assert len(rows) == 2, rows
    assert {r["check"] for r in rows} == {"backend", "bot_heartbeat"}
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_journal_view.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `AttributeError: ... has no attribute 'collapse_suppressed'`.

- [ ] **Шаг 3: реализация**

В `app/services/jarvis_farm.py` под `journal()`:

```python
def collapse_suppressed(records: list) -> list:
    """`suppressed` + исход по ТОЙ ЖЕ пробе → одна строка.

    В журнале обе записи законны: писатель фиксирует факты. На экране один
    инцидент обязан занимать один элемент — приглушённая первая строка всё
    равно осталась бы вторым элементом про то же событие, а первый экран мы
    чистили ровно от такого (одиннадцать одинаковых грязных деревьев).

    Исходов ровно три: `down` (подтвердилось), `recovered` (поднялось само),
    None (окно ещё идёт). Четвёртого нет: `changed` до `down` прийти не может —
    причина запоминается только вместе с алертом.

    Время строки — время ПЕРВОЙ записи: искать инцидент владелец будет по
    моменту падения, а не подтверждения.
    """
    records = sorted(records, key=lambda r: float(r.get("ts") or 0.0))
    consumed, out = set(), []
    for i, rec in enumerate(records):
        if i in consumed:
            continue
        if rec.get("kind") != "suppressed":
            out.append(rec)
            continue
        row = dict(rec)
        row["outcome"], row["after_s"] = None, None
        for j in range(i + 1, len(records)):
            nxt = records[j]
            if j in consumed or nxt.get("check") != rec.get("check"):
                continue
            if nxt.get("kind") in ("down", "recovered"):
                consumed.add(j)
                row["outcome"] = nxt["kind"]
                row["after_s"] = float(nxt.get("ts") or 0.0) - float(rec.get("ts") or 0.0)
            break
        out.append(row)
    return out
```

- [ ] **Шаг 4: убедиться, что тесты зелёные**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_journal_view.py -q --no-header -p no:cacheprovider
```

Ожидаемо: `9 passed`.

- [ ] **Шаг 5: коммит**

```bash
cd /c/jarvis && git add app/services/jarvis_farm.py tests/chatter/test_panel_journal_view.py
git commit -m "feat(panel): подавленное падение и его исход — одна строка"
```

---

### Task 7: Блок «Что изменилось» на панели

**Файлы:**
- Изменить: `app/services/jarvis_farm.py` (`snapshot_fast`)
- Изменить: `app/routers/jarvis_panel.py`
- Тест: `tests/chatter/test_panel_journal_view.py`

- [ ] **Шаг 1: написать падающий тест**

Дописать в `tests/chatter/test_panel_journal_view.py`:

```python
import importlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

KEY = "test-owner-key"


def _client(tmp_path, monkeypatch, records, *, beat_age=5.0, backend_since=None):
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _journal(tmp_path, records, beat_age=beat_age)

    import app.routers.jarvis_panel as jp
    import app.routers.panels_auth as pa
    for m in (pa, jp):
        importlib.reload(m)
    monkeypatch.setattr(F, "ROOT", tmp_path)

    now = time.time()
    fast = {
        "collected_at": now,
        "external": F.Row("ext", "Внешний сторож", "bad", "НЕ настроен"),
        "processes": [F.Row("backend", "backend :8010", "ok", "PID 1",
                            {"since": backend_since or (now - 7200)})],
        "guardians": [F.Row("ops_watchdog", "ops_watchdog", "ok", "PID 2")],
        "journal": F.journal(now=now),
    }
    monkeypatch.setattr(F, "snapshot_fast", lambda: fast)
    monkeypatch.setattr(F, "slow_cached", lambda: None)
    api = FastAPI()
    for m in (pa, jp):
        api.include_router(m.router)
    return TestClient(api)


def _page(c):
    r = c.get("/panel/jarvis", headers={"X-Panels-Key": KEY})
    assert r.status_code == 200
    return r.text


def test_todays_events_are_open_and_older_days_are_counted(tmp_path, monkeypatch):
    """Три дня событий зальют первый экран так же, как его залили одиннадцать
    грязных деревьев 14.08. Сегодня раскрыто, вчера и позавчера — счётчиком."""
    now = time.time()
    recs = [_rec(now - 3600, detail="упал сегодня")]
    recs += [_rec(now - 2 * DAY - i, detail=f"позавчера {i}") for i in range(4)]
    body = _page(_client(tmp_path, monkeypatch, recs))
    assert "упал сегодня" in body
    grp = re.search(r"<details class='grp'><summary>(.*?)</summary>", body, re.S)
    assert grp, "старые сутки не свёрнуты в счётчик"
    assert "4" in grp.group(1), grp.group(1)


def test_the_restart_divider_stands_at_the_backend_start(tmp_path, monkeypatch):
    """Якорь объективный: не «пока тебя не было», а «здесь перезапустился
    бэкенд». Момент уже лежит в снапшоте — нового состояния не заводим."""
    now = time.time()
    body = _page(_client(tmp_path, monkeypatch,
                         [_rec(now - 3600, detail="до рестарта"),
                          _rec(now - 600, detail="после рестарта")],
                         backend_since=now - 1800))
    assert "перезапустился" in body
    assert body.index("после рестарта") < body.index("перезапустился") < body.index("до рестарта")


def test_a_silent_writer_replaces_the_list_with_words(tmp_path, monkeypatch):
    body = _page(_client(tmp_path, monkeypatch, [_rec(time.time() - 60)],
                         beat_age=10_000.0))
    assert "верить нельзя" in body
    assert "процесс не найден" not in body, "показан список, которому нельзя верить"


def test_a_confirmed_suppressed_fall_reads_as_one_line(tmp_path, monkeypatch):
    now = time.time()
    body = _page(_client(tmp_path, monkeypatch,
                         [_rec(now - 900, kind="suppressed"), _rec(now - 600, kind="down")]))
    assert body.count("подавлено в загрузочном окне") == 1
    assert "подтверждено через 5 мин" in body
```

- [ ] **Шаг 2: убедиться, что тест падает**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/test_panel_journal_view.py -q --no-header -p no:cacheprovider
```

Ожидаемо: 4 падения — блока в разметке нет.

- [ ] **Шаг 3: добавить журнал в быстрый снапшот**

В `app/services/jarvis_farm.py`, в `snapshot_fast()`, добавить ключ:

```python
    return {
        "collected_at": _now(),
        "external": external_watchdog(),
        "processes": processes(table),
        "guardians": guardians(table),
        # Журнал читается в БЫСТРОЙ части: потолок 5000 записей даёт файлу
        # жёсткую границу (~1 МБ), а разбор — единицы миллисекунд. «Что
        # изменилось» обязано быть на первом экране, а не ждать git.
        "journal": journal(),
    }
```

- [ ] **Шаг 4: разметка блока**

В `app/routers/jarvis_panel.py` добавить перед функцией `panel()`:

```python
# Подписи видов записи журнала. Закрытый список: седьмого вида не появится без
# правки этой таблицы и таблицы в спеке (§2.3).
_JOURNAL_KIND = {
    "down": "упало", "recovered": "поднялось", "changed": "новая причина",
    "suppressed": "подавлено в загрузочном окне", "reboot": "перезагрузка",
    "rotated": "журнал обрезан",
}


def _journal_rows(records, now: float) -> str:
    out = []
    for r in sorted(records, key=lambda x: -float(x.get("ts") or 0.0)):
        kind = _JOURNAL_KIND.get(r.get("kind"), r.get("kind", "—"))
        tail = ""
        if r.get("kind") == "suppressed":
            mins = int((r.get("after_s") or 0) // 60)
            tail = {"down": f", подтверждено через {mins} мин",
                    "recovered": f", поднялось само через {mins} мин",
                    None: ", исход пока неизвестен"}[r.get("outcome")]
        out.append(
            f"<tr><td><b>{esc(r.get('check', '—'))}</b></td>"
            f"<td data-l='Что' class='sub'>{esc(kind + tail)}</td>"
            f"<td data-l='Деталь' class='sub'>{esc((r.get('detail') or '')[:110])}</td>"
            f"<td data-l='Когда' class='sub'>{esc(_ago(r.get('ts'), now))}</td></tr>")
    return "".join(out)


def _journal_html(snap_journal, backend_since, now: float) -> str:
    """Блок «Что изменилось»: 72 часа, сутками, разделитель рестарта.

    Якорь ОБЪЕКТИВНЫЙ: не «пока тебя не было» (для этого нужна была бы метка
    прочтения, то есть мутирующая ручка), а окно в часах плюс момент рестарта
    бэкенда, который уже лежит в снапшоте.
    """
    records, note = snap_journal
    if note:
        # Молчание писателя не имеет права выглядеть тишиной фермы.
        return f"<h2>Что изменилось</h2><div class='note broken'>{esc(note)}</div>"
    if not records:
        return ("<h2>Что изменилось</h2><div class='card'>"
                "<div class='empty'>Переходов не было — за 72 часа ферма ни разу "
                "не меняла состояние.</div></div>")

    rows = F.collapse_suppressed(records)
    if backend_since:
        rows.append({"ts": backend_since, "check": "—", "kind": "_restart",
                     "detail": "здесь перезапустился бэкенд"})
    day = 86400.0
    buckets = {}
    for r in rows:
        age_days = int((now - float(r.get("ts") or 0.0)) // day)
        buckets.setdefault(min(age_days, 2), []).append(r)

    titles = {0: "Сегодня", 1: "Вчера", 2: "Раньше"}
    parts = []
    for key in sorted(buckets):
        inner = ("<div class='card'><table><tbody>"
                 + _journal_rows(buckets[key], now) + "</tbody></table></div>")
        if key == 0:
            parts.append(f"<h2>{titles[key]}</h2>{inner}")
        else:
            # Свёрнуто, а не спрятано: список под сводкой. Тот же приём, что у
            # одиннадцати грязных деревьев.
            parts.append(_group(f"{titles[key]}: {len(buckets[key])}", inner, 1))
    return "<h2>Что изменилось</h2>" + "".join(parts)
```

Добавить `"_restart": "рестарт"` в `_JOURNAL_KIND`. Затем в `panel()` вставить
блок между аномалиями и колонкой состояния — в разметке `body` после
`<div>{anomalies}</div>` дописать внутри той же колонки:

```python
 <div>{anomalies}
  {_journal_html(fast.get("journal", ([], "")), backend_since, now)}
 </div>
```

и перед сборкой `body` вычислить:

```python
    backend_row = next((r for r in fast["processes"] if r.key == "backend"), None)
    backend_since = backend_row.extra.get("since") if backend_row else None
```

- [ ] **Шаг 5: убедиться, что тесты зелёные, регрессий нет**

```
cd /c/jarvis && ./.venv/Scripts/python.exe -m pytest tests/chatter/ -q --no-header -p no:cacheprovider
```

Ожидаемо: все зелёные, включая 13 в `test_panel_journal_view.py`.

- [ ] **Шаг 6: коммит**

```bash
cd /c/jarvis && git add app/services/jarvis_farm.py app/routers/jarvis_panel.py tests/chatter/test_panel_journal_view.py
git commit -m "feat(panel): блок «Что изменилось» — 72 ч, сутками, с разделителем рестарта"
```

---

### Task 8: Мутации DEV-26 в оба гейта

**Файлы:**
- Изменить: `scripts/mutate_ops_watchdog.py`
- Изменить: `scripts/mutate_panels_hierarchy.py`

- [ ] **Шаг 1: мутации сторожа**

В `scripts/mutate_ops_watchdog.py` добавить `JT = "tests/test_panel_event_journal.py"`
и шесть мутаций:

```python
    ("подавленное падение перестало попадать в журнал", WATCHDOG,
     [('                if st["fail"] >= debounce:\n'
       '                    fire(check, "suppressed", res, reason)',
       '                pass')],
     f"{JT}::test_a_suppressed_fall_is_a_transition_but_not_an_alert"),

    ("о подавленном падении снова шлётся алерт", WATCHDOG,
     [('ALERTING_KINDS = ("down", "recovered", "changed")',
       'ALERTING_KINDS = ("down", "recovered", "changed", "suppressed")')],
     f"{JT}::test_a_suppressed_fall_is_a_transition_but_not_an_alert"),

    ("ротация режет по возрасту, но молчит", WATCHDOG,
     [('        if dropped:\n'
       '            kept.append({"ts": now, "check": "_journal", "kind": "rotated",',
       '        if False:\n'
       '            kept.append({"ts": now, "check": "_journal", "kind": "rotated",')],
     f"{JT}::test_rotation_leaves_a_marker_in_the_journal"),

    ("потолок по числу записей снят", WATCHDOG,
     [("JOURNAL_MAX_RECORDS = 5000", "JOURNAL_MAX_RECORDS = 10**9")],
     f"{JT}::test_the_record_ceiling_catches_a_restart_storm"),

    ("маркер живости обновляется даже при провале записи", WATCHDOG,
     [("    if journal_append(journal):\n        touch_beat()",
       "    journal_append(journal)\n    touch_beat()\n    if False:\n        touch_beat()")],
     f"{JT}::test_a_failed_write_does_not_refresh_the_marker"),

    ("ребут больше не попадает в журнал", WATCHDOG,
     [('    if reboot_text and boot_time is not None:\n'
       '        journal.insert(0, reboot_record(boot_time, time.time()))',
       '    if False:\n'
       '        journal.insert(0, reboot_record(boot_time, time.time()))')],
     f"{JT}::test_a_reboot_becomes_a_journal_record"),
```

- [ ] **Шаг 2: мутации панели**

В `scripts/mutate_panels_hierarchy.py` добавить `JV = "tests/chatter/test_panel_journal_view.py"`
и шесть мутаций:

```python
    ("молчащий писатель снова читается как тишина", FARM,
     [('    if beat_age is None or beat_age > JOURNAL_BEAT_FRESH:',
       '    if False:')],
     f"{JV}::test_a_stale_marker_means_the_writer_is_silent_not_the_farm"),

    ("тревога о писателе стала вечной", FARM,
     [('    if beat_age is None or beat_age > JOURNAL_BEAT_FRESH:',
       '    if True:')],
     f"{JV}::test_a_fresh_marker_with_an_empty_journal_means_real_silence"),

    ("порог свежести маркера поднят до суток", FARM,
     [("JOURNAL_BEAT_FRESH = 180.0", "JOURNAL_BEAT_FRESH = 86400.0")],
     f"{JV}::test_a_stale_marker_means_the_writer_is_silent_not_the_farm"),

    ("схлопывание проглотило соседнюю пробу", FARM,
     [('            if j in consumed or nxt.get("check") != rec.get("check"):',
       '            if j in consumed:')],
     f"{JV}::test_other_checks_are_not_swallowed_by_the_collapse"),

    ("подавленное и подтверждение снова две строки", FARM,
     [('        if rec.get("kind") != "suppressed":\n            out.append(rec)\n            continue',
       '        out.append(rec)\n        continue')],
     f"{JV}::test_suppressed_then_down_is_one_line_with_the_confirmation_delay"),

    ("старые сутки снова выкладываются списком", JP,
     [('            parts.append(_group(f"{titles[key]}: {len(buckets[key])}", inner, 1))',
       '            parts.append(f"<h2>{titles[key]}</h2>{inner}")')],
     f"{JV}::test_todays_events_are_open_and_older_days_are_counted"),
```

- [ ] **Шаг 3: закоммитить — гейты требуют чистого дерева**

```bash
cd /c/jarvis && git add scripts/mutate_ops_watchdog.py scripts/mutate_panels_hierarchy.py
git commit -m "test(journal): мутации DEV-26 на писателя и на вид журнала"
```

- [ ] **Шаг 4: прогнать оба гейта**

```
cd /c/jarvis && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/mutate_ops_watchdog.py
cd /c/jarvis && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/mutate_panels_hierarchy.py
```

Ожидаемо: `Все N мутаций пойманы.` в обоих. `[СЛЕП]` — чинить ТЕСТ, а не мутацию.
`МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ` — фрагмент разошёлся с исходником, поправить фрагмент.

---

### Task 9: Приёмка живым падением

- [ ] **Шаг 1: мержить ТОЛЬКО с отмашкой владельца**

```bash
cd /c/jarvis && git checkout phase-4.0-unified-jarvis && git merge --ff-only arc/panel-event-journal
```

- [ ] **Шаг 2: ЯВНЫЙ рестарт бэкенда по точным PID**

Команда — как в Task 8 плана ленты (`docs/superpowers/plans/2026-08-14-jarvis-panel-feed-fixes.md`).

- [ ] **Шаг 3: живое падение и подъём**

Убить раннер chatter по ТОЧНОМУ PID (не по маске — маска в командной строке
скрипта совпадёт с ним самим), дождаться двух циклов сторожа:

```
cd /c/jarvis && PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "
import psutil
for c in [p for p in psutil.process_iter(['pid','name'])]:
    try: cl = ' '.join(c.cmdline() or [])
    except Exception: continue
    if 'telethon_run' in cl and (c.info['name'] or '').startswith('python'):
        print('кандидат', c.info['pid'], cl[:90])
"
```

Убить найденный PID точечно, подождать ~2 минуты, затем:

```
cd /c/jarvis && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -c "
from app.services import jarvis_farm as F
recs, note = F.journal()
print('пояснение:', note or '(пусто — списку можно верить)')
for r in recs[-10:]: print(' ', r['kind'], r['check'], r['detail'][:60])
"
```

Ожидаемо: записи `down` и затем `recovered` по `chatter_runner`, обе с временем.

- [ ] **Шаг 4: мёртвый писатель**

Остановить задачу `JarvisOpsWatchdog`, подождать 180 с, открыть панель: на месте
списка — слова о молчащем писателе. Поднять задачу обратно, убедиться, что строка
исчезла.

- [ ] **Шаг 5: ротация**

```
cd /c/jarvis && PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "
import json, time, shutil, pathlib
p = pathlib.Path('state/panel_events.jsonl')
shutil.copy(p, p.with_suffix('.jsonl.bak'))
now = time.time()
with open(p, 'a', encoding='utf-8') as f:
    for i in range(5200):
        f.write(json.dumps({'ts': now - 60, 'check': 'x', 'kind': 'down',
                            'reason': 'r', 'detail': 'заполнитель %d' % i},
                           ensure_ascii=False) + '\n')
print('набито')
"
cd /c/jarvis && PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/ops_watchdog.py
cd /c/jarvis && PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -c "
import json, pathlib
recs = [json.loads(l) for l in pathlib.Path('state/panel_events.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
print('записей:', len(recs))
print('маркеры:', [r['detail'] for r in recs if r['kind'] == 'rotated'])
"
```

Ожидаемо: записей ≤ 5001, среди них маркер `rotated` со счётчиком.
После проверки восстановить журнал из `.bak`.

- [ ] **Шаг 6: бюджет первого экрана с полным журналом**

Замерить `/panel/jarvis` на журнале, набитом до потолка: не хуже 0.13 с на
тёплом кэше. Если хуже — запасной ход из §4.5 спеки: чтение с конца блоками по
64 КБ до первой записи старше 72 часов.

- [ ] **Шаг 7: скриншоты приёмки** на 707 px и 344 px, как в заходе 1.

---

## Самопроверка плана против спеки

| Требование спеки | Задача |
|---|---|
| §2.1 писатель — ops_watchdog, stdlib-only | Task 1, 3, 4 (тест грузит модуль по пути, без импорта app/) |
| §2.2 JSONL, поля ts/check/kind/reason/detail | Task 1, 3 |
| §2.3 шесть видов записи, список закрыт | Task 1 (4 вида), Task 3 (`rotated`), Task 4 (`reboot`), Task 7 (`_JOURNAL_KIND`) |
| §2.4 ротация «30 сут ИЛИ 5000», обе с маркером, атомарная перезапись | Task 2, Task 3 |
| §2.5 маркер живости, touch в конце и только при успехе | Task 3, Task 4 |
| §3 `transitions()` под `evaluate()`, сигнатура неизменна | Task 1 |
| §4.1 окно 72 ч, разделитель рестарта из `since` | Task 5, Task 7 |
| §4.2 группировка по суткам, раскрыты 24 ч | Task 7 |
| §4.3 место — под аномалиями | Task 7 |
| §4.4 инвариант молчащего писателя | Task 5, Task 7 |
| §4.5 журнал в быстрой части | Task 7 |
| §4.6 схлопывание `suppressed` + исход, три исхода | Task 6, Task 7 |
| §6 ловушки 1-5, 9 | Tasks 1, 3, 4, 5 |
| §7 сторожа журнала, ротации, маркера, панели | Tasks 1-7 |
| §7 мутации в оба гейта | Task 8 |
| §8 приёмка журнала, пункты 5-9 | Task 9 |

§5 (лента) целиком закрыт первым планом,
`docs/superpowers/plans/2026-08-14-jarvis-panel-feed-fixes.md`.
