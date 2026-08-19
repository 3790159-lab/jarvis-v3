# -*- coding: utf-8 -*-
"""Сторож на ДРЕЙФ ПРЕФИКСА BRAIN — гейты Д1…Д3 спеки
`docs/superpowers/specs/2026-08-19-classifier-on-haiku.md`, §9 (плюс §2.0 про
разные токенизаторы), развёрнутые владельцем до Д1…Д10.

Написано ОТ СПЕКИ И ОТ ПУБЛИЧНОГО КОНТРАКТА. План реализации
(`docs/superpowers/plans/…-prefix-drift-guard-plan.md`) НЕ читался и не грепался
намеренно: сторож, написанный по плану, наследует допущения реализации и
зеленеет вместе с её ошибкой. Читались §9 и §2.0 спеки, публичный контракт
модуля и ПРОДАКШЕН-код, которым сторож обязан пользоваться
(`chatter.core.brain.build_system_prompt`, `chatter.config.loader`,
`chatter.telethon_run`, `chatter.onboard.checks`).

──────────────────────────────────────────────────────────────────────────────
ЧЕМ ЭТОТ ФАЙЛ ОТЛИЧАЕТСЯ ОТ ОБЫЧНЫХ СТОРОЖЕЙ

Он сторожит СТОРОЖА, поставленного ради одного класса дефекта: величина, от
которой зависит цена, росла молча (§9.1: brain $0.0134 → $0.02184 за вызов,
×1.63 за месяц; полосы 790 → 653 диалога — узнали случайно). Значит у этого
файла ровно четыре способа стать зелёной ширмой, и все четыре сторожатся
поимённо:

  1. **Сторож не краснеет на дрейфе** — Д1, и отдельно граница Д2 (`limit` ещё
     молчит, `limit+1` уже кричит). Порог, у которого не проверена граница,
     проверен только в середине диапазона.
  2. **Эталон догоняет дрейф сам.** Это ГЛАВНОЕ свойство §9.2 и главный тест
     файла (Д3): самообновляющийся эталон воспроизводит ровно тот дефект, ради
     которого раздел написан, — он всегда молчит. Проверяется ПОБАЙТОВО: «нужная
     строка на месте» зеленеет и на файле, переписанном с другим числом.
  3. **Сверка идёт разными токенизаторами** (§2.0). Один и тот же текст весит по
     haiku и по sonnet по-разному (расхождение 7–12%), поэтому считать обязана
     модель ЭТАЛОНА, а не текущая модель клиента, — Д6.
  4. **Сторож молчит потому, что не отработал.** Сбой сети (Д4), отсутствующий
     эталон (Д5), битый файл эталонов (Д8) — три состояния, где «ничего не
     сказал» неотличимо от «всё в порядке». Каждое обязано быть ГРОМКИМ и при
     этом НЕ ронять клиента (§9.2 «не отказ», §9.5 «сбой count_tokens не роняет
     старт клиента, но кричит в лог — DEV-18»).

Ноль сети: счётчик токенов ВСЕГДА подсовывается фейком, а там, где счётчик не
инъектируется по контракту (`load_personas`), фейком подсовывается сам SDK
`anthropic` — и отсутствие сетевого замера доказывается тем, что фейк не
позвали НИ РАЗУ. Ноль живых секретов, ноль живой БД (`Store(":memory:")`), ноль
записи в дерево репозитория.

Фикстуры клиента собираются существующими в репозитории приёмами:
`tests.chatter.test_loader._make_client` (пять файлов + `load_config`) и
`tests.test_onboard_checks.write_client` (каталог в форме `build/onboard/<slug>`
плюс согласованный `report.json`) — свой способ здесь не выдумывался.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from chatter.config.loader import load_config
from chatter.core import prefix_budget
from chatter.core.brain import build_system_prompt
from chatter.onboard import checks
from chatter.storage.db import Store
from tests.chatter.test_loader import SETTINGS as _LOADER_SETTINGS, _make_client
from tests.test_onboard_checks import report_document, write_client

# ─────────────────────────────────────────────────────────────────────────────
# Числа §9.3 — ЛИТЕРАЛАМИ, один раз, здесь.
#
# Эталон, разошедшийся со спекой, — молчащий сторож: он продолжает сверять, но
# уже не с тем, о чём договорились. Поэтому Д7 сравнивает файл репозитория с
# ПЕРЕПИСАННОЙ ОТ РУКИ таблицей спеки, а не с самим собой.
# ─────────────────────────────────────────────────────────────────────────────

# ДЕЙСТВУЮЩИЕ эталоны — §9.3 «после срезки (а)». Исходный замер был
# {volska: 9131, yarina: 14847, demo: 2394}; спека 2026-08-19-playbook-trim
# §1(а) вырезала секцию ключевых слов из текста для модели, и эталоны
# опущены ТЕМ ЖЕ коммитом — иначе сторож, ловящий только РОСТ, замолчал бы
# на полторы тысячи токенов. Числа переписаны из спеки РУКАМИ: сравнение
# файла с самим собой ничего не доказывает.
SPEC_9_3_BRAIN = {"volska": 8586, "yarina": 13869, "demo": 2200}
SPEC_9_4_THRESHOLD = 15               # N = 15% (§9.4)

SONNET = "claude-sonnet-5"            # модель brain — ею снят эталон §9.3
HAIKU = "claude-haiku-4-5"            # модель классификатора — НЕ ею считать brain

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def limit_for(baseline: int, percent: int) -> int:
    """Предел из контракта, ПЕРЕПИСАННЫЙ здесь целиком.

    Импортировать формулу из проверяемого модуля значит проверить, что модуль
    равен сам себе: сдвиг на `int(baseline * 1.15)` или на округление вверх
    прошёл бы незамеченным.
    """
    return baseline * (100 + percent) // 100


# ─────────────────────────────────────────────────────────────────────────────
# Инструменты
# ─────────────────────────────────────────────────────────────────────────────

class Counter:
    """Фейковый `count_tokens`: запоминает КАЖДЫЙ вызов и его аргументы.

    Аргументы запоминаются целиком, потому что половина гейта §2.0 — это не
    «сколько насчитали», а «КАКОЙ МОДЕЛЬЮ и ПО КАКОМУ ТЕКСТУ считали».
    """

    def __init__(self, value: int | None = None, *, raises: BaseException | None = None):
        self._value = value
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model, system_text=None, *args, **kwargs):
        text = system_text if system_text is not None else kwargs.get("system_text", "")
        self.calls.append((model, text))
        if self._raises is not None:
            raise self._raises
        return self._value

    @property
    def models(self) -> list[str]:
        return [m for m, _ in self.calls]


def make_cfg(tmp_path: Path, *, slug: str = "demo", model: str = HAIKU):
    """Клиент из пяти файлов + живой `load_config` (приём `test_loader`)."""
    settings = _LOADER_SETTINGS.replace(f"model: {HAIKU}", f"model: {model}")
    assert f"model: {model}" in settings, "фикстура настроек не подставила модель"
    _make_client(tmp_path, slug=slug, settings=settings)
    return load_config(tmp_path, slug)


def baseline(slug: str, brain: int, *, measured_with: str = SONNET,
             measured_on: str = "2026-08-19"):
    return prefix_budget.Baseline(
        slug=slug, brain_tokens=brain, measured_with=measured_with,
        measured_on=measured_on)


def write_baselines(path: Path, clients: dict[str, dict], *,
                    threshold: int = SPEC_9_4_THRESHOLD) -> Path:
    """Файл эталонов в форме контракта: `threshold_percent` + `clients`."""
    doc = {"threshold_percent": threshold, "clients": clients}
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def entry(brain: int, *, measured_with: str = SONNET, measured_on: str = "2026-08-19"):
    return {"brain": brain, "measured_with": measured_with, "measured_on": measured_on}


def only(verdicts) -> "prefix_budget.PrefixVerdict":
    """Вердикт `brain_drift` из отчёта — РОВНО ОДИН.

    Отчёт с §2.3 несёт две проверки (рядом едет сторож порога кэша), поэтому
    выбираем свою по имени, а не по длине списка. Но «ровно один» остаётся
    требованием: пусто — это исчезнувшая проверка, неотличимая от пройденной;
    два — это два ответа на один вопрос, и меньший погасит больший молча.
    """
    rows = [v for v in verdicts if v.check == "brain_drift"]
    assert len(rows) == 1, f"ожидался ровно один вердикт brain_drift, пришли {list(verdicts)!r}"
    return rows[0]


# ─────────────────────────────────────────────────────────────────────────────
# Д1. Дрейф на N%+1 токен — ГРОМКО
# ─────────────────────────────────────────────────────────────────────────────

def test_d1_one_token_past_the_limit_is_loud(tmp_path):
    """Д1: префикс, раздутый ровно на N%+1 токен против эталона, даёт громкий
    вердикт.

    Почему важно: §9.1 — промпт распух на треть НЕ одной правкой, а суммой
    мелких. Сторож, который срабатывает только на грубом скачке, пропустил бы
    ровно тот дрейф, ради которого он ставится.
    """
    cfg = make_cfg(tmp_path)
    base = 1000
    over = limit_for(base, SPEC_9_4_THRESHOLD) + 1        # 1150 + 1
    counter = Counter(over)

    v = prefix_budget.brain_drift_verdict(
        cfg, baselines={cfg.slug: baseline(cfg.slug, base)},
        threshold_percent=SPEC_9_4_THRESHOLD, counter=counter)

    assert v.kind == "drift"
    assert v.ok is False
    assert v.loud is True
    assert v.fatal is False                                # §9.2: не отказ
    assert v.slug == cfg.slug
    assert v.check == "brain_drift"
    assert v.actual == over
    assert v.baseline == base
    assert v.threshold_percent == SPEC_9_4_THRESHOLD
    # Владелец получает алерт, а не загадку: оба числа обязаны быть названы.
    assert str(over) in v.message
    assert str(base) in v.message


# ─────────────────────────────────────────────────────────────────────────────
# Д2. В пределах эталона — ТИШИНА, и граница названа явно
# ─────────────────────────────────────────────────────────────────────────────

def test_d2_comfortably_within_the_baseline_is_silent(tmp_path):
    """Д2: префикс в пределах эталона молчит.

    Почему важно: §9.2 ставит алерт владельцу тем же каналом, что у прочих
    сторожей. Ложный крик на шести клиентах = канал, который перестают читать,
    и настоящий дрейф уедет вместе с шумом.
    """
    cfg = make_cfg(tmp_path)
    counter = Counter(900)

    v = prefix_budget.brain_drift_verdict(
        cfg, baselines={cfg.slug: baseline(cfg.slug, 1000)},
        threshold_percent=SPEC_9_4_THRESHOLD, counter=counter)

    assert v.kind == "within"
    assert v.ok is True
    assert v.loud is False
    assert v.fatal is False
    assert v.actual == 900
    assert v.baseline == 1000


def test_d2_the_boundary_is_the_limit_itself(tmp_path):
    """Д2 (граница): РОВНО предел (`actual == baseline*(100+N)//100`) ещё
    молчит, `limit+1` уже кричит.

    Почему важно: порог, проверенный только серединой диапазона, свободно
    съезжает на единицу — `>=` вместо `>`, округление вверх вместо целочисленного
    деления. Такой сдвиг либо добавляет ложный крик каждому клиенту у границы,
    либо (в другую сторону) молча дарит лишний процент дрейфа. Граница названа
    здесь числом, а не формулой из проверяемого модуля.
    """
    cfg = make_cfg(tmp_path)
    base = 1000
    limit = limit_for(base, SPEC_9_4_THRESHOLD)
    assert limit == 1150, "предел посчитан не так, как записано в контракте"

    bl = {cfg.slug: baseline(cfg.slug, base)}

    at_limit = prefix_budget.brain_drift_verdict(
        cfg, baselines=bl, threshold_percent=SPEC_9_4_THRESHOLD,
        counter=Counter(limit))
    assert at_limit.kind == "within", "предел включительно — это ещё НЕ дрейф"
    assert at_limit.ok is True
    assert at_limit.loud is False

    past_limit = prefix_budget.brain_drift_verdict(
        cfg, baselines=bl, threshold_percent=SPEC_9_4_THRESHOLD,
        counter=Counter(limit + 1))
    assert past_limit.kind == "drift", "предел + 1 токен — это уже дрейф"
    assert past_limit.ok is False
    assert past_limit.loud is True


# ─────────────────────────────────────────────────────────────────────────────
# Д3. ГЛАВНОЕ СВОЙСТВО §9.2: эталон НЕ обновляется сам
# ─────────────────────────────────────────────────────────────────────────────

def test_d3_baselines_file_is_byte_identical_after_the_guard_fires(tmp_path):
    """Д3 (главный тест файла): после СРАБАТЫВАНИЯ сторожа файл эталонов не
    изменился ПОБАЙТОВО.

    Почему важно: §9.2 — «эталон поднимает ЧЕЛОВЕК осознанной правкой».
    Самообновляющийся эталон воспроизвёл бы ровно тот дефект, ради которого
    сторож ставится: он бы догонял дрейф и всегда молчал. Сверка байтами, а не
    «нужная строка на месте»: файл, переписанный с числом 1151 вместо 1000,
    прошёл бы проверку «volska в файле есть» и «ключ threshold_percent на
    месте» — и молчал бы дальше.
    """
    cfg = make_cfg(tmp_path)
    path = write_baselines(tmp_path / "prefix_baselines.yaml",
                           {cfg.slug: entry(1000)})
    before = path.read_bytes()

    counter = Counter(limit_for(1000, SPEC_9_4_THRESHOLD) + 1)
    verdicts = prefix_budget.check_client_prefixes(
        cfg, counter=counter, baselines_path=path)

    fired = only(verdicts)
    assert fired.loud is True and fired.kind == "drift", (
        "тест ничего не доказал бы, не сработай сторож: проверять «файл не "
        f"тронут» после НЕсработавшего сторожа бессмысленно; пришло {fired!r}")

    after = path.read_bytes()
    assert after == before, (
        "файл эталонов изменился после срабатывания — это ровно тот дефект, "
        "ради которого написан §9.2 (эталон догнал дрейф сам)")


def test_d3_repo_baselines_file_untouched_by_a_default_path_run(tmp_path):
    """Д3 (вторая половина): прогон БЕЗ явного пути тоже ничего не пишет.

    Почему важно: тест выше подсовывает свой файл, и реализация, которая пишет
    только в `DEFAULT_BASELINES_PATH`, прошла бы его чисто. Здесь сторож гоняется
    по боевому файлу репозитория — тому самому, который поднимает человек, — и
    его байты сверяются до и после.
    """
    cfg = make_cfg(tmp_path, slug="demo")
    repo_file = Path(prefix_budget.DEFAULT_BASELINES_PATH)
    before = repo_file.read_bytes()

    baselines, threshold = prefix_budget.load_baselines()
    known = baselines[cfg.slug].brain_tokens
    counter = Counter(limit_for(known, threshold) + 1)

    fired = only(prefix_budget.check_client_prefixes(cfg, counter=counter))
    assert fired.kind == "drift" and fired.loud is True

    assert repo_file.read_bytes() == before, (
        "прогон по умолчанию переписал боевой файл эталонов")


# ─────────────────────────────────────────────────────────────────────────────
# Д4. Сбой замера — ГРОМКО, но наружу не летит
# ─────────────────────────────────────────────────────────────────────────────

def test_d4_counter_failure_is_measure_failed_and_does_not_escape(tmp_path):
    """Д4: `counter` бросил → `measure_failed`, громкий, исключение НЕ уходит
    наружу.

    Почему важно: §9.5 — «сбой `count_tokens` (сеть) не роняет старт клиента, но
    кричит в лог — DEV-18, молча не глотаем». Два требования в одной строке, и
    оба ломаются по-разному: проглоченное исключение делает сторожа вечно
    зелёным, пробившееся наружу — роняет клиента из-за сетевого чиха.
    """
    cfg = make_cfg(tmp_path)
    boom = RuntimeError("connection reset by peer")
    counter = Counter(raises=boom)

    v = prefix_budget.brain_drift_verdict(
        cfg, baselines={cfg.slug: baseline(cfg.slug, 1000)},
        threshold_percent=SPEC_9_4_THRESHOLD, counter=counter)

    assert v.kind == "measure_failed"
    assert v.ok is False
    assert v.loud is True
    assert v.fatal is False                                # клиент не падает
    assert v.actual is None
    # Не молча: причина обязана доехать до человека, иначе «замер не сделан» и
    # «замер сделан, всё хорошо» выглядят одинаково.
    assert "connection reset by peer" in v.message or "RuntimeError" in v.message


def test_d4_counter_failure_does_not_escape_check_client_prefixes(tmp_path):
    """Д4 (та же дыра этажом выше): сбой замера не пробивает и внешнюю функцию.

    Почему важно: `check_client_prefixes` — то, что зовут точки старта клиента и
    онбординга. Обработка, оставшаяся только во внутренней функции, здесь ничего
    не стоит.
    """
    cfg = make_cfg(tmp_path)
    path = write_baselines(tmp_path / "b.yaml", {cfg.slug: entry(1000)})

    v = only(prefix_budget.check_client_prefixes(
        cfg, counter=Counter(raises=OSError("network is unreachable")),
        baselines_path=path))

    assert v.kind == "measure_failed"
    assert v.loud is True
    assert v.fatal is False


# ─────────────────────────────────────────────────────────────────────────────
# Д5. Эталона нет — ГРОМКО, и число названо
# ─────────────────────────────────────────────────────────────────────────────

def test_d5_missing_baseline_is_loud_and_names_the_actual_number(tmp_path):
    """Д5: эталона для слага нет → громкий `no_baseline`, и в тексте есть
    ФАКТИЧЕСКОЕ число.

    Почему важно: «эталон поднимает человек» (§9.2) означает, что человеку надо
    что-то вписать руками. Сообщение без числа заставляет его лезть считать
    самому — а на шестом клиенте это значит, что строку не впишут вовсе и новый
    клиент навсегда останется без сторожа. Молчание же здесь было бы худшим
    исходом: клиент без эталона неотличим от клиента в пределах эталона.
    """
    cfg = make_cfg(tmp_path)
    measured = 4242

    v = prefix_budget.brain_drift_verdict(
        cfg, baselines={"somebody-else": baseline("somebody-else", 1000)},
        threshold_percent=SPEC_9_4_THRESHOLD, counter=Counter(measured))

    assert v.kind == "no_baseline"
    assert v.ok is False
    assert v.loud is True
    assert v.fatal is False
    assert v.baseline is None
    assert v.actual == measured
    assert str(measured) in v.message, (
        "человеку нужно ЧТО вносить руками — без числа сообщение бесполезно")
    assert cfg.slug in v.message


# ─────────────────────────────────────────────────────────────────────────────
# Д6. Считает модель ЭТАЛОНА, а не текущая модель клиента (§2.0)
# ─────────────────────────────────────────────────────────────────────────────

def test_d6_counting_uses_the_model_the_baseline_was_measured_with(tmp_path):
    """Д6: `counter` вызван с моделью ИЗ ЭТАЛОНА, а не с `cfg.settings.model`.

    Почему важно: §2.0 — у sonnet-5 и haiku-4-5 РАЗНЫЕ токенизаторы, один и тот
    же текст весит по-разному (замер 19.08: +7.5% / +6.9% / +12.1%). Эталон §9.3
    снят sonnet'ом. Сверка, посчитанная текущей моделью клиента, сравнивала бы
    числа из двух разных систем измерения — и при N=15% расхождение в 12%
    съедало бы почти весь порог: сторож либо кричал бы на ровном месте, либо
    молчал бы на настоящем дрейфе.

    Фикстура нарочно разводит модели: клиент живёт на haiku, эталон снят sonnet.
    """
    cfg = make_cfg(tmp_path, model=HAIKU)
    assert cfg.settings.model == HAIKU, "фикстура обязана отличаться от эталонной модели"

    counter = Counter(900)
    prefix_budget.brain_drift_verdict(
        cfg, baselines={cfg.slug: baseline(cfg.slug, 1000, measured_with=SONNET)},
        threshold_percent=SPEC_9_4_THRESHOLD, counter=counter)

    assert counter.calls, "счётчик не позвали вовсе — сверять было нечего"
    assert counter.models == [SONNET], (
        f"считали моделью {counter.models!r}, а эталон снят {SONNET!r} — "
        "сверка разными токенизаторами (§2.0)")
    assert HAIKU not in counter.models


def test_d6_the_measured_text_is_the_brain_stable_prefix(tmp_path):
    """Д6 (вторая половина): меряется ИМЕННО стабильный префикс brain.

    Почему важно: §9 сторожит «размер стабильного префикса brain» — то, что
    уходит кэшируемым system-блоком (`Brain.__init__`: `build_system_prompt`).
    Померив вместо него, скажем, только `cfg.playbook` или префикс с приклеенным
    изменчивым блоком времени, сторож сверял бы число с эталоном другой
    величины: он бы честно работал и честно врал.
    """
    cfg = make_cfg(tmp_path)
    counter = Counter(900)

    prefix_budget.brain_drift_verdict(
        cfg, baselines={cfg.slug: baseline(cfg.slug, 1000)},
        threshold_percent=SPEC_9_4_THRESHOLD, counter=counter)

    assert [text for _, text in counter.calls] == [build_system_prompt(cfg)]


# ─────────────────────────────────────────────────────────────────────────────
# Д7. Файл эталонов репозитория == таблица §9.3
# ─────────────────────────────────────────────────────────────────────────────

def test_d7_repo_baselines_match_the_spec_table_exactly():
    """Д7: `chatter/prefix_baselines.yaml` читается `load_baselines()` и
    содержит РОВНО числа §9.3 плюс `threshold_percent == 15` (§9.4).

    Почему важно: эталон, разошедшийся со спекой, — молчащий сторож. Он
    продолжает сверять, но уже не с тем, о чём договорились: занижённый порог
    даст шум, завышенный эталон проглотит дрейф, который уже случился. Числа
    здесь переписаны из спеки руками — сравнение файла с самим собой ничего не
    доказывает.
    """
    baselines, threshold = prefix_budget.load_baselines()

    assert threshold == SPEC_9_4_THRESHOLD

    got = {slug: baselines[slug].brain_tokens
           for slug in SPEC_9_3_BRAIN if slug in baselines}
    assert got == SPEC_9_3_BRAIN, (
        f"эталоны разошлись с таблицей §9.3: файл {got}, спека {SPEC_9_3_BRAIN}")

    for slug in SPEC_9_3_BRAIN:
        row = baselines[slug]
        assert row.slug == slug
        # Модель замера — обязательное поле: без неё Д6 нечем исполнить, сверка
        # молча поедет на текущей модели клиента (§2.0).
        assert row.measured_with, f"{slug}: не записано, какой моделью снят эталон"
        assert ISO_DATE.match(row.measured_on), (
            f"{slug}: дата замера {row.measured_on!r} не ISO — эталон, поднятый "
            "человеком, обязан быть датирован")

    # §9.3 прямо говорит: числа brain сняты sonnet'ом, «моделью brain». У volska
    # и yarina brain и правда живёт на sonnet (их settings.yaml), поэтому здесь
    # это проверяемое утверждение, а не пересказ.
    for slug in ("volska", "yarina"):
        assert baselines[slug].measured_with == SONNET


# ─────────────────────────────────────────────────────────────────────────────
# Д8. Битый файл эталонов
# ─────────────────────────────────────────────────────────────────────────────

def test_d8_broken_baselines_file_raises_from_load_baselines(tmp_path):
    """Д8 (нижний этаж): сломанный файл эталонов → `PrefixBaselineError`.

    Почему важно: битый YAML, прочитанный «как пустой словарь», превратил бы всех
    клиентов в `no_baseline` или, того хуже, в тишину. Ошибка обязана быть
    названа отдельным типом, а не утонуть в `KeyError` где-то выше.
    """
    path = tmp_path / "broken.yaml"
    path.write_text("threshold_percent: [15\nclients: {volska:\n", encoding="utf-8")

    with pytest.raises(prefix_budget.PrefixBaselineError):
        prefix_budget.load_baselines(path)


def test_d8_broken_baselines_file_does_not_escape_check_client_prefixes(tmp_path):
    """Д8 (верхний этаж): `check_client_prefixes` на битом файле НЕ бросает
    наружу и отдаёт ГРОМКИЙ вердикт.

    Почему важно: эту функцию зовут старт клиента и `--check` онбординга. Если
    она пробивает исключением, опечатка в файле эталонов роняет живого клиента —
    а §9.2 прямо говорит, что этот сторож «не отказ». Если же она молча вернёт
    пустой список, шесть клиентов останутся без сверки и никто не узнает.
    """
    cfg = make_cfg(tmp_path)
    path = tmp_path / "broken.yaml"
    path.write_text("clients: {volska: {brain: [\n", encoding="utf-8")

    verdicts = prefix_budget.check_client_prefixes(
        cfg, counter=Counter(900), baselines_path=path)

    v = only(verdicts)
    assert v.ok is False
    assert v.loud is True
    assert v.fatal is False, "битый файл эталонов не поднимает клиента, но и не роняет"
    assert v.message.strip(), "громкий вердикт без текста читать нечем"


# ─────────────────────────────────────────────────────────────────────────────
# Д9. Точка вызова №1: `telethon_run.load_personas`
# ─────────────────────────────────────────────────────────────────────────────

class _CountTokensSpy:
    """Фейковый SDK `anthropic`: единственная дверь к сети в этом процессе.

    Замер токенов по контракту идёт через `messages.count_tokens`. Подменив весь
    модуль, мы получаем и подъём `AnthropicLLM` без ключа/сети, и ЧЕСТНЫЙ ответ
    на вопрос «ходил ли кто-нибудь считать»: не «мы не нашли следов», а «дверь
    не открывали ни разу».
    """

    def __init__(self, value: int):
        self.value = value
        self.calls: list[dict] = []


def _install_fake_anthropic(monkeypatch, spy: _CountTokensSpy):
    import sys
    import types

    class _Messages:
        def count_tokens(self, **kwargs):
            spy.calls.append(kwargs)
            return types.SimpleNamespace(input_tokens=spy.value)

        def create(self, **kwargs):                       # pragma: no cover
            raise AssertionError("живой вызов модели в тесте")

    class _Anthropic:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return spy


def test_d9_fake_llm_mode_never_measures(tmp_path, monkeypatch):
    """Д9 (половина 1): в fake-режиме LLM `load_personas` НЕ зовёт счётчик.

    Почему важно: замер идёт по сети и стоит времени на каждом подъёме персоны.
    Дёрни его офлайновый прогон — и каждый тест, каждый дрил, каждый локальный
    запуск начал бы ходить в API (и падать там, где ключа нет). Доказывается
    сильной формой: фейковый SDK не позвали НИ РАЗУ, и `prefix_findings` пуст —
    хотя у слага `demo` эталон ЕСТЬ, замер вернул бы 999 999 токенов, и сторож,
    отработай он, обязан был бы закричать про дрейф.
    """
    spy = _install_fake_anthropic(monkeypatch, _CountTokensSpy(999_999))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from chatter import telethon_run as tr

    slug = "demo"
    make_cfg(tmp_path, slug=slug)
    personas = tr.load_personas(tmp_path, [slug], Store(":memory:"), llm_mode="auto")

    assert spy.calls == [], "офлайновый прогон сходил в сеть за замером токенов"
    assert personas[slug].prefix_findings == ()


def test_d9_real_llm_mode_measures_and_reports_loud_findings(tmp_path, monkeypatch):
    """Д9 (половина 2): на РЕАЛЬНОМ клиенте LLM счётчик зовётся, а громкий текст
    ложится в `PersonaBundle.prefix_findings`.

    Почему важно: §9.5 ставит сторож на СТАРТ КЛИЕНТА. Сторож, посчитавший всё
    правильно и оставивший результат себе, — это сторож, которого нет: владелец
    узнаёт о дрейфе из счёта. И обратная сторона: сбой замера не имеет права
    уронить `load_personas` — раннер обязан подняться (§9.5).

    Слаг взят С ЭТАЛОНОМ (`demo`), а замер задран заведомо выше предела: так
    этот гейт держит ровно своё («позвал и доложил») и не зависит от того, как
    решён вопрос отсутствующего эталона — его держит Д5.
    """
    measured = 999_777
    spy = _install_fake_anthropic(monkeypatch, _CountTokensSpy(measured))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from chatter import telethon_run as tr

    slug = "demo"
    make_cfg(tmp_path, slug=slug)
    personas = tr.load_personas(tmp_path, [slug], Store(":memory:"), llm_mode="real")

    assert spy.calls, "на реальном клиенте замер не выполнялся вовсе"
    findings = personas[slug].prefix_findings
    assert isinstance(findings, tuple)
    assert findings, "дрейф в 999 777 токенов остался внутри сторожа"
    assert any(str(measured) in f for f in findings), (
        f"громкий текст не называет фактическое число: {findings!r}")


def test_d9_measure_failure_does_not_break_load_personas(tmp_path, monkeypatch):
    """Д9 (третья сторона): падение замера НЕ роняет `load_personas`.

    Почему важно: §9.5 дословно — «сбой `count_tokens` (сеть) не роняет старт
    клиента, но кричит в лог». Сетевой чих при подъёме не имеет права оставить
    живого клиента без бота; при этом молчать о нём тоже нельзя.
    """
    import sys
    import types

    class _Messages:
        def count_tokens(self, **kwargs):
            raise OSError("network is unreachable")

    class _Anthropic:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from chatter import telethon_run as tr

    slug = "demo"
    make_cfg(tmp_path, slug=slug)
    personas = tr.load_personas(tmp_path, [slug], Store(":memory:"), llm_mode="real")

    assert slug in personas, "сбой замера уронил подъём персоны"
    assert personas[slug].prefix_findings, "сбой замера прошёл молча (DEV-18)"


def test_d9_a_silent_within_measurement_leaves_no_findings(tmp_path, monkeypatch):
    """Д9 (четвёртая сторона): замер В ПРЕДЕЛАХ эталона не оставляет НИ ОДНОЙ
    строки владельцу.

    Почему важно: `prefix_findings` — канал к владельцу, и половина контракта
    этого канала не «громкое доехало», а «спокойное НЕ доехало». Реализация,
    складывающая туда все вердикты подряд, шлёт по строке на каждом подъёме
    каждого клиента: на шести клиентах настоящий дрейф уедет вместе с пятью
    спокойными «в пределах» — тем самым механизмом, которым канал перестают
    читать (§9.2 ставит алерт в общий канал сторожей).

    Половина 2 этого гейта проверяет только «непустой список при дрейфе» и
    поэтому зеленеет на снятом фильтре `if v.loud` — дыру нашёл мутационный
    гейт `scripts/mutate_prefix_drift_guard.py`.

    Замер задан РОВНО эталоном из файла (а не литералом): подъём эталона
    человеком — законное событие §9.2 и не должен ронять этот тест.
    """
    baselines, _threshold = prefix_budget.load_baselines()
    at_baseline = baselines["demo"].brain_tokens
    spy = _install_fake_anthropic(monkeypatch, _CountTokensSpy(at_baseline))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from chatter import telethon_run as tr

    slug = "demo"
    # model=SONNET намеренно: у haiku порог включения кэша 4096, а замер здесь
    # задан эталоном brain demo (2394) — клиент на haiku законно ОТКАЗАЛСЯ бы
    # подниматься по §2.2, и тест про дрейф упал бы на чужом вердикте. Это не
    # обход сторожа порога: он проверен своим файлом, здесь предмет другой.
    make_cfg(tmp_path, slug=slug, model=SONNET)
    personas = tr.load_personas(tmp_path, [slug], Store(":memory:"), llm_mode="real")

    assert spy.calls, (
        "замер не выполнялся вовсе — тогда пустой список ничего не доказывает")
    assert personas[slug].prefix_findings == (), (
        "спокойный вердикт «в пределах» уехал владельцу: в таком шуме тонет "
        f"настоящий дрейф; пришло {personas[slug].prefix_findings!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Д10. Точка вызова №2: `onboard.checks.run_checks` — проверка C16
# ─────────────────────────────────────────────────────────────────────────────

def _c16(results):
    for r in results:
        if str(r.id).strip().upper() == "C16":
            return r
    raise AssertionError(
        "проверки C16 нет в результате — исчезнувшая проверка неотличима от "
        f"пройденной; вернулись {[r.id for r in results]}")


def test_d10_c16_is_registered_as_a_flag_not_as_a_red():
    """Д10 (реестр): C16 объявлена среди проверок И объявлена ФЛАГОМ.

    Почему важно: §9.2 — рост префикса может быть законным (клиент правит свой
    плейбук через пульт), поэтому дрейф brain «не отказ», в отличие от порога
    кэша §2.3. Красная C16 блокировала бы подключение клиента за его же законную
    правку; C16, забытая в реестре, не появилась бы в отчёте вовсе.
    """
    assert "C16" in checks.CHECK_IDS
    assert "C16" in checks.FLAG_IDS


def test_d10_without_a_counter_c16_says_the_measurement_did_not_happen(tmp_path):
    """Д10 (половина 1): `run_checks(..., token_counter=None)` даёт C16 ФЛАГОМ с
    сообщением, начинающимся со слов «ЗАМЕР НЕ ВЫПОЛНЕН».

    Почему важно: `--check` обязан ходить и без сети/ключа. Но «замер не делали»
    и «замер сделали, всё в пределах» — разные состояния, и склеить их в зелёную
    строку значит выдать непроведённую проверку за пройденную (то самое «прогон
    не состоялся» из правил `checks.py`). Слова названы дословно, потому что
    именно по ним человек в отчёте отличает одно от другого.
    """
    client_dir = write_client(tmp_path, slug="demo")

    results = checks.run_checks(client_dir, report_document(), slug="demo",
                               token_counter=None)
    row = _c16(results)

    assert row.is_flag is True
    assert row.blocked is False, "отсутствие счётчика — это флаг, а не сорванный прогон"
    assert row.message.startswith("ЗАМЕР НЕ ВЫПОЛНЕН"), row.message
    # `ok` — отдельное утверждение, и без него текст «ЗАМЕР НЕ ВЫПОЛНЕН» ничего
    # не стоит: строка с ним, но зелёная, читается в отчёте ровно как
    # пройденная проверка (статус видно раньше текста). Мутационный гейт
    # показал это прямо: `_red` → `_ok` при том же тексте не краснел нигде.
    assert row.ok is False, (
        "непроведённый замер объявлен зелёным — это выдача пропуска за "
        f"пройденную проверку: {row!r}")


def test_d10_c16_does_not_change_the_exit_code(tmp_path):
    """Д10 (половина 1б): C16 не влияет на код возврата `verdict()`.

    Почему важно: обе стороны флага обязаны проверяться отдельно. «Не роняет
    вердикт» без «не исчезает» даёт молчаливый флаг — ширму на месте самого
    механизма против ширмы; «не исчезает» без «не роняет» превращает законную
    правку плейбука в блокировку подключения.

    Вердикт считается на СОБРАННОМ вручную наборе: иначе тест зависел бы от
    того, удалось ли фикстуре сделать зелёными все прочие проверки, и «rc 0
    недостижим» маскировался бы под «флаг работает».
    """
    client_dir = write_client(tmp_path, slug="demo")
    real_c16 = _c16(checks.run_checks(client_dir, report_document(), slug="demo",
                                      token_counter=None))

    rows = [
        real_c16 if cid == "C16" else checks.CheckResult(
            id=cid, ok=True, is_flag=cid in checks.FLAG_IDS, message="ок")
        for cid in checks.CHECK_IDS
    ]

    assert checks.verdict(rows, reviewed=True) == 0
    # И флаг не покупает зелёное там, где его нет: невычитанный отчёт — это 1.
    assert checks.verdict(rows, reviewed=False) == 1


def test_d10_with_a_drifting_counter_c16_names_the_numbers_and_stays_a_flag(tmp_path):
    """Д10 (половина 2): со счётчиком, показывающим дрейф, C16 остаётся ФЛАГОМ
    (не красным), но текст называет числа.

    Почему важно: §2.3 (порог кэша) — красное, §9.2 (дрейф brain) — громкое, но
    не отказ. Это разные решения владельца, и склеить их значит либо блокировать
    подключение за законный рост плейбука, либо пропустить дефект, ради которого
    §2.3 сделан красным. Числа в тексте — единственное, что делает флаг
    действием: «префикс подрос» без «9131 → 11 000» нечем ни проверить, ни
    внести в эталон.

    Эталон берётся ИЗ ФАЙЛА, а не литералом: его подъём человеком (§9.2) —
    законное событие и не должен ронять этот тест. Литералы §9.3 держит Д7.
    """
    baselines, threshold = prefix_budget.load_baselines()
    base = baselines["demo"].brain_tokens
    drifted = limit_for(base, threshold) + 1
    counter = Counter(drifted)

    client_dir = write_client(tmp_path, slug="demo")
    results = checks.run_checks(client_dir, report_document(), slug="demo",
                                token_counter=counter)
    row = _c16(results)

    assert counter.calls, "счётчик подсунут, но C16 им не воспользовалась"
    assert row.is_flag is True
    assert row.ok is False, "дрейф найден — строка не может быть зелёной"
    assert row.blocked is False
    assert str(drifted) in row.message
    assert str(base) in row.message
    assert not row.message.startswith("ЗАМЕР НЕ ВЫПОЛНЕН"), (
        "замер выполнен — текст «не выполнен» здесь был бы прямой ложью")


def test_d10_check_run_does_not_touch_the_baselines_file(tmp_path):
    """Д10 + Д3: `--check` онбординга тоже не переписывает эталон.

    Почему важно: `checks.py` объявляет границей модуля «ноль записи на диск», а
    §9.2 требует, чтобы эталон поднимал человек. Вторая точка вызова — второй
    шанс для самообновляющегося эталона, и он закрывается тем же побайтовым
    сравнением.
    """
    repo_file = Path(prefix_budget.DEFAULT_BASELINES_PATH)
    before = repo_file.read_bytes()

    baselines, threshold = prefix_budget.load_baselines()
    drifted = limit_for(baselines["demo"].brain_tokens, threshold) + 1

    client_dir = write_client(tmp_path, slug="demo")
    checks.run_checks(client_dir, report_document(), slug="demo",
                      token_counter=Counter(drifted))

    assert repo_file.read_bytes() == before
