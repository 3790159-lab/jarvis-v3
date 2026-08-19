"""Сторож на дрейф стабильного префикса brain (спека 2026-08-19 §9).

Зачем модуль вообще: 19.08 при пересчёте тарифных полос выяснилось, что brain
подорожал ×1.63 за месяц ($0.0134 → $0.02184 за вызов), и полосы съехали
790 → 653 диалога в месяц. Аварии не было ни одной: каждая арка (слот
обязательств, платежи, правила ответа) добавляла в системный промпт по
чуть-чуть, каждая правка по отдельности выглядела дешёвой, а экономика поехала
СУММОЙ. Величина, от которой прямо зависит цена, жила без сторожа — «её же
никто не менял». Её и правда никто не менял: её растили. Узнали мы об этом
случайно, при пересчёте под встречи.

ГЛАВНЫЙ ЗАПРЕТ МОДУЛЯ (§9.2). Эталон поднимает ЧЕЛОВЕК осознанной правкой
`chatter/prefix_baselines.yaml`. Ни одна функция здесь НЕ ПИШЕТ в этот файл ни
при каких условиях — даже «просто чтобы догнать законный рост». Эталон,
обновляющий себя сам, воспроизвёл бы ровно тот дефект, ради которого сторож
ставится: он бы догонял дрейф и всегда молчал.

Сторож ГРОМКИЙ, но НЕ отказ (§9.2): рост префикса бывает законным — клиент
правит свой плейбук через пульт, — и запрещать из-за этого старт нельзя.
Поэтому `fatal` во всех вердиктах ниже ВСЕГДА False, а сбой замера (сеть) не
роняет клиента, но и не глотается молча (DEV-18): он становится громким
вердиктом `measure_failed` и строкой в логе.

МОДЕЛЬ СЧЁТА = МОДЕЛЬ ЭТАЛОНА, а при отсутствии эталона — модель клиента.
Основание — §2.0 спеки: токенизаторы sonnet-5 и haiku-4-5 расходятся на 7–12%,
то есть на величину порядка самого порога, и замер «правильной» моделью против
эталона, снятого другой, дал бы две трети порога из воздуха (у demo brain уже
работает на haiku, а все три эталона §9.3 сняты sonnet'ом). Дрейф — это
ОТНОШЕНИЕ, поэтому требование к модели счёта ровно одно: совпасть с той,
которой снят эталон. Когда эталона нет вовсе, сравнивать не с чем, и верный
токенизатор — тот, которым клиент реально платит: `cfg.settings.model`.

Сеть здесь только одна — `count_tokens` ($0, но всё-таки запрос), и она всегда
проходит через инъектируемый `counter`. SDK `anthropic` импортируется ЛЕНИВО
внутри функции: импорт этого модуля тянут за собой и раннер, и офлайн-прогон
онбординга, и ни тому, ни другому не должен понадобиться ни SDK, ни ключ.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

import yaml

log = logging.getLogger("chatter.core.prefix_budget")

__all__ = [
    "DEFAULT_BASELINES_PATH", "DEFAULT_THRESHOLD_PERCENT", "BRAIN_DRIFT",
    "PrefixBaselineError", "Baseline", "PrefixVerdict",
    "load_baselines", "count_tokens", "brain_drift_verdict",
    "check_client_prefixes",
]

# Файл эталонов лежит рядом с клиентами, в самом пакете: он часть ПРОДУКТА, а
# не рантайм-состояния. Правка эталона обязана приезжать коммитом и проходить
# ревью — именно это и делает эталон «записанным человеком».
DEFAULT_BASELINES_PATH: Path = Path(__file__).resolve().parents[1] / "prefix_baselines.yaml"

# Порог на случай, когда файл эталонов не прочитался ВОВСЕ и сказать, во
# сколько процентов мы верили, уже неоткуда. Живое число всегда берётся из
# файла: два числа на одну вещь расходятся молча, и меньшее гасит большее.
DEFAULT_THRESHOLD_PERCENT = 15

# Имя проверки в вердикте. Строкой, а не enum'ом: рядом сядет сторож порога
# кэша 4096 (§2.2), и оба поедут одним списком в один отчёт.
BRAIN_DRIFT = "brain_drift"

# ЗОНД — ЧАСТЬ КОНТРАКТА ЗАМЕРА, а не деталь реализации.
#
# `/v1/messages/count_tokens` требует непустой `messages`, а меряем мы СИСТЕМУ.
# Значит в каждое число входят накладные расходы служебного хода, и эталон с
# рантаймом обязаны звать API ОДНИМ И ТЕМ ЖЕ зондом: посчитанные разными
# зондами, они разойдутся ровно на эту добавку, и разойдутся молча. Эталоны
# §9.3 сняты именно этой формой (проверено живьём 19.08: volska 9131,
# yarina 14847, demo 2394). Менять зонд = сдвинуть ВСЕ эталоны разом.
_PROBE_MESSAGES = [{"role": "user", "content": "."}]


class PrefixBaselineError(Exception):
    """Файл эталонов сломан ЦЕЛИКОМ (не читается / не той формы).

    Про отдельного клиента говорит вердикт `no_baseline`, а не исключение:
    отсутствие строки у одного клиента не имеет права ронять проверку парка.
    А вот битый файл лечится только руками — и молчать о нём нельзя, иначе
    сторож выключится ровно так же тихо, как выключился бы кэш."""


@dataclass(frozen=True)
class Baseline:
    """Записанное ЧЕЛОВЕКОМ число, а не то, что померилось в прошлый раз."""

    slug: str
    brain_tokens: int
    measured_with: str          # модель, КОТОРОЙ снят эталон (см. §2.0)
    measured_on: str            # ISO-дата — по ней человек судит о свежести


@dataclass(frozen=True)
class PrefixVerdict:
    """Один вердикт сторожа. `ok` и `loud` РАЗНЫЕ поля, и это не дубль.

    «Проверка нашла превышение» и «владелец обязан это услышать» совпадают
    сегодня, но `no_baseline` — уже не-ok при полностью исправном клиенте, а
    `measure_failed` — не-ok при исправном ВСЁМ. Склеить их в один флаг значит
    выбирать между «молчать о непроверенном» и «кричать о законном».

    `fatal` всегда False (§9.2 «не отказ»); поле оставлено, потому что рядом
    сядет сторож порога кэша 4096, который отказ как раз обязан (§2.2), и оба
    поедут одним списком.
    """

    slug: str
    check: str                  # BRAIN_DRIFT
    ok: bool                    # True = в пределах эталона
    loud: bool                  # True = обязан быть слышен владельцу
    fatal: bool                 # ВСЕГДА False в этой ветке
    kind: str                   # within | drift | no_baseline | measure_failed
                                # | baselines_broken
    actual: int | None
    baseline: int | None
    threshold_percent: int
    message: str                # человеческий текст, уже с числами


def load_baselines(path=None) -> tuple[dict[str, Baseline], int]:
    """(эталоны по слагам, порог N%). Только ЧТЕНИЕ — см. запрет в шапке.

    Строгость здесь намеренная: любая недостача (нет порога, нет модели
    замера, нет даты) — это `PrefixBaselineError`, а не умолчание. Умолчание
    на месте пропавшего числа даёт сторожу, который отработал «успешно» на
    выдуманной величине, — то есть зелёную ширму.
    """
    p = Path(path) if path is not None else DEFAULT_BASELINES_PATH
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise PrefixBaselineError(f"{p} не читается: {exc}") from exc
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise PrefixBaselineError(f"{p} не парсится: {exc}") from exc
    if not isinstance(data, dict):
        raise PrefixBaselineError(
            f"{p}: ожидался словарь верхнего уровня, получено {type(data).__name__}")

    threshold = data.get("threshold_percent")
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
        raise PrefixBaselineError(
            f"{p}: 'threshold_percent' обязан быть целым > 0, получено {threshold!r}. "
            f"Умолчание здесь запрещено: порог, взятый из воздуха, — второе "
            f"число на ту же вещь, и молча победит меньшее")

    clients = data.get("clients")
    if not isinstance(clients, dict):
        raise PrefixBaselineError(
            f"{p}: 'clients' обязан быть словарём slug -> эталон, получено "
            f"{type(clients).__name__}")

    out: dict[str, Baseline] = {}
    for slug, row in clients.items():
        slug = str(slug)
        if not isinstance(row, dict):
            raise PrefixBaselineError(
                f"{p}: эталон клиента {slug!r} обязан быть словарём, получено "
                f"{type(row).__name__}")
        brain = row.get("brain")
        if not isinstance(brain, int) or isinstance(brain, bool) or brain <= 0:
            raise PrefixBaselineError(
                f"{p}: {slug}.brain обязан быть целым > 0, получено {brain!r}")
        measured_with = str(row.get("measured_with") or "").strip()
        if not measured_with:
            raise PrefixBaselineError(
                f"{p}: у {slug} не названа measured_with — модель, КОТОРОЙ снят "
                f"эталон. Без неё замер сделала бы другая модель, а токенизаторы "
                f"расходятся на 7–12% (§2.0) — это две трети порога из воздуха")
        measured_on = str(row.get("measured_on") or "").strip()
        if not measured_on:
            raise PrefixBaselineError(
                f"{p}: у {slug} не названа measured_on — дата замера. По ней "
                f"человек судит, эталон это или прошлогодняя память")
        out[slug] = Baseline(slug=slug, brain_tokens=brain,
                             measured_with=measured_with, measured_on=measured_on)
    return out, threshold


def count_tokens(model: str, system_text: str, *, client=None) -> int:
    """Сколько токенов весит `system_text` ПО УКАЗАННОЙ МОДЕЛИ.

    Зонд `_PROBE_MESSAGES` — часть контракта замера: эталон в
    `prefix_baselines.yaml` и этот рантайм обязаны звать API одним и тем же
    зондом, иначе сравнение поедет на величину накладных расходов служебного
    сообщения (см. комментарий у константы).

    Система идёт ПРОСТОЙ СТРОКОЙ, хотя рантайм шлёт её блоком с
    `cache_control` (см. AnthropicLLM.complete). Проверено живьём 19.08: обе
    формы дают одно и то же число (3021 на префиксе demo), поэтому усложнять
    вызов незачем — факт записан здесь, чтобы следующий не перепроверял.

    `messages.count_tokens` — $0, но это сетевой вызов, поэтому он всегда
    инъектируется в проверки параметром `counter`, а не зовётся ими напрямую.
    SDK импортируется здесь, внутри функции: модуль обязан импортироваться в
    офлайне (тесты, `--check` без ключа), где `anthropic` может отсутствовать.
    """
    if client is None:
        import anthropic          # лениво: импорт модуля не требует ни SDK, ни ключа
        client = anthropic.Anthropic()
    resp = client.messages.count_tokens(
        model=model,
        system=system_text,
        messages=_PROBE_MESSAGES,
    )
    return int(resp.input_tokens)


def brain_drift_verdict(cfg, *, baselines, threshold_percent,
                        counter=count_tokens, baselines_path=None) -> PrefixVerdict:
    """Вердикт по ОДНОМУ клиенту. НИКОГДА не бросает (§9.5).

    Предел — целочисленный пол `эталон * (100 + N) // 100`. Ровно предел это
    ещё `within`: порог назван «больше чем на N%», и клиент, попавший в него
    впритык, не должен будить владельца.

    Замер делается ВСЕГДА, в том числе когда эталона нет. Иначе вердикт
    `no_baseline` велел бы человеку вписать руками число, которое сторож
    отказался посчитать, — и на шестом клиенте строку не впишут вовсе, то есть
    клиент навсегда останется без сверки. Модель счёта при отсутствии эталона —
    `cfg.settings.model`: `measured_with` взять неоткуда, а у нового клиента
    модель brain и есть верный токенизатор.
    """
    # `build_system_prompt` тянем внутри функции, а не в шапке модуля: сторож
    # живёт в `core` рядом с brain, и модульный импорт замкнул бы их в кольцо
    # ровно тогда, когда brain захочет спросить у бюджета собственный размер.
    from chatter.core.brain import build_system_prompt

    slug = str(getattr(cfg, "slug", "") or "?")
    path = Path(baselines_path) if baselines_path is not None else DEFAULT_BASELINES_PATH
    bl = (baselines or {}).get(slug)

    try:
        model = bl.measured_with if bl is not None else str(cfg.settings.model)
        actual = int(counter(model, build_system_prompt(cfg)))
    except Exception as exc:                              # noqa: BLE001
        # DEV-18: сбой не глотаем. Он кричит вердиктом И оставляет трассировку
        # в логе — но старт клиента не роняет (§9.5): недоступная сеть не повод
        # оставить лида без ответа. Упавший замер — это `measure_failed` даже
        # тогда, когда эталона нет: «не с чем сравнить» и «нечего сравнивать»
        # разные беды, и лечатся они разными руками.
        log.warning("prefix-guard [%s]: замер префикса brain не выполнен", slug,
                    exc_info=True)
        known = (f"Эталон {bl.brain_tokens} токенов (модель {bl.measured_with}, "
                 f"снят {bl.measured_on}) остался непроверенным"
                 if bl is not None else
                 f"Эталона у этого клиента и так нет в {path}, а теперь нет и замера")
        return PrefixVerdict(
            slug=slug, check=BRAIN_DRIFT, ok=False, loud=True, fatal=False,
            kind="measure_failed", actual=None,
            baseline=bl.brain_tokens if bl is not None else None,
            threshold_percent=threshold_percent,
            message=(
                f"⚠️ [{slug}] замер префикса brain НЕ ВЫПОЛНЕН: "
                f"{type(exc).__name__}: {exc}. {known} — старт не роняем "
                f"(§9.5), но прямо сейчас дрейф не сторожит никто"))

    if bl is None:
        # Строка отдаётся ГОТОВОЙ К ВСТАВКЕ, с настоящими числами: человек
        # обязан её скопировать, а не сочинить. Требование вписать число,
        # которого никто не посчитал, — это способ не получить строку вовсе.
        today = _date.today().isoformat()
        return PrefixVerdict(
            slug=slug, check=BRAIN_DRIFT, ok=False, loud=True, fatal=False,
            kind="no_baseline", actual=actual, baseline=None,
            threshold_percent=threshold_percent,
            message=(
                f"⚠️ [{slug}] эталона префикса brain НЕТ в {path} — дрейф этого "
                f"клиента не сторожит НИКТО. Замер сейчас: {actual} токенов "
                f"(модель клиента {model}). Строку вносит ЧЕЛОВЕК, скопировав "
                f"её в раздел clients как есть: "
                f"{slug}: {{brain: {actual}, measured_with: {model}, "
                f"measured_on: \"{today}\"}} — сама она не появится (§9.2)"))

    limit = bl.brain_tokens * (100 + threshold_percent) // 100
    if actual <= limit:
        return PrefixVerdict(
            slug=slug, check=BRAIN_DRIFT, ok=True, loud=False, fatal=False,
            kind="within", actual=actual, baseline=bl.brain_tokens,
            threshold_percent=threshold_percent,
            message=(
                f"[{slug}] префикс brain {actual} токенов при эталоне "
                f"{bl.brain_tokens} (модель {bl.measured_with}, снят "
                f"{bl.measured_on}); предел {limit} = эталон +{threshold_percent}% "
                f"— в пределах"))

    # Процент с десятой долей, а рядом — превышение ПРЕДЕЛА в токенах. Целый
    # процент на границе печатал бы «+15% при пороге 15%», то есть строку,
    # по которой невозможно понять, почему сторож вообще заговорил.
    grown = (actual - bl.brain_tokens) * 100 / bl.brain_tokens
    return PrefixVerdict(
        slug=slug, check=BRAIN_DRIFT, ok=False, loud=True, fatal=False,
        kind="drift", actual=actual, baseline=bl.brain_tokens,
        threshold_percent=threshold_percent,
        message=(
            f"⚠️ [{slug}] префикс brain РАЗДУЛСЯ: {actual} токенов против "
            f"эталона {bl.brain_tokens} (модель {bl.measured_with}, снят "
            f"{bl.measured_on}) — это +{grown:.1f}% при пороге "
            f"{threshold_percent}%: предел {limit} превышен на {actual - limit}. "
            f"Цена вызова растёт вместе с префиксом, а полоса "
            f"тарифа съезжает. Либо срежь системный промпт, либо ПОДНИМИ эталон "
            f"руками в {path} — сам он не поднимется (§9.2)"))


def check_client_prefixes(cfg, *, counter=count_tokens,
                          baselines_path=None) -> list[PrefixVerdict]:
    """Все проверки префиксов ОДНОГО клиента. Список — потому что рядом сядет
    сторож порога кэша 4096 (§2.2): один замер, две проверки, один отчёт.

    Битый файл эталонов НЕ роняет вызывающего: он становится своим громким
    вердиктом `baselines_broken`. Разница между «сторож промолчал, потому что
    всё хорошо» и «сторож промолчал, потому что сломался» обязана быть видна
    снаружи — иначе поломка сторожа неотличима от тишины.
    """
    slug = str(getattr(cfg, "slug", "") or "?")
    path = Path(baselines_path) if baselines_path is not None else DEFAULT_BASELINES_PATH
    try:
        baselines, threshold = load_baselines(baselines_path)
    except PrefixBaselineError as exc:
        log.warning("prefix-guard [%s]: файл эталонов не прочитан: %s", slug, exc)
        # Отдельный `kind`, а не `measure_failed`: замер тут ни при чём, и
        # чинится это не сетью, а руками в файле. Слепить их в один вид значило
        # бы предложить владельцу «подождать, пока сеть вернётся», когда ждать
        # нечего.
        return [PrefixVerdict(
            slug=slug, check=BRAIN_DRIFT, ok=False, loud=True, fatal=False,
            kind="baselines_broken", actual=None, baseline=None,
            threshold_percent=DEFAULT_THRESHOLD_PERCENT,
            message=(
                f"⚠️ [{slug}] файл эталонов {path} не прочитан: {exc}. Пока он "
                f"битый, дрейф префикса brain не сторожит НИКТО ни у одного "
                f"клиента. Старт не роняем (§9.5) — чинить руками"))]
    return [brain_drift_verdict(
        cfg, baselines=baselines, threshold_percent=threshold, counter=counter,
        baselines_path=baselines_path)]
