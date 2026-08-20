# -*- coding: utf-8 -*-
"""T7 — пробы шагов подключения: ЧТЕНИЕ ФАКТОВ С ДИСКА И БОЛЬШЕ НИЧЕГО.

Спека: `docs/superpowers/specs/2026-08-20-t7-turnkey-connect.md`
(§1 модель состояния, §2 ворота, §3 карта шагов, §12 контракт, §12.6 схема
`facts`).

── КРАСНАЯ ЛИНИЯ (§12.5, §12.6 п.4) ────────────────────────────────────────
Ни одна функция этого модуля не пишет на диск НИ БАЙТА: ни файла, ни каталога,
ни строки журнала. Никаких `mkdir`, `write_text`, `os.replace`. Причина не в
аккуратности: состояние подключения обязано восстанавливаться из фактов, а
проба, оставляющая след, становится вторым представлением состояния — и молча
гасит первое ([[jarvis-two-numbers-for-one-thing]], §1 спеки и сторож Д7).

Прямого `subprocess` здесь тоже нет (§12.2): наружу — только через
`ctx.runner.run(argv, cwd=..., timeout=...)`. Это и делает сторожей
возможными без живых процессов.

── ПОЧЕМУ ВЕЗДЕ ТРИ ВЕРДИКТА, А НЕ ДВА ─────────────────────────────────────
`OPEN` — «факта нет, шаг предстоит», человеку печатается «что сделать».
`CONFLICT` — «факты спорят» ИЛИ «факт нечитаем». Второе не оговорка: DEV-18
запрещает тихий фолбэк, а «не смогли посмотреть» ≠ «посмотрели, чисто»
(тот же принцип, что у отказа замера радиуса в §2.3 и у `rc 2` автоприёмки).
Нечитаемый факт, свёрнутый в `OPEN`, печатал бы человеку «сделай то, что ты и
так сделал», и подключение ходило бы по кругу.

── `False` ЗНАЧИТ «ПРОВЕРИЛИ, НЕТ»; «НЕ ПРОВЕРЯЛИ» — ЭТО `None` (§12.12 п.2) ─
Ключ `facts`, которого проба на СВОЕЙ ветке не читала, равен `None`, а не
умолчанию типа. Разница не стилистическая, и цена её измерена: короткое
замыкание S12 на «ещё не alive» отдавало `isolated_token_line: False` при
СУЩЕСТВУЮЩЕЙ строке в логе — улика утверждала то, чего проба не проверяла, и
человек по ней шёл перешифровывать исправный токен. Поэтому умолчания в первой
строке пробы — `None`, а конкретное значение появляется РОВНО там, где факт
прочитан. Где `None` иначе слипся бы с законным «не нашлось», рядом стоит
отдельный ключ-признак (`heartbeat_present`, `log_present`, `slug_marker_checked`).

── ЧЕГО ПРОБЫ НЕ ДЕЛАЮТ ────────────────────────────────────────────────────
* не смотрят в журнал `state/connect/<slug>.md` — он для человека (§1, Д7);
* не смотрят на mtime session-файла: он двигается при каждой записи Telethon,
  и сторож на нём менял бы вердикт сам по себе (S5 и S8 спеки — прямой запрет);
* не зависят от `ctx.drill_yes` / `ctx.drill_again`: флаг — это разрешение
  ДЕЙСТВОВАТЬ, а не факт на диске. Проба, меняющая вердикт от флага, врёт про
  состояние. Деньги стережёт `__main__`/`actions` (§4, Д17);
* не подставляют `-Force` и не переводят `funnel_gate` в `true` — они вообще
  ничего не переводят (Д4, Д13, Д14).
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from chatter.config.loader import ConfigError, load_config
from chatter.connect.model import Ctx, StepResult, Verdict
from chatter.core.client_registry import (
    ClientEntry,
    RegistryError,
    parse_registry,
    validate,
)
from chatter.core.drill import DrillScenarioError, Scenario, parse_scenario
from chatter.payments.drill_gate import DRILL_CONTACTS
from chatter.registry_cli import session_available
from chatter.runtime_paths import chatter_beat_path

# ОТСТУПЛЕНИЕ ОТ §12.1, НАЗВАННОЕ ВСЛУХ. Контракт говорит «импорта
# chatter.onboard из chatter.connect нет», и обоснование там — РАЗНЫЕ ПРАВА:
# connect не должен уметь пересобирать конфиг. Отсюда берутся ровно две
# ЗАМОРОЖЕННЫЕ КОНСТАНТЫ и ни одной функции — прав они не дают. Альтернатива —
# написать "REVIEWED" и {"C12","C13","C16"} строками здесь, то есть завести
# второе определение той же вещи; первая же правка `checks.py` разъехалась бы с
# ним молча, а S2/S3 зеленели бы на несуществующей метке. Если интегратор
# решит, что буква §12.1 важнее, замена — две строки ровно в этом месте.
from chatter.onboard.checks import FLAG_IDS, REVIEWED_FILENAME

__all__ = [
    "probe_s0", "probe_s1", "probe_s2", "probe_s3", "probe_s4", "probe_s5",
    "probe_s6", "probe_s7", "probe_s8", "probe_s9", "probe_s10", "probe_s11",
    "probe_s12", "probe_s13", "probe_s14", "probe_s15",
]

# ─────────────────────────────────────────────────────────────────────────────
# Константы, которые НЕ выдуманы здесь
# ─────────────────────────────────────────────────────────────────────────────

# Среда логина/мозга (§3 S0). Имена — те же, что резолвит
# `chatter.telethon_login.load_api_credentials` и `_prefix_token_counter`.
ENV_REQUIRED: tuple[str, ...] = (
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "ANTHROPIC_API_KEY")

# Строка-доказательство доехавшего токена. Печатает её `chatter/telethon_run.py`
# при подъёме изолированного контрол-бота; именно она доказала перешифровку у
# Ярины 16.08 (§2.2).
ISOLATED_TOKEN_LINE = "control-bot poller starting (isolated token)"

# Порог свежести отметки живости. То же число, что у супервизора
# (`chatter_guardian_detached.ps1 -HeartbeatMaxAgeSec`, дефолт 180): «alive»
# считает он, и приёмка обязана мерить тем же метром, иначе два числа на одну
# вещь разъедутся и меньшее погасит большее молча.
HEARTBEAT_MAX_AGE_SEC = 180.0

# Строка маркера бандла (решение владельца q3, вариант «б»): её печатает сама
# команда экспорта, человек кладёт её в маркер.
BUNDLE_MARKER_LINE = "сессии в бандле"

# Состояния `.env.enc` — ЗАКРЫТЫЙ перечень (§12.7 п.6). Значения — те же, что
# возвращает `scripts/reencrypt_env.enc_status`; шаг закрывает только `in_sync`.
ENV_ENC_IN_SYNC = "in_sync"
ENV_ENC_UNREADABLE = "unreadable"
ENV_ENC_NO_ENV = "no_env"
ENV_ENC_STATES: frozenset[str] = frozenset(
    {ENV_ENC_NO_ENV, "no_enc", ENV_ENC_UNREADABLE, ENV_ENC_IN_SYNC, "stale"})

# Заголовок отчёта дрила (`scripts/drill_runner.format_report`) и три вердикта
# `chatter.core.drill.run_verdict`. Код возврата харнесса на диск не пишется
# вовсе — единственный его след — эта строка, поэтому связь именно такая.
_DRILL_TITLE_RE = re.compile(r"^#\s*Дрил:\s*(?P<name>.+?)\s*$")
_DRILL_VERDICTS: tuple[tuple[str, int], ...] = (
    ("ПРОГОН НЕ СОСТОЯЛСЯ", 2),
    ("ЕСТЬ КРАСНОЕ", 1),
    ("ПРОГОН ЗЕЛЁНЫЙ", 0),
)
_DRILL_RUNNING = "ПРОГОН ИДЁТ"

# Имена, под которыми дрил-сценарий лежит в каталоге клиента. Список тот же,
# что перебирает `chatter/onboard/checks._drill_scenarios`: если он разойдётся,
# C7 и S13 будут смотреть на разные файлы.
_SCENARIO_NAMES: tuple[str, ...] = (
    "drill.yaml", "drill_scenario.yaml", "{slug}-drill.yaml", "drill_{slug}.yaml")

# Таймауты внешних вызовов. Автоприёмка при наличии ANTHROPIC_API_KEY ходит в
# сеть за счётчиком токенов (C16/C17) — отсюда запас.
_TIMEOUT_CHECK = 600.0
_TIMEOUT_ENC = 180.0


# ─────────────────────────────────────────────────────────────────────────────
# Инструменты
# ─────────────────────────────────────────────────────────────────────────────

class _Unreadable(Exception):
    """Факт на диске ЕСТЬ, но прочитать его не удалось.

    Отдельный тип, потому что вердикт у этого случая другой: не `OPEN`
    («сделай»), а `CONFLICT` («разбирайся»). DEV-18 — исключение не глотаем,
    а превращаем в названный вслух вердикт.
    """


def _result(step_id: str, verdict: Verdict, why: str, todo: str,
            facts: dict) -> StepResult:
    """Единственная точка сборки `StepResult` — здесь же держится Д11.

    Пустое «что сделать» у не-CLOSED — не косметика: остановка без действия
    это тупик, из которого человек выходит чтением кода. Поэтому пустой `todo`
    ломается ГРОМКО (§12.6 п.5 ловит это как «контракт нарушен»), а не
    подменяется вежливой заглушкой.
    """
    if verdict is not Verdict.CLOSED and not (todo or "").strip():
        raise ValueError(
            f"{step_id}: вердикт {verdict.value} без «что сделать» — "
            f"нарушен контракт §12.2/Д11")
    return StepResult(step_id=step_id, verdict=verdict, why=why, todo=todo,
                      facts=facts)


def _closed(step_id: str, why: str, facts: dict) -> StepResult:
    return _result(step_id, Verdict.CLOSED, why, "", facts)


def _open(step_id: str, why: str, todo: str, facts: dict) -> StepResult:
    return _result(step_id, Verdict.OPEN, why, todo, facts)


def _conflict(step_id: str, why: str, todo: str, facts: dict) -> StepResult:
    return _result(step_id, Verdict.CONFLICT, why, todo, facts)


def _clients_dir(ctx: Ctx) -> Path:
    return Path(ctx.root) / "chatter" / "clients"


def _client_dir(ctx: Ctx) -> Path:
    return _clients_dir(ctx) / ctx.slug


def _registry_path(ctx: Ctx) -> Path:
    return _clients_dir(ctx) / "registry.yaml"


def _build_dir(ctx: Ctx) -> Path:
    return Path(ctx.root) / "build" / "onboard" / ctx.slug


def _state_dir(ctx: Ctx) -> Path:
    return Path(ctx.root) / "state"


def _python(ctx: Ctx) -> str:
    """Интерпретатор для внешних вызовов.

    Венв корня — первым: `python` из PATH это чужое окружение, из которого
    `chatter.*` не импортируется (грабля jarvis-wrong-python-on-path). Венва
    нет (песочница §9) — берём тот, которым запущены сами.
    """
    venv = Path(ctx.root) / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.is_file() else sys.executable


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _Unreadable(f"{path} не читается: {exc}") from exc


def _read_json(path: Path) -> dict:
    text = _read_text(path)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _Unreadable(f"{path} не разбирается как JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _Unreadable(
            f"{path}: ожидался словарь, получено {type(data).__name__}")
    return data


def _run(ctx: Ctx, argv: list[str], *, timeout: float):
    """Внешняя команда через `ctx.runner` (§12.2) с переводом беды в `_Unreadable`.

    Форма вызова одна и записана в §12.8 п.4:
    `ctx.runner.run(argv, cwd=..., timeout=...)`. Прежнее имя поля (`Ctx.run`)
    допускало два законных прочтения — «поле-функция» и «поле-объект», — и пара
    авторов разошлась на нём молча.

    §12.6 п.3: таймаут и ненайденный исполняемый — это ИСКЛЮЧЕНИЕ, а не
    `CommandResult` с выдуманным `rc`. Ловим здесь, чтобы каждая проба
    отличала «инструмент сказал нет» от «инструмент не отработал».
    """
    try:
        return ctx.runner.run(argv, cwd=Path(ctx.root), timeout=timeout)
    except Exception as exc:                      # noqa: BLE001 — DEV-18: не глотаем
        raise _Unreadable(
            f"команда {argv[0]} не отработала: {type(exc).__name__}: {exc}"
        ) from exc


def _default_entry(slug: str) -> ClientEntry:
    """Запись реестра «по умолчанию» — ТЕМ ЖЕ кодом, что и настоящая.

    Пути (`.secrets/<slug>.session`, `.secrets/<slug>.db`) выводит
    `parse_registry`, и повторять их здесь строкой значило бы завести второе
    определение: реестр однажды сменит правило, а проба продолжит смотреть на
    старый путь и молча зеленеть на чужой сессии.
    """
    text = "clients:\n  %s:\n    enabled: false\n" % json.dumps(slug)
    return parse_registry(text)[0]


def _registry(ctx: Ctx) -> tuple[tuple[ClientEntry, ...], str | None, bool]:
    """(записи, fatal, файла-нет). `fatal` — то же, что отдаёт `registry_cli`."""
    path = _registry_path(ctx)
    if not path.is_file():
        return (), f"registry not found: {path}", True
    try:
        text = _read_text(path)
    except _Unreadable as exc:
        return (), str(exc), False
    try:
        return parse_registry(text), None, False
    except RegistryError as exc:
        return (), str(exc), False


def _entry_of(entries: Iterable[ClientEntry], slug: str) -> ClientEntry | None:
    for e in entries:
        if e.slug == slug:
            return e
    return None


def _validated(ctx: Ctx, entries: tuple[ClientEntry, ...]
               ) -> tuple[set[str], dict[str, str]]:
    """`validate` реестра — ЕГО ЖЕ кодом (§2.1: «зовём её, а не повторяем»).

    Конфликт `session`/`db` среди включённых, отсутствие каталога клиента и
    недоступность сессии считает `chatter.core.client_registry`; своя копия
    этих правил разъехалась бы с супервизором, который ходит через
    `registry_cli`.
    """
    runnable, issues = validate(
        entries,
        root=str(ctx.root),
        session_available=lambda s: session_available(s, root=str(ctx.root)),
        client_dir_exists=lambda slug: (_clients_dir(ctx) / slug).is_dir(),
    )
    return {e.slug for e in runnable}, {i.slug: i.error for i in issues}


def _load_live_config(ctx: Ctx, slug: str | None = None):
    """Живой конфиг ТЕМ ЖЕ `load_config`, которым его грузит раннер.

    Своя чтение-yaml-и-посмотреть-ключи версия принимала бы конфиги, на
    которых раннер падает, — то есть закрывала бы шаг ровно перед тем, как
    гардиан уйдёт в цикл подъёма.
    """
    return load_config(_clients_dir(ctx), slug or ctx.slug)


def _mentions(text: str, token: str) -> bool:
    """Назван ли идентификатор ОТДЕЛЬНЫМ словом (C1 не считается за C12)."""
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![0-9])",
                     text) is not None


def _scenario_path(ctx: Ctx) -> Path | None:
    for name in _SCENARIO_NAMES:
        path = _client_dir(ctx) / name.format(slug=ctx.slug)
        if path.is_file():
            return path
    return None


def _scenario(ctx: Ctx) -> tuple[Path | None, Scenario | None]:
    path = _scenario_path(ctx)
    if path is None:
        return None, None
    try:
        return path, parse_scenario(_read_text(path))
    except DrillScenarioError as exc:
        raise _Unreadable(f"{path.name}: сценарий не разбирается — {exc}") from exc


# СМЕТУ ЗДЕСЬ НЕ СЧИТАЮТ. Ставки `COST_TURN_COLD/WARM` и правило «холодного
# хода» живут в `scripts/drill_runner.py`; копия того и другого была бы вторым
# определением одной вещи ([[jarvis-two-numbers-for-one-thing]]) и разошлась бы
# с харнессом молча — то есть ровно там, где цена печатается человеку ДО
# списания. Смету берёт `act_s13` из вывода харнесса в режиме плана (§12.9 п.5).


def _names_client(text: str, slug: str, drill_ids: Iterable[int]) -> bool:
    """Назван ли в ТЕЛЕ отчёта этот клиент — слагом или своим дрил-контактом.

    §12.7 п.2, найдено сторожем: имя файла отчёта — `<unix_ts>.md` и только
    оно, слага в пути нет вовсе. На машине уже лежат прогоны других клиентов, и
    проба, смотрящая «есть ли свежий файл», объявила бы дрил состоявшимся, не
    потратив ни цента и ничего не проверив — ложный зелёный на ЕДИНСТВЕННОМ
    шаге, который доказывает, что бот вообще отвечает. Каталог `<slug>/`
    отделяет, а доказывает ТЕЛО.
    """
    if _mentions(text, slug) or slug in text:
        return True
    return any(_mentions(text, str(i)) for i in drill_ids)


def _drill_runs(ctx: Ctx, drill_ids: Iterable[int]
                ) -> tuple[list[tuple[Path, str]], list[str]]:
    """(отчёты ЭТОГО клиента новыми вперёд, отвергнутые в СВОЁМ каталоге).

    Два места, и оба нужны: `state/drills/<slug>/` — куда пишет `act_s13`
    (§12.7 п.2), и плоский `state/drills/` — где лежат прогоны, снятые руками
    до этой арки. Принадлежность в ОБОИХ случаях доказывается телом файла, а не
    каталогом: каталог можно перепутать копированием, а слаг внутри — нет.

    Второй список — не диагностика ради диагностики. `format_report` харнесса
    печатает имя сценария и реплики, но НЕ слаг и НЕ контакт; если `act_s13` не
    позаботится, чтобы клиент был назван в теле, отчёт в собственном каталоге
    окажется «не нашим», проба вернёт «прогона не было», и платный дрил пойдёт
    по второму кругу. Молчать об этом нельзя: такой файл обязан стать видимым
    противоречием, а не поводом потратить деньги ещё раз.

    Имя сценария из заголовка идёт в `facts` подсказкой, но воротами не служит:
    `name` печатает сам харнесс, и человек вправе его переименовать.
    """
    ids = list(drill_ids)
    out: list[tuple[int, Path, str]] = []
    rejected: list[str] = []
    own = _state_dir(ctx) / "drills" / ctx.slug
    seen: set[Path] = set()
    for drills in (own, _state_dir(ctx) / "drills"):
        if not drills.is_dir():
            continue
        for path in sorted(drills.glob("*.md")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                text = _read_text(path)
            except _Unreadable as exc:
                # Нечитаемый отчёт не имеет права ослепить пробу целиком, но и
                # исчезнуть молча не должен: в своём каталоге он — улика.
                if drills == own:
                    rejected.append(f"{path.name}: {exc}")
                continue
            if not _names_client(text, ctx.slug, ids):
                if drills == own:
                    rejected.append(
                        f"{path.name}: в теле не назван ни «{ctx.slug}», ни его "
                        f"дрил-контакт")
                continue
            try:
                ts = int(path.stem)
            except ValueError:
                ts = 0
            out.append((ts, path, text))
    out.sort(key=lambda row: row[0], reverse=True)
    return [(path, text) for _, path, text in out], rejected


def _drill_rc(text: str) -> int | None:
    """Код возврата харнесса по его же заголовку. `None` — прогон не завершён."""
    if _DRILL_RUNNING in text:
        return None
    for marker, code in _DRILL_VERDICTS:
        if marker in text:
            return code
    return None


_CONSENT_DATE_RE = re.compile(r"^\s*(?P<raw>(\d{4})-(\d{2})-(\d{2}))(?!\d)")


def _parse_consent_date(text: str) -> tuple[date | None, str | None]:
    """Дата согласия: ПЕРВАЯ непустая строка НАЧИНАЕТСЯ с ISO `YYYY-MM-DD`.

    Формат зафиксирован §12.7 п.4, и узость здесь — предмет, а не строгость.
    Разбор «найди любое число, похожее на дату, где угодно в файле» находил бы
    год из фразы «працюємо з 2018» и объявлял согласие датированным. Дата
    разбирается, всё остальное в строке — человеческое «от кого получено», и
    его код не судит.
    """
    first = ""
    for line in text.splitlines():
        if line.strip():
            first = line
            break
    if not first:
        return None, None
    m = _CONSENT_DATE_RE.match(first)
    if not m:
        return None, None
    y, mo, d = int(m.group(2)), int(m.group(3)), int(m.group(4))
    try:
        return date(y, mo, d), m.group("raw")
    except ValueError:
        # «2026-13-32» — это не дата, и молча считать её отсутствующей нельзя:
        # строка ЕСТЬ, значит человек думал, что записал согласие.
        return None, m.group("raw")


def _drill_contact_ids(slug: str) -> list[int]:
    """Дрил-контакты этого клиента из канона `payments/drill_gate`.

    Суффикс — ПЕРСОНА (`<id>:<slug>`), и выдумывать адресат платного прогона
    нельзя: это реплики стенда живому человеку (та же причина, по которой C7
    сверяет контакт с двумя копиями канона).
    """
    ids: list[int] = []
    for contact in sorted(DRILL_CONTACTS):
        head, _, tail = contact.partition(":")
        if tail != slug:
            continue
        try:
            ids.append(int(head))
        except ValueError:
            continue
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Д8 — «этот слаг уже работает»
#
# Живёт ЗДЕСЬ, в пробе, а не над циклом — §12.12 п.1 в редакции 21.08. Отказ
# БЫЛ написан и стоял в `main()` перед вызовом конвейера, и сторож на порядок
# его не увидел вовсе: §12.3 объявляет цикл по `STEPS` ЕДИНСТВЕННЫМ порядком
# исполнения, поэтому всё, поставленное НАД циклом, для этого цикла попросту
# не существует. Замер повторился слово в слово: клиент `alive`, отметка
# свежая, воронка открыта — конвейер выполнил `act_s9` (переписал
# `settings.yaml` живого клиента) и дошёл до платного дрила в его боевой БД.
#
# Правило общее и стоит дороже этого случая: защита, которую нельзя выразить
# вердиктом шага, недостижима для всякого, кто идёт по контракту.
# ─────────────────────────────────────────────────────────────────────────────

def _beat_age(ctx: Ctx) -> float | None:
    """Возраст отметки живости раннера, в секундах. `None` — читать нечего.

    Отметка читается ОТДЕЛЬНО от `probe_s12`, и это не второе число на ту же
    вещь: у пробы и у Д8 разные вопросы. Проба доходит до отметки только на
    своей поздней ветке (§12.12 п.2), а Д8 обязан увидеть раннера, который
    пишет отметку ПРЯМО СЕЙЧАС, даже если супервизор ещё не отработал ни
    одного цикла, — то есть в самой опасной точке.

    Своих имён и порогов здесь нет: путь даёт `runtime_paths` (шесть мест уже
    собирали это имя догадкой, и цена догадки измерена), порог —
    `HEARTBEAT_MAX_AGE_SEC`. Разбор — первая строка, unix-секунды.
    """
    path = chatter_beat_path(ctx.slug, root=Path(ctx.root))
    if not path.is_file():
        return None
    try:
        head = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return float(ctx.now) - float(int(head[0].strip()))
    except (OSError, IndexError, TypeError, ValueError):
        # Нечитаемая отметка — это «признака нет», а не «клиент мёртв»:
        # утверждать по ней что-либо мы не имеем права (§12.12 п.2).
        return None


def _live_client_signs(ctx: Ctx) -> tuple[str, ...]:
    """Признаки того, что слаг УЖЕ развёрнут. Пустой кортеж — не развёрнут.

    Гейт здесь не спрашивается: его проверяет `probe_s0` раньше и отдельно,
    потому что у нормального подключения он закрыт, и на этом проверка
    заканчивается одним чтением конфига.

    Признак ЛЮБОЙ, а не один конкретный ключ. Причина механическая: `None` в
    `facts` значит «не проверяли» (§12.12 п.2), и условие вида
    `facts.get("state") == "alive"` читает «не проверяли» как «не живой» — то
    есть молча пропускает ровно тот случай, ради которого стоит. Сторож так и
    поймал: живость у него доказывалась не тем ключом, который спрашивали.

    Цена ошибки несимметрична, и это решает форму условия. Лишний отказ стоит
    одной строки человеку, который назовёт другой слаг. Пропущенный — правки
    в `settings.yaml` ЖИВОГО клиента и платного дрила в его боевой БД при
    открытой воронке.

    Свои копии условия «живой» тут не заводятся: признаки берутся у тех же
    проб, что закрывают шаги (S12 живость, S11 реестр). Разойдись копия с
    пробой — отказ срабатывал бы не тогда, когда шаг считает клиента живым.
    Пробы ничего не пишут и наружу не ходят (§12.5), поэтому это бесплатно.
    """
    signs: list[str] = []
    live = probe_s12(ctx)
    facts = dict(live.facts)
    if live.is_closed:
        signs.append("приёмка живости закрыта")
    if str(facts.get("state") or "").strip().lower() == "alive":
        signs.append("супервизор: alive")
    if facts.get("pid"):
        signs.append(f"pid {facts['pid']}")
    age = _beat_age(ctx)
    if age is not None and 0 <= age <= HEARTBEAT_MAX_AGE_SEC:
        signs.append(f"отметка живости {age:.0f} с назад")
    if dict(probe_s11(ctx).facts).get("enabled") is True:
        signs.append("в реестре enabled: true")
    return tuple(signs)


# ─────────────────────────────────────────────────────────────────────────────
# S0 — среда
# ─────────────────────────────────────────────────────────────────────────────

def probe_s0(ctx: Ctx) -> StepResult:
    """Ключи среды на месте и реестр парка ПАРСИТСЯ (§3 S0).

    Реестр здесь проверяется целиком, а не запись нашего клиента: сломанный
    `registry.yaml` — это `fatal` у ВСЕХ клиентов сразу, включая живых, и
    подключаться в такую среду нельзя.
    """
    # Д8 стоит ПЕРВЫМ и внутри цикла: S0 — первый шаг карты, поэтому дальше
    # него не уходит ни одна проба и тем более ни одно действие. Место выбрано
    # не «куда влезло»: «в этой среде клиент уже работает» — это факт СРЕДЫ,
    # ровно того же рода, что сломанный реестр парка ниже, и отказ у обоих
    # один — подключаться в такую среду нельзя.
    #
    # СНАЧАЛА гейт: у нормального подключения он закрыт, и проверка кончается
    # одним чтением конфига. Открытый гейт — обязательное условие отказа, а не
    # достаточное: живой раннер с ЗАКРЫТЫМ гейтом это штатная середина
    # подключения (S11 поднял, S13 ещё не гонялся), и отказ на нём сделал бы
    # команду неспособной довести до конца собственную работу.
    if dict(probe_s15(ctx).facts).get("funnel_gate") is True:
        signs = _live_client_signs(ctx)
        if signs:
            # Код 1 (ПРОТИВОРЕЧИЕ), а не 3: подключать уже подключённого
            # НЕЧЕГО, и это не ожидание человека, а спор просьбы с состоянием
            # мира (решение владельца q2).
            return _conflict(
                "S0",
                f"клиент «{ctx.slug}» уже подключён и работает: воронка "
                f"ОТКРЫТА, и клиент развёрнут ({'; '.join(signs)}) — бот "
                f"прямо сейчас отвечает лидам. Команда подключения правит "
                f"конфиг, пишет реестр и гоняет платный дрил в боевой БД: на "
                f"работающем клиенте это не подключение, а вмешательство в "
                f"живой разговор",
                "если этого клиента надо изменить — правь его точечно (пульт: "
                "/allow, /funnel_gate off); если это НЕ тот слаг — назови "
                "нужный",
                # Ключи среды — `None`: на этой ветке их никто не смотрел
                # (§12.12 п.2). Улика отказа названа отдельными ключами.
                {"env_missing": None, "registry_fatal": None,
                 "clients_known": None, "env_present_lengths": None,
                 "registry_path": str(_registry_path(ctx)),
                 "already_live": True, "live_signs": list(signs)})

    facts: dict = {"env_missing": [], "registry_fatal": None,
                   # «Проверяли, не живой» — это False, а не отсутствие ключа:
                   # молчание читается как «не смотрели» (§12.12 п.2).
                   "already_live": False, "live_signs": []}
    missing = [name for name in ENV_REQUIRED if not (ctx.env.get(name) or "").strip()]
    facts["env_missing"] = missing
    # Только ПРИЗНАК, никогда значение (§12.5).
    facts["env_present_lengths"] = {
        name: len((ctx.env.get(name) or "")) for name in ENV_REQUIRED}

    entries, fatal, absent = _registry(ctx)
    facts["registry_path"] = str(_registry_path(ctx))
    facts["registry_fatal"] = fatal
    facts["clients_known"] = [e.slug for e in entries]

    if fatal and not absent:
        # Файл есть и не читается/не той формы — это дефект среды, а не шаг,
        # который кто-то «ещё не сделал».
        return _conflict(
            "S0",
            f"реестр {_registry_path(ctx)} есть, но не разбирается: {fatal}. "
            f"Гардиан читает его каждые ~30 с и при таком файле пометит fatal "
            f"ВСЕХ клиентов, включая живых",
            "почини chatter/clients/registry.yaml (ключ 'clients', словарь "
            "slug -> настройки) и проверь `python -m chatter.registry_cli`",
            facts)
    if absent:
        return _open(
            "S0",
            f"реестра {_registry_path(ctx)} нет — подключать некуда: источник "
            f"истины подключения это он, а не active.yaml (§0-бис)",
            "создай chatter/clients/registry.yaml с ключом 'clients'",
            facts)
    if missing:
        return _open(
            "S0",
            f"в окружении нет значений: {', '.join(missing)} — без них не "
            f"состоится ни логин Telegram, ни замер префикса",
            f"положи {', '.join(missing)} через .\\scripts\\add_secret.ps1 и "
            f"перешифруй .\\scripts\\reencrypt_env.ps1",
            facts)
    return _closed(
        "S0",
        f"ключи среды на месте ({', '.join(ENV_REQUIRED)}), реестр парка "
        f"разобран: клиентов {len(entries)}",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S1 — конфиг собран T1–T6
# ─────────────────────────────────────────────────────────────────────────────

def probe_s1(ctx: Ctx) -> StepResult:
    """Каталог сборки и `report.json` (§3 S1).

    ПОЧЕМУ здесь НЕ смотрят на `rc` автоприёмки. `--check` даёт rc 0 только
    ВМЕСТЕ с меткой вычитки (решение владельца q3 от 17.08), то есть «rc 0»
    уже содержит в себе S2. Опираясь на него, шаг «конфиг собран» стал бы
    недостижим до вычитки и печатал бы человеку «сделай то, что ты и так
    собирался», вместо «читай отчёт».

    ЧЕСТНАЯ ГРАНИЦА. `report.json` не хранит вердиктов C1–C17 вовсе (см.
    `chatter/onboard/report.build_report`): в нём есть флаги, счётчики и
    разделы, но не «красное». Поэтому `red` здесь — это красное, которое
    ОТЧЁТ МОЖЕТ ДОКАЗАТЬ САМ: отчёт про другой слаг и файл, названный отчётом,
    но отсутствующий на диске. Красное автоприёмки живёт в S2, и подменять его
    здесь догадкой было бы третьим числом на ту же вещь.
    """
    build = _build_dir(ctx)
    report_path = build / "report.json"
    # `red`/`flags` считаются только после разбора отчёта: на ветках «каталога
    # нет» и «отчёт не читается» мы про них НИЧЕГО не знаем (§12.12 п.2).
    facts: dict = {"report_path": str(report_path), "red": None, "flags": None,
                   "build_dir": str(build)}

    if not build.is_dir():
        return _open(
            "S1",
            f"каталога сборки {build} нет — конфиг клиента ещё не собирали",
            f"собери конфиг: python -m chatter.onboard {ctx.slug} "
            f"--brief <файл.xlsx>",
            facts)
    if not report_path.is_file():
        return _open(
            "S1",
            f"в {build} нет report.json — сборка не доехала до отчёта, а без "
            f"отчёта вычитывать нечего",
            f"пересобери: python -m chatter.onboard {ctx.slug} "
            f"--brief <файл.xlsx>",
            facts)
    try:
        document = _read_json(report_path)
    except _Unreadable as exc:
        return _conflict(
            "S1",
            f"{exc} — отчёт есть, но прочитать его нельзя; «не смогли "
            f"посмотреть» не равно «чисто»",
            f"пересобери каталог сборки: python -m chatter.onboard {ctx.slug} "
            f"--brief <файл.xlsx>",
            facts)

    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
    report_slug = meta.get("slug")
    flag_rows = document.get("flags") or []
    flags = sorted({str(row.get("check")) for row in flag_rows
                    if isinstance(row, dict) and row.get("check")})
    facts["flags"] = flags
    facts["flags_checked"] = bool(meta.get("flags_checked"))
    facts["report_slug"] = report_slug

    red: list[str] = []
    if report_slug is not None and str(report_slug) != ctx.slug:
        red.append(f"slug_mismatch:{report_slug}")
    named_files = [str(x) for x in (meta.get("files") or [])]
    absent_files = [name for name in named_files if not (build / name).is_file()]
    red.extend(f"file_missing:{name}" for name in absent_files)
    facts["red"] = red
    facts["files"] = named_files

    if any(item.startswith("slug_mismatch:") for item in red):
        # DEV-36 (§10): «конфиг собран» и «конфиг собран ИМЕННО для этого
        # клиента» — разные утверждения, и путь их не различает.
        return _conflict(
            "S1",
            f"{report_path} собран для слага «{report_slug}», а подключаем "
            f"«{ctx.slug}» — каталог назван нашим именем, содержимое чужое",
            f"собери сборку своего клиента: python -m chatter.onboard "
            f"{ctx.slug} --brief <файл.xlsx> (или убери чужой каталог {build})",
            facts)
    if absent_files:
        return _conflict(
            "S1",
            f"отчёт называет файлы, которых в {build} нет: "
            f"{', '.join(absent_files)} — отчёт и каталог спорят",
            f"пересобери каталог: python -m chatter.onboard {ctx.slug} "
            f"--brief <файл.xlsx>",
            facts)
    return _closed(
        "S1",
        f"сборка на месте: {report_path} разбирается, файлов "
        f"{len(named_files)}, флагов в отчёте {len(flags)}"
        f"{' (' + ', '.join(flags) + ')' if flags else ''}",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S2 — вычитка тона и правдоподобия
# ─────────────────────────────────────────────────────────────────────────────

def probe_s2(ctx: Ctx) -> StepResult:
    """Метка вычитки в каталоге сборки И `rc 0` у автоприёмки (§3 S2).

    Метка — файл, который ставит ЧЕЛОВЕК; `rc 0` доказывает, что после неё
    автоприёмка зелёная. Оба факта нужны вместе: метка без зелёного `--check`
    — это подпись под невычитанным (ширма из §7 спеки T1–T6), а зелёный
    `--check` без метки невозможен по построению.

    `--check` ничего не пишет на диск (`chatter/onboard/__main__`, режим 2) —
    красная линия §12.5 не нарушена.
    """
    build = _build_dir(ctx)
    mark = build / REVIEWED_FILENAME
    facts: dict = {"mark_path": str(mark), "mark_present": mark.exists(),
                   "build_dir": str(build), "rc": None}

    if not build.is_dir():
        return _open(
            "S2",
            f"каталога сборки {build} нет — вычитывать нечего",
            f"собери конфиг: python -m chatter.onboard {ctx.slug} "
            f"--brief <файл.xlsx>, затем прочитай REPORT.md",
            facts)
    if not facts["mark_present"]:
        return _open(
            "S2",
            f"метки вычитки {REVIEWED_FILENAME} в {build} нет — отчёт, который "
            f"не читали, становится зелёной ширмой над дефолтами",
            f"прочитай {build / 'REPORT.md'} (разделы 2 и 3 обязательно) и "
            f"поставь метку {mark}, назвав в её тексте решения по флагам",
            facts)

    argv = [_python(ctx), "-m", "chatter.onboard", ctx.slug, "--check", str(build)]
    facts["argv"] = argv
    try:
        res = _run(ctx, argv, timeout=_TIMEOUT_CHECK)
    except _Unreadable as exc:
        return _conflict(
            "S2",
            f"автоприёмка не отработала: {exc}. Молчание инструмента не имеет "
            f"права читаться как «чисто»",
            f"прогони руками: {' '.join(argv)} и разбери, почему она не "
            f"запускается",
            facts)
    facts["rc"] = res.rc
    if res.rc == 0:
        return _closed(
            "S2",
            f"метка {REVIEWED_FILENAME} стоит и автоприёмка после неё зелёная "
            f"(rc 0)",
            facts)
    if res.rc == 2:
        return _conflict(
            "S2",
            f"автоприёмка НЕ СОСТОЯЛАСЬ (rc 2): часть проверок не выполнялась, "
            f"прогон ничего не доказал — это не «одно красное»",
            f"прогони {' '.join(argv)} и почини то, из-за чего проверки не "
            f"состоялись (обычно нет report.json или brief.json рядом)",
            facts)
    return _conflict(
        "S2",
        f"метка {REVIEWED_FILENAME} стоит, а автоприёмка красная (rc {res.rc}) "
        f"— подпись под вычиткой и вердикт проверок спорят",
        f"прогони {' '.join(argv)}, почини красное, при необходимости сними "
        f"метку и вычитай заново",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S3 — решения по флагам названы в метке
# ─────────────────────────────────────────────────────────────────────────────

def probe_s3(ctx: Ctx) -> StepResult:
    """Каждый поднятый отчётом флаг НАЗВАН в тексте метки (§3 S3).

    «Человек принял решения по флагам» — суждение, и проверить его нельзя. Но
    можно проверить МЕХАНИЧЕСКОЕ следствие: что в свободном тексте метки
    названы все id, которые поднял отчёт. Это не оценка решения, это
    доказательство, что решение принималось про КАЖДЫЙ флаг, а не про те, что
    попались на глаза.

    ТРЕБУЮТСЯ ВСЕ поднятые id, а не только `C12/C13/C16`. Спека называет
    множество флагов подмножеством `FLAG_IDS`, но это описание сегодняшних
    данных, а не фильтр: `checks._c7` возвращает `is_flag=True` руками, и C7
    штатно попадает в блок ФЛАГИ отчёта. Фильтровать по `FLAG_IDS` значило бы
    молча простить человеку ровно тот флаг, который говорит «сценарий нашли по
    совпадению имени» (DEV-36).
    """
    build = _build_dir(ctx)
    report_path = build / "report.json"
    mark = build / REVIEWED_FILENAME
    # Пустые списки означали бы «флагов не поднимали и называть нечего» — а на
    # ветках без отчёта и без читаемой метки это неизвестно (§12.12 п.2).
    facts: dict = {"flags_named": None, "flags_missing": None,
                   "report_path": str(report_path), "mark_path": str(mark)}

    if not report_path.is_file():
        return _open(
            "S3",
            f"{report_path} нет — список флагов брать неоткуда",
            f"собери конфиг: python -m chatter.onboard {ctx.slug} "
            f"--brief <файл.xlsx>",
            facts)
    try:
        document = _read_json(report_path)
    except _Unreadable as exc:
        return _conflict(
            "S3",
            f"{exc} — какие флаги поднимал отчёт, прочитать нельзя",
            f"пересобери каталог сборки: python -m chatter.onboard {ctx.slug} "
            f"--brief <файл.xlsx>",
            facts)

    meta = document.get("meta") if isinstance(document.get("meta"), dict) else {}
    rows = document.get("flags") or []
    raised = sorted({str(row.get("check")) for row in rows
                     if isinstance(row, dict) and row.get("check")})
    facts["flags_raised"] = raised
    facts["flags_known"] = sorted(FLAG_IDS)
    facts["flags_checked"] = bool(meta.get("flags_checked"))

    if not meta.get("flags_checked"):
        # `flags=[]` и `flags=None` — разные состояния (контракт report.py):
        # «проверок не было» нельзя читать как «флагов нет».
        return _open(
            "S3",
            f"{report_path} не знает, проверялись ли флаги "
            f"(meta.flags_checked=false) — пустой список тут значит «не "
            f"считали», а не «чисто»",
            f"пересобери каталог сборки целиком: python -m chatter.onboard "
            f"{ctx.slug} --brief <файл.xlsx>",
            facts)

    if not raised:
        facts["mark_present"] = mark.exists()
        # Проверено и пусто — это НЕ то же самое, что «не проверяли».
        facts["flags_named"] = []
        facts["flags_missing"] = []
        return _closed(
            "S3",
            "отчёт не поднял ни одного флага — называть в метке нечего",
            facts)

    if not mark.exists():
        # Метки нет, значит не назван ни один — это ПРОВЕРЕННОЕ утверждение.
        facts["flags_named"] = []
        facts["flags_missing"] = raised
        return _open(
            "S3",
            f"отчёт поднял флаги {', '.join(raised)}, а метки вычитки нет — "
            f"решения по ним нигде не записаны",
            f"поставь {mark} и назови в её тексте каждый флаг: "
            f"{', '.join(raised)}",
            facts)
    try:
        text = _read_text(mark)
    except _Unreadable as exc:
        return _conflict(
            "S3",
            f"{exc} — метка есть, а текст решений прочитать нельзя",
            f"перезапиши {mark} в UTF-8, назвав в ней флаги: "
            f"{', '.join(raised)}",
            facts)

    named = [cid for cid in raised if _mentions(text, cid)]
    missing = [cid for cid in raised if cid not in named]
    facts["flags_named"] = named
    facts["flags_missing"] = missing
    facts["mark_chars"] = len(text)

    if missing:
        return _open(
            "S3",
            f"в тексте метки не названы флаги {', '.join(missing)} — значит "
            f"решение принималось не про каждый, а про те, что попались на "
            f"глаза",
            f"допиши в {mark} решение по каждому из: {', '.join(missing)} "
            f"(достаточно назвать id и словами, что решено)",
            facts)
    return _closed(
        "S3",
        f"в метке названы все поднятые флаги: {', '.join(named)}",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S4 — конфиг перенесён в боевой каталог
# ─────────────────────────────────────────────────────────────────────────────

_SLUG_FROM_TOKEN_ENV = re.compile(r"^CHATTER_CONTROL_BOT_TOKEN_(?P<slug>[A-Z0-9_]+)$")
_SLUG_FROM_HEADER = re.compile(
    r"Кл[іи][єе]нт\s*[`'\"]?(?P<slug>[a-z0-9][a-z0-9_-]*)")


def _slug_in_config(ctx: Ctx, cfg) -> str | None:
    """Слаг, названный ВНУТРИ конфига, а не в имени каталога (§10, DEV-36).

    Два места, оба кладёт генератор T1–T6:
      * `control.control_bot_token_env` = `CHATTER_CONTROL_BOT_TOKEN_<SLUG>`;
      * шапка `settings.yaml`: «… Клієнт `<slug>`.».
    `Config.slug` для этого не годится: он равен тому, что мы САМИ передали в
    `load_config`, и совпал бы всегда — это проверка аргумента, а не файла.
    """
    env_name = (cfg.settings.control.control_bot_token_env or "").strip()
    m = _SLUG_FROM_TOKEN_ENV.match(env_name)
    if m:
        return m.group("slug").lower()
    try:
        head = _read_text(_client_dir(ctx) / "settings.yaml")
    except _Unreadable:
        return None
    m = _SLUG_FROM_HEADER.search(head)
    return m.group("slug") if m else None


def probe_s4(ctx: Ctx) -> StepResult:
    """Каталог клиента существует и грузится НАСТОЯЩИМ `load_config` (§3 S4).

    Вердиктов ровно три, и однозначны они ТОЛЬКО потому, что `act_s4` въезжает
    одним переименованием временного каталога (§12.8 п.2):

      * каталога нет        → OPEN, копирует действие;
      * есть и грузится     → CLOSED;
      * есть и НЕ грузится  → CONFLICT.

    Третья строка была бы несправедливой при пофайловом копировании: обрыв
    посреди него оставлял каталог, который не грузится, и следующий запуск
    объявлял противоречием то, что команда сама же и оставила, — починить себя
    она при этом не могла. Полукопии НАШЕЙ руки не существует, значит битый
    каталог — правка человека, и разбирать её человеку: там может лежать
    вычитанный тон, перезаписывать который нельзя ни при каких условиях.

    Расхождение с `build/` ПОСЛЕ переноса — норма, а не ворота: человек правит
    тон в живом конфиге, и ради этого §5.1 и существует. Поэтому разница с
    каталогом сборки печатается строкой отчёта (`facts`), а не краснеет.

    Загрузка — тем же кодом, что у раннера: «yaml разбирается» и «раннер
    поднимется» разные утверждения, а `enabled: true` при битом конфиге даёт
    гардиану бесконечный цикл подъёма (§2.1).
    """
    client_dir = _client_dir(ctx)
    # `loads: None` — «load_config не звали»; `False` появится только там, где
    # он отработал и отказал. `slug_marker_checked` разводит два разных None у
    # `slug_in_config`: «не искали» и «искали, маркера в конфиге нет».
    facts: dict = {"client_dir": str(client_dir), "loads": None,
                   "slug_in_config": None, "slug_marker_checked": None}

    if not client_dir.is_dir():
        return _open(
            "S4",
            f"каталога живого клиента {client_dir} нет — конфиг ещё не "
            f"перенесён из сборки",
            f"перенеси вычитанный каталог {_build_dir(ctx)} в {client_dir}",
            facts)
    try:
        cfg = _load_live_config(ctx)
    except ConfigError as exc:
        facts["loads"] = False
        return _conflict(
            "S4",
            f"каталог {client_dir} есть, но load_config его отвергает: {exc}. "
            f"Перенос идёт одним переименованием, полукопии от команды не "
            f"бывает — значит это правка человека; а включить такого клиента "
            f"значит отдать гардиану цикл «поднял — упал»",
            f"почини {client_dir / 'settings.yaml'} (и соседние файлы) так, "
            f"чтобы load_config его принял; НЕ пересобирай каталог поверх — "
            f"там может лежать вычитанный тон",
            facts)
    facts["loads"] = True

    in_config = _slug_in_config(ctx, cfg)
    facts["slug_in_config"] = in_config
    facts["slug_marker_checked"] = True
    facts["funnel_gate"] = bool(
        cfg.settings.telegram.funnel_gate if cfg.settings.telegram else False)

    # Разница со сборкой — СТРОКА ОТЧЁТА (§3 S4), не ворота.
    build = _build_dir(ctx)
    differs: list[str] = []
    if build.is_dir():
        for path in sorted(build.glob("*")):
            if not path.is_file():
                continue
            live = client_dir / path.name
            if not live.is_file():
                differs.append(f"{path.name}: только в сборке")
                continue
            try:
                if _read_text(live) != _read_text(path):
                    differs.append(f"{path.name}: правлен после переноса")
            except _Unreadable as exc:
                differs.append(f"{path.name}: не сверить ({exc})")
    facts["differs_from_build"] = differs

    if in_config is not None and in_config != ctx.slug:
        return _conflict(
            "S4",
            f"каталог называется «{ctx.slug}», а сам конфиг называет себя "
            f"«{in_config}» — перенесли чужую сборку под нашим именем (DEV-36)",
            f"перенеси в {client_dir} сборку своего клиента и проверь "
            f"control.control_bot_token_env в settings.yaml",
            facts)
    if in_config is None:
        # Не мисматч, а отсутствие опоры: сверять не с чем. Закрываем, но
        # говорим об этом вслух — потолок проверяемого назван, а не спрятан.
        return _closed(
            "S4",
            f"{client_dir} грузится настоящим load_config; слага внутри "
            f"конфига не нашлось (ни в control_bot_token_env, ни в шапке "
            f"settings.yaml), поэтому принадлежность доказана только путём. "
            f"Расхождений со сборкой: {len(differs)}",
            facts)
    return _closed(
        "S4",
        f"{client_dir} грузится настоящим load_config, слаг внутри конфига "
        f"«{in_config}» совпал. Расхождений со сборкой: {len(differs)}"
        f"{' — это НОРМА, тон правит человек' if differs else ''}",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S5 — согласие клиента на доступ к аккаунту
# ─────────────────────────────────────────────────────────────────────────────

def probe_s5(ctx: Ctx) -> StepResult:
    """Согласие: файл есть, непуст, дата разбирается и не в будущем (§3 S5).

    Бот читает ВСЮ личку аккаунта — мы просим доступ к личной переписке
    человека, и это фиксируется, а не подразумевается (решение владельца q6).

    ЖИВЁТ ВНЕ КАТАЛОГА КЛИЕНТА (`state/connect/<slug>.consent.md`, §12.8 п.3),
    рядом с маркером бандла. Прежнее место — внутри `chatter/clients/<slug>/` —
    было тупиком для того, кто всё делает ПРАВИЛЬНО: согласие пишется ДО логина,
    а каталог клиента появляется на S4, и человек, положивший согласие первым,
    создавал каталог с одним файлом. S4 видел «есть и не грузится» и объявлял
    ПРОТИВОРЕЧИЕ — подключение вставало намертво на верном порядке действий.

    Цена решения названа вслух: `state/` не в git, поэтому единственная улика
    доступа к чужой переписке держится регулярным бэкапом состояния
    (`JarvisStateBackup`), а не историей репозитория.

    Сверку «согласие датировано РАНЬШЕ логина» спека ОТВЕРГЛА: единственная
    опора для даты логина — mtime session-файла, а он двигается при каждой
    записи Telethon. Сторож на нём был бы то зелёным, то красным без связи с
    предметом. Порядок держится порядком шагов, а не сверкой времён.
    """
    path = _state_dir(ctx) / "connect" / f"{ctx.slug}.consent.md"
    facts: dict = {"consent_path": str(path), "consent_date": None}

    if not path.is_file():
        return _open(
            "S5",
            f"{path} нет — согласия на чтение личной переписки аккаунта не "
            f"зафиксировано, а бот читает её всю",
            f"получи согласие клиента и запиши в {path} одну строку: дату и "
            f"от кого получено",
            facts)
    try:
        text = _read_text(path)
    except _Unreadable as exc:
        return _conflict(
            "S5",
            f"{exc} — файл согласия есть, а прочитать его нельзя",
            f"перезапиши {path} в UTF-8: дата и от кого получено",
            facts)
    facts["chars"] = len(text.strip())
    if not text.strip():
        return _open(
            "S5",
            f"{path} пуст — пустой файл согласием не является",
            f"впиши в {path} дату и от кого получено согласие",
            facts)

    parsed, raw = _parse_consent_date(text)
    facts["consent_date"] = raw
    if parsed is None:
        return _open(
            "S5",
            f"первая строка {path} не начинается с ISO-даты ГГГГ-ММ-ДД"
            f"{f' (нашлось «{raw}», но это не дата)' if raw else ''} — «когда "
            f"получено» это половина факта",
            f"сделай первой строкой {path}: «ГГГГ-ММ-ДД — от кого получено "
            f"согласие»",
            facts)
    facts["consent_date"] = parsed.isoformat()
    today = datetime.fromtimestamp(ctx.now).date()
    facts["today"] = today.isoformat()
    if parsed > today:
        return _conflict(
            "S5",
            f"согласие в {path} датировано будущим ({parsed.isoformat()} > "
            f"{today.isoformat()}) — либо дата написана наугад, либо часы "
            f"машины врут; и то и другое обесценивает запись",
            f"поправь дату в {path} на ту, когда согласие действительно "
            f"получено",
            facts)
    return _closed(
        "S5",
        f"согласие зафиксировано: {path}, дата {parsed.isoformat()}, "
        f"текста {facts['chars']} символ(ов)",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S6 — логин Telegram
# ─────────────────────────────────────────────────────────────────────────────

def probe_s6(ctx: Ctx) -> StepResult:
    """Сессия ОТКРЫВАЕТСЯ (§3 S6).

    Проверка идёт `registry_cli.session_available`, то есть тем же контрактом,
    что и `telethon_run.build_session`: сначала `.enc`, иначе legacy plaintext.
    Наивное «файл по пути существует» здесь НЕВЕРНО — после cutover P1/P2
    plaintext `.session` на диске нет вовсе, и живой клиент был бы помечен
    несуществующим.
    """
    entries, fatal, absent = _registry(ctx)
    entry = _entry_of(entries, ctx.slug) if not fatal else None
    # Пути записи ещё нет (S10 впереди) — берём умолчание ТЕМ ЖЕ кодом,
    # которым его выведет реестр, когда запись появится.
    effective = entry or _default_entry(ctx.slug)
    facts: dict = {
        "session_path": effective.session,
        # Перезаписывается строкой ниже до любой ветки; None здесь — страховка
        # на будущее: ранний выход, добавленный сверху, не должен унаследовать
        # «проверили, сессии нет».
        "session_available": None,
        "from_registry": entry is not None,
        "registry_fatal": fatal if not absent else None,
    }
    available = session_available(effective.session, root=str(ctx.root))
    facts["session_available"] = bool(available)

    if not available:
        if entry is not None and entry.enabled:
            # §6: опасное состояние ровно одно — «в реестре enabled: true, а
            # сессии нет»: гардиан начнёт цикл подъёма.
            return _conflict(
                "S6",
                f"в реестре {ctx.slug} включён, а сессия «{effective.session}» "
                f"не открывается (ни .enc, ни plaintext) — гардиан будет "
                f"поднимать раннер, который умирает на старте",
                f"выключи клиента (.\\scripts\\chatter_client.ps1 -Slug "
                f"{ctx.slug} -Action stop) и войди в аккаунт: "
                f"python -m chatter.telethon_login",
                facts)
        return _open(
            "S6",
            f"сессии «{effective.session}» нет — раннер без неё уйдёт в "
            f"интерактивный запрос кода и повиснет",
            f"войди в аккаунт клиента: python -m chatter.telethon_login "
            f"(код из SMS и 2FA вводит человек)",
            facts)
    return _closed(
        "S6",
        f"сессия «{effective.session}» открывается тем же правилом, что у "
        f"build_session",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S7 — токен контрол-бота доехал до .env.enc
# ─────────────────────────────────────────────────────────────────────────────

def probe_s7(ctx: Ctx) -> StepResult:
    """Пер-клиентская env-переменная токена непуста и `.env.enc` `in_sync`.

    Три ворот в одном шаге, и у каждого своя цена (§2.1):
      * имя переменной ПЕР-КЛИЕНТСКОЕ — общий токен даёт двум раннерам 409 в
        `getUpdates`, и пульт отвечает через раз БЕЗ ошибок в логе;
      * имя не совпадает с включённым соседом — та же беда, только адресно;
      * `.env.enc` `in_sync` — `bootstrap_env` при наличии `.enc` читает ТОЛЬКО
        его, и токен, записанный в plaintext `.env`, до процесса не доедет
        (ловушка Ярины 16.08).

    Значение токена не попадает ни в `facts`, ни в `why`: доказываем ПРИЗНАКОМ
    (`token_len`), а не значением (§12.5).
    """
    # Ни одного умолчания: `token_len: 0` на ветке «имя не пер-клиентское»
    # утверждало бы, что переменную смотрели и она пуста, а `env_enc_state`
    # обязан жить в ЗАКРЫТОМ алфавите `reencrypt_env` — шестого значения у него
    # нет (§12.12 п.2 и п.3).
    facts: dict = {"env_name": None, "token_len": None, "env_enc_state": None}
    try:
        cfg = _load_live_config(ctx)
    except ConfigError as exc:
        return _conflict(
            "S7",
            f"живой конфиг {_client_dir(ctx)} не грузится ({exc}) — имя "
            f"env-переменной токена брать неоткуда",
            f"почини конфиг клиента в {_client_dir(ctx)} (шаг S4)",
            facts)

    env_name = (cfg.settings.control.control_bot_token_env or "").strip()
    facts["env_name"] = env_name
    if not env_name:
        return _open(
            "S7",
            "в settings.yaml не задан control.control_bot_token_env — имени "
            "переменной с токеном нет, контрол-бот не поднимется",
            f"впиши в {_client_dir(ctx) / 'settings.yaml'} "
            f"control.control_bot_token_env: "
            f"CHATTER_CONTROL_BOT_TOKEN_{ctx.slug.upper()}",
            facts)

    # ПЕР-КЛИЕНТСКОЕ имя: единственный механический признак — слаг внутри
    # имени. Проверяем ДО значения: общий токен с непустым значением выглядит
    # исправным ровно до второго раннера.
    slug_token = re.sub(r"[^A-Z0-9]", "_", ctx.slug.upper())
    facts["slug_token"] = slug_token
    if not _mentions(env_name.upper(), slug_token):
        return _conflict(
            "S7",
            f"имя переменной токена «{env_name}» не пер-клиентское (в нём нет "
            f"«{slug_token}») — общий токен даёт двум раннерам 409 в "
            f"getUpdates, и команды пульта ходят через раз без ошибок в логе",
            f"заведи отдельного бота у @BotFather и укажи в "
            f"{_client_dir(ctx) / 'settings.yaml'} "
            f"control.control_bot_token_env: "
            f"CHATTER_CONTROL_BOT_TOKEN_{ctx.slug.upper()}",
            facts)

    # Совпадение с ВКЛЮЧЁННЫМ соседом.
    entries, fatal, absent = _registry(ctx)
    clash: list[str] = []
    unreadable: list[str] = []
    if not fatal:
        for entry in entries:
            if entry.slug == ctx.slug or not entry.enabled:
                continue
            try:
                other = _load_live_config(ctx, entry.slug)
            except ConfigError as exc:
                unreadable.append(f"{entry.slug}: {exc}")
                continue
            other_name = (other.settings.control.control_bot_token_env or "").strip()
            if other_name and other_name.upper() == env_name.upper():
                clash.append(entry.slug)
    facts["token_env_clash"] = clash
    facts["neighbours_unreadable"] = unreadable
    if clash:
        return _conflict(
            "S7",
            f"переменная токена «{env_name}» уже используется включённым "
            f"клиентом: {', '.join(clash)} — два раннера в getUpdates на одном "
            f"токене, пульт отвечает через раз",
            f"заведи ОТДЕЛЬНОГО бота у @BotFather и своё имя переменной для "
            f"{ctx.slug}",
            facts)
    if unreadable:
        # «Не смогли посмотреть» ≠ «чисто»: уникальность имени не доказана.
        return _conflict(
            "S7",
            f"конфиги включённых соседей не читаются ({'; '.join(unreadable)}) "
            f"— уникальность имени переменной токена не доказана",
            "почини конфиги названных клиентов либо выключи их, затем повтори",
            facts)

    value = (ctx.env.get(env_name) or "")
    facts["token_len"] = len(value.strip())
    if not value.strip():
        return _open(
            "S7",
            f"{env_name} пуст — токена контрол-бота в окружении нет, пульт "
            f"клиента не поднимется",
            f"возьми токен у @BotFather, добавь через "
            f".\\scripts\\add_secret.ps1 (имя {env_name}), затем "
            f".\\scripts\\reencrypt_env.ps1",
            facts)

    argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(Path(ctx.root) / "scripts" / "reencrypt_env.ps1"),
            "-Root", str(ctx.root), "-Check"]
    facts["argv"] = argv
    try:
        res = _run(ctx, argv, timeout=_TIMEOUT_ENC)
    except _Unreadable as exc:
        return _conflict(
            "S7",
            f"состояние .env.enc не проверено: {exc}. Не смогли посмотреть — "
            f"не равно in_sync",
            f"прогони .\\scripts\\reencrypt_env.ps1 -Check руками и разбери "
            f"отказ",
            facts)
    m = re.search(r"статус до:\s*(\S+)", (res.stdout or "") + (res.stderr or ""))
    state = m.group(1) if m else None
    facts["env_enc_state"] = state
    facts["rc"] = res.rc
    if state not in ENV_ENC_STATES:
        # Перечень ЗАКРЫТ (§12.7 п.6). Значение вне алфавита — включая «строку
        # статуса вообще не нашли» — значит, что мы НЕ ПОНЯЛИ ответ инструмента.
        # Это ПРОТИВОРЕЧИЕ, а не «шаг предстоит» (§12.12 п.3): «не закрыт»
        # послал бы человека перешифровывать на основании нечитанного ответа.
        return _conflict(
            "S7",
            (f"в выводе reencrypt_env нет строки «статус до: …» (rc {res.rc}) — "
             f"ответ инструмента не понят"
             if state is None else
             f"состояние .env.enc «{state}» вне закрытого перечня "
             f"({', '.join(sorted(ENV_ENC_STATES))}), rc {res.rc} — ответ "
             f"инструмента не понят"),
            f"прогони .\\scripts\\reencrypt_env.ps1 -Check руками и посмотри, "
            f"что он печатает",
            facts)
    if state == ENV_ENC_UNREADABLE:
        return _conflict(
            "S7",
            ".env.enc не расшифровывается (unreadable): нет энтропии, чужая "
            "машина или порча файла. «Не смогли прочитать» не равно «пусто»",
            "восстанови .env.enc из бандла секретов на этой машине, затем "
            "повтори",
            facts)
    if state != ENV_ENC_IN_SYNC:
        # `stale` — рабочее состояние машины, но НЕ то, из которого токен
        # доедет: bootstrap_env при наличии .enc читает ТОЛЬКО его.
        return _open(
            "S7",
            f".env.enc в состоянии «{state}», а не in_sync: bootstrap_env при "
            f"наличии .enc читает ТОЛЬКО его, и токен, записанный в plaintext "
            f".env, до процесса не доедет (ловушка Ярины 16.08)",
            ".\\scripts\\reencrypt_env.ps1"
            + (" (сначала заведи .env через .\\scripts\\add_secret.ps1)"
               if state == ENV_ENC_NO_ENV else ""),
            facts)
    if res.rc != 0:
        return _conflict(
            "S7",
            f"инструмент говорит in_sync, а возвращает rc {res.rc} — вывод и "
            f"код возврата спорят",
            "прогони .\\scripts\\reencrypt_env.ps1 -Check руками и разбери "
            "расхождение",
            facts)
    return _closed(
        "S7",
        f"{env_name} непуста (длина {facts['token_len']}), .env.enc in_sync",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S8 — маркер пересъёма бандла секретов
# ─────────────────────────────────────────────────────────────────────────────

def probe_s8(ctx: Ctx) -> StepResult:
    """Маркер бандла существует, непуст, и в строке «сессии в бандле» назван слаг.

    Вариант (б) владельца (q3): строку печатает САМА команда экспорта, человек
    кладёт её в маркер. Вариант (а) — напоминание без проверки — через месяц
    становится фоном.

    ЧЕГО ПРОБА НЕ ДЕЛАЕТ: не сравнивает время маркера с временем session-файла.
    Соблазн «бандл снят ДО логина и не несёт новой сессии» понятен, но опора
    негодная — mtime сессии двигается при каждой записи Telethon, и после
    подъёма раннера (S12) такой сторож краснел бы на здоровом подключении.
    Сторож, который меняет вердикт сам по себе, — это фон, а не проверка (Д16).

    ОСТАТОЧНЫЙ РИСК НАЗВАН ВСЛУХ: маркер доказывает, что бандл снимали и что в
    нём была ЭТА сессия, но не доказывает ни свежести бандла, ни того, что
    носитель уехал с машины. Это потолок проверяемого; round-trip из §9
    мануала остаётся человеческим.
    """
    path = _state_dir(ctx) / "connect" / f"{ctx.slug}.bundle.txt"
    facts: dict = {"marker_path": str(path), "slug_named": None,
                   "marker_line_present": None}

    if not path.is_file():
        return _open(
            "S8",
            f"маркера {path} нет — не зафиксировано, что бандл секретов "
            f"пересняли после логина; в бэкапе остался бы мёртвый auth",
            f"пересними бандл (.jrvbak) и положи в {path} строку «"
            f"{BUNDLE_MARKER_LINE}: …», которую напечатала команда экспорта",
            facts)
    try:
        text = _read_text(path)
    except _Unreadable as exc:
        return _conflict(
            "S8",
            f"{exc} — маркер есть, а прочитать его нельзя",
            f"перезапиши {path} в UTF-8 строкой «{BUNDLE_MARKER_LINE}: …» из "
            f"вывода команды экспорта",
            facts)
    facts["chars"] = len(text.strip())
    if not text.strip():
        # Файл ПРОЧИТАН и пуст: строки нет и слаг не назван — оба утверждения
        # проверены, а не унаследованы от умолчания.
        facts["marker_line_present"] = False
        facts["slug_named"] = False
        return _open(
            "S8",
            f"маркер {path} пуст — пустой файл ничего не доказывает",
            f"положи в {path} строку «{BUNDLE_MARKER_LINE}: …» из вывода "
            f"команды экспорта",
            facts)

    lines = [ln for ln in text.splitlines()
             if BUNDLE_MARKER_LINE.casefold() in ln.casefold()]
    facts["marker_line_present"] = bool(lines)
    if not lines:
        facts["slug_named"] = False
        return _open(
            "S8",
            f"в {path} нет строки «{BUNDLE_MARKER_LINE}» — состав бандла не "
            f"назван, проверять нечего",
            f"скопируй в {path} строку «{BUNDLE_MARKER_LINE}: …» ровно так, "
            f"как её напечатала команда экспорта",
            facts)
    named = any(_mentions(ln, ctx.slug) or ctx.slug in ln for ln in lines)
    facts["slug_named"] = bool(named)
    if not named:
        return _open(
            "S8",
            f"в строке «{BUNDLE_MARKER_LINE}» маркера {path} слаг «{ctx.slug}» "
            f"не назван — бандл снимали, но нашей сессии в нём не было",
            f"пересними бандл ПОСЛЕ логина {ctx.slug} и обнови {path} новой "
            f"строкой «{BUNDLE_MARKER_LINE}: …»",
            facts)
    return _closed(
        "S8",
        f"маркер {path} называет сессию «{ctx.slug}» в строке "
        f"«{BUNDLE_MARKER_LINE}» (свежесть бандла и судьба носителя — вне "
        f"проверяемого, §9 мануала)",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S9 — allowlist
# ─────────────────────────────────────────────────────────────────────────────

def probe_s9(ctx: Ctx) -> StepResult:
    """Владелец и дрил-контакт в `telegram.allowlist` живого конфига (§3 S9).

    При `funnel_gate: false` allowlist — ЕДИНСТВЕННЫЙ источник допуска: без
    владельца пульт не ответит своему хозяину, без дрил-контакта платный
    прогон S13 упрётся в тишину.

    Дрил-контакт НЕ выдумывается: берётся из канона
    `payments/drill_gate.DRILL_CONTACTS` по суффиксу персоны (Д6). Сочинить
    адресат платного прогона — значит отправить реплики стенда живому человеку.
    """
    facts: dict = {"allowlist": None, "owner_present": None,
                   "drill_present": None}
    try:
        cfg = _load_live_config(ctx)
    except ConfigError as exc:
        return _conflict(
            "S9",
            f"живой конфиг {_client_dir(ctx)} не грузится ({exc}) — allowlist "
            f"читать неоткуда",
            f"почини конфиг клиента в {_client_dir(ctx)} (шаг S4)",
            facts)

    telegram = cfg.settings.telegram
    allowlist = [int(x) for x in (telegram.allowlist if telegram else ())]
    facts["allowlist"] = allowlist
    facts["funnel_gate"] = bool(telegram.funnel_gate) if telegram else False

    owner = cfg.settings.control.owner_chat_id
    facts["owner_id"] = owner
    if owner is None:
        # Кого искать в allowlist — неизвестно, значит `owner_present` не
        # проверен; и `drill_present` тоже: до него мы не дошли.
        return _open(
            "S9",
            "в settings.yaml не задан control.owner_chat_id — кого считать "
            "владельцем в allowlist, неизвестно",
            f"впиши в {_client_dir(ctx) / 'settings.yaml'} "
            f"control.owner_chat_id: <telegram id владельца>",
            facts)
    # Владельца проверяем СРАЗУ, до вопроса про дрил-контакт: иначе выход по
    # отсутствующему контакту унёс бы с собой и неотвеченный `owner_present`.
    facts["owner_present"] = int(owner) in allowlist

    drill_ids = _drill_contact_ids(ctx.slug)
    facts["drill_ids"] = drill_ids
    if not drill_ids:
        return _open(
            "S9",
            f"в payments/drill_gate.DRILL_CONTACTS нет контакта с суффиксом "
            f"«:{ctx.slug}» — дрил-контакт клиенту ещё не заводили, а без него "
            f"платный прогон S13 идти некуда",
            f"заведи дрил-контакт «<id>:{ctx.slug}» в ОБЕИХ копиях канона: "
            f"chatter/payments/drill_gate.py и scripts/drill_reset.py",
            facts)

    facts["drill_present"] = any(i in allowlist for i in drill_ids)
    facts["extra_ids"] = [i for i in allowlist
                          if i != int(owner) and i not in drill_ids]

    missing_names: list[str] = []
    if not facts["owner_present"]:
        missing_names.append(f"владелец {owner}")
    if not facts["drill_present"]:
        missing_names.append(
            "дрил-контакт " + "/".join(str(i) for i in drill_ids))
    if missing_names:
        return _open(
            "S9",
            f"в telegram.allowlist нет: {', '.join(missing_names)}. При "
            f"funnel_gate: false allowlist — единственный источник допуска, и "
            f"без этих id ни пульт, ни дрил не состоятся",
            f"впиши недостающие id в telegram.allowlist файла "
            f"{_client_dir(ctx) / 'settings.yaml'}",
            facts)
    return _closed(
        "S9",
        f"allowlist содержит владельца ({owner}) и дрил-контакт "
        f"({'/'.join(str(i) for i in drill_ids)}); посторонних id "
        f"{len(facts['extra_ids'])}",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S10 — запись в реестре
# ─────────────────────────────────────────────────────────────────────────────

def probe_s10(ctx: Ctx) -> StepResult:
    """Запись есть, `runnable` ПОСЧИТАН, `error=null` (§3 S10, §2.2).

    Тонкость, которую легко потерять: `validate` не проверяет ВЫКЛЮЧЕННЫХ
    клиентов вовсе — «выключенный клиент с несуществующей сессией это не
    ошибка». Значит на записи с `enabled: false` (а именно такой её пишет S10)
    ошибок не бывает НИКОГДА, и «error=null» доказывало бы ноль.

    Поэтому запись валидируется ВКЛЮЧЁННОЙ КОПИЕЙ: тем же `validate`, теми же
    правилами, но с `enabled=True` — то есть ровно тем вопросом, который
    возникнет через шаг: «а если поднять?». Ворота §2.1 (конфликт session/db с
    включённым соседом, каталог, сессия) обязаны стоять ДО включения, а не
    после, иначе о конфликте мы узнаем от гардиана, пометившего invalid.

    Вердикт НЕ зависит от значения `enabled` в файле: после S11 оно станет
    `true`, а повторный запуск обязан находить S10 закрытым (Д1, Д12).
    """
    # `error` — это ошибка ВАЛИДАЦИИ НАШЕЙ записи; поломка реестра целиком
    # живёт в отдельном ключе, иначе «запись клиента плоха» и «файл не
    # разбирается» слиплись бы в одну улику.
    facts: dict = {"entry_present": None, "runnable": None, "error": None,
                   "registry_fatal": None}
    entries, fatal, absent = _registry(ctx)
    facts["registry_path"] = str(_registry_path(ctx))
    if fatal:
        facts["registry_fatal"] = fatal
        if absent:
            return _open(
                "S10",
                f"реестра {_registry_path(ctx)} нет — записывать некуда",
                "создай chatter/clients/registry.yaml с ключом 'clients'",
                facts)
        return _conflict(
            "S10",
            f"реестр не разбирается: {fatal} — дописывать запись в такой файл "
            f"нельзя, гардиан читает его каждые ~30 с",
            "почини chatter/clients/registry.yaml и повтори",
            facts)

    entry = _entry_of(entries, ctx.slug)
    if entry is None:
        facts["entry_present"] = False
        return _open(
            "S10",
            f"в {_registry_path(ctx)} нет записи «{ctx.slug}» — подключить "
            f"клиента значит добавить её сюда (§0-бис: active.yaml не при чём)",
            f"добавь запись {ctx.slug} с enabled: false в "
            f"{_registry_path(ctx)}",
            facts)
    facts["entry_present"] = True
    facts["enabled"] = bool(entry.enabled)
    facts["personas"] = list(entry.personas)
    facts["session"] = entry.session
    facts["db"] = entry.db

    forced = tuple(dataclasses.replace(e, enabled=True) if e.slug == ctx.slug else e
                   for e in entries)
    runnable, errors = _validated(ctx, forced)
    error = errors.get(ctx.slug)
    facts["runnable"] = ctx.slug in runnable
    facts["error"] = error
    if error:
        return _conflict(
            "S10",
            f"запись «{ctx.slug}» в реестре есть, но подняться по ней нельзя: "
            f"{error}",
            f"устрани названную причину в {_registry_path(ctx)} (или в "
            f"каталоге/сессии клиента) и повтори",
            facts)
    return _closed(
        "S10",
        f"запись «{ctx.slug}» в реестре есть, посчитана как runnable, "
        f"error=null (enabled в файле: {str(entry.enabled).lower()})",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S11 — подъём
# ─────────────────────────────────────────────────────────────────────────────

def probe_s11(ctx: Ctx) -> StepResult:
    """`enabled: true` в реестре + замер радиуса прошёл (§3 S11).

    ЗАМЕР ЗДЕСЬ НЕ ПОВТОРЯЕТСЯ, И ЭТО НЕ ЭКОНОМИЯ. §2.3 требует мерить радиус
    НЕ своей копией, а `scripts/chatter_client.ps1 -Slug <slug> -Action start`,
    в который он вшит (17.08, требование владельца). Скрипт выставляет
    `enabled: true` ТОЛЬКО после чистого замера (`Assert-CatchupRadius` стоит
    до `Set-Enabled`), поэтому `enabled: true` и есть след прошедшего замера.

    Своя проба радиуса вдобавок ломала бы идемпотентность: у работающего
    клиента диалоги с последним словом лида появляются штатно, и S11 то
    закрывался бы, то нет, без всякой связи с подключением. Остаточный риск
    назван вслух: `enabled: true`, выставленный правкой файла руками, замера
    не проходил — это видно в журнале гардиана, но не отсюда.
    """
    facts: dict = {"enabled": None, "radius_ok": None, "entry_present": None,
                   "registry_fatal": None}
    entries, fatal, absent = _registry(ctx)
    if fatal:
        facts["registry_fatal"] = fatal
        return _conflict(
            "S11",
            f"реестр не читается ({fatal}) — состояние подъёма неизвестно",
            "почини chatter/clients/registry.yaml и повтори",
            facts)
    entry = _entry_of(entries, ctx.slug)
    if entry is None:
        facts["entry_present"] = False
        return _conflict(
            "S11",
            f"записи «{ctx.slug}» в реестре нет, хотя шаг S10 её требует — "
            f"поднимать нечего",
            f"добавь запись {ctx.slug} в {_registry_path(ctx)} и повтори",
            facts)

    facts["entry_present"] = True
    facts["enabled"] = bool(entry.enabled)
    # След замера: enabled:true выставляет chatter_client.ps1, и только после
    # чистого Assert-CatchupRadius.
    facts["radius_ok"] = bool(entry.enabled)

    runnable, errors = _validated(ctx, entries)
    error = errors.get(ctx.slug)
    facts["error"] = error
    facts["runnable"] = ctx.slug in runnable

    if entry.enabled and error:
        # §6: единственное опасное состояние — включён, а конфига/сессии нет.
        return _conflict(
            "S11",
            f"«{ctx.slug}» включён в реестре, но подняться не может: {error}. "
            f"Гардиан крутит цикл «поднял — упал» каждые ~30 с",
            f".\\scripts\\chatter_client.ps1 -Slug {ctx.slug} -Action stop, "
            f"устрани причину, затем подними заново",
            facts)
    if not entry.enabled:
        return _open(
            "S11",
            f"«{ctx.slug}» в реестре выключен — раннер не поднят и замер "
            f"catch-up радиуса не проходил",
            f"подними клиента: .\\scripts\\chatter_client.ps1 -Slug "
            f"{ctx.slug} -Action start (замер радиуса вшит в него; -Force не "
            f"подставлять — нулевой радиус у нового клиента получается сам, а "
            f"ненулевой надо прочитать глазами)",
            facts)
    return _closed(
        "S11",
        f"«{ctx.slug}» включён в реестре; enabled:true выставляет "
        f"chatter_client.ps1 только после чистого замера радиуса",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S12 — приёмка живости
# ─────────────────────────────────────────────────────────────────────────────

def probe_s12(ctx: Ctx) -> StepResult:
    """`alive` + свежий heartbeat + строка `isolated token` (§3 S12, Д10).

    Три факта, и ни один не заменяет другой:
      * `state == alive` в `state/chatter_clients.json`. **`starting` за
        `alive` не считается** — «поднимаю» и «поднялся» разные утверждения;
      * heartbeat свежий. Файл отметки переживает смерть процесса и ребут,
        поэтому протухшая отметка при `alive` — это спор фактов, а не ожидание;
      * строка `control-bot poller starting (isolated token)` в логе клиента:
        именно она доказала перешифровку у Ярины. Без неё раннер жив, а пульт
        владельца — нет.
    """
    state_path = _state_dir(ctx) / "chatter_clients.json"
    beat_path = chatter_beat_path(ctx.slug, root=Path(ctx.root))
    log_path = Path(ctx.root) / "logs" / f"chatter_{ctx.slug}.log"
    # ЗДЕСЬ И БЫЛ ДЕФЕКТ, найденный сторожем на порядок: `isolated_token_line`
    # со значением False уезжал во ВСЕ ранние выходы — «клиента ещё нет в
    # состоянии», «не alive», «отметка протухла», — где лог никто не открывал.
    # Улика утверждала то, чего проба не проверяла, и человек по ней шёл
    # перешифровывать исправный токен. Умолчания — None; `heartbeat_present` и
    # `log_present` разводят «не читали» и «файла нет» (§12.12 п.2).
    facts: dict = {
        "state": None, "heartbeat_age": None, "isolated_token_line": None,
        "heartbeat_present": None, "log_present": None,
        "state_path": str(state_path), "heartbeat_path": str(beat_path),
        "log_path": str(log_path),
    }

    if not state_path.is_file():
        return _open(
            "S12",
            f"наблюдаемого состояния {state_path} ещё нет — супервизор не "
            f"отработал ни одного цикла",
            "убедись, что таск JarvisChatterGuardian живёт, и подожди один "
            "его цикл (~30 с)",
            facts)
    try:
        document = _read_json(state_path)
    except _Unreadable as exc:
        return _conflict(
            "S12",
            f"{exc} — состояние парка нечитаемо, живость подтвердить нечем",
            "проверь супервизор JarvisChatterGuardian: он пишет этот файл "
            "атомарно, битый файл означает сбой записи",
            facts)

    facts["registry_fatal"] = document.get("fatal")
    facts["updated_ts"] = document.get("updated_ts")
    clients = document.get("clients")
    entry = clients.get(ctx.slug) if isinstance(clients, dict) else None
    if not isinstance(entry, dict):
        return _open(
            "S12",
            f"клиента «{ctx.slug}» нет в {state_path} — супервизор его ещё не "
            f"видел",
            f"подними клиента (.\\scripts\\chatter_client.ps1 -Slug "
            f"{ctx.slug} -Action start) и подожди один цикл супервизора",
            facts)

    # Своего слова для «поля state в записи нет» не выдумываем: шестое значение
    # в алфавите супервизора было бы ровно тем, за что §12.12 п.3 бракует
    # `unknown` у .env.enc. Нет поля — нет и факта.
    raw_state = entry.get("state")
    state = str(raw_state) if raw_state else None
    facts["state"] = state
    facts["desired"] = entry.get("desired")
    facts["pid"] = entry.get("pid")
    facts["consecutive_fail"] = entry.get("consecutive_fail")
    facts["last_error"] = entry.get("last_error")

    # Возраст отметки берём из САМОГО файла отметки, а не из `heartbeat_ts`
    # состояния: состояние — производная, и опираться на производную там, где
    # рядом лежит первоисточник, значит однажды поймать их расхождение молча.
    age: float | None = None
    facts["heartbeat_present"] = beat_path.is_file()
    if beat_path.is_file():
        try:
            raw = _read_text(beat_path).splitlines()
        except _Unreadable as exc:
            return _conflict(
                "S12",
                f"{exc} — отметка живости есть, а прочитать её нельзя",
                f"проверь раннер {ctx.slug}: отметку пишет он сам",
                facts)
        head = (raw[0].strip() if raw else "")
        try:
            age = float(ctx.now) - float(int(head))
        except (TypeError, ValueError):
            return _conflict(
                "S12",
                f"отметка живости {beat_path} не разбирается (первая строка "
                f"«{head[:32]}») — свежесть посчитать нечем",
                f"проверь раннер {ctx.slug}: он пишет в отметку unix-секунды",
                facts)
    facts["heartbeat_age"] = age

    if entry.get("last_error"):
        return _conflict(
            "S12",
            f"супервизор держит «{ctx.slug}» в состоянии "
            f"«{state or 'без поля state'}» с ошибкой: "
            f"{entry.get('last_error')}",
            "устрани названную супервизором причину и повтори",
            facts)
    if state != "alive":
        return _open(
            "S12",
            f"состояние «{ctx.slug}» — «{state or 'поля state в записи нет'}», "
            f"а не «alive»"
            + (" («starting» за «alive» не считается: «поднимаю» и «поднялся» "
               "разные утверждения)" if state == "starting" else ""),
            f"подожди цикл супервизора (~30 с) и посмотри "
            f".\\scripts\\chatter_client.ps1 -Slug {ctx.slug} -Action status; "
            f"если состояние не меняется — читай logs/chatter_{ctx.slug}.log",
            facts)
    if age is None:
        return _conflict(
            "S12",
            f"супервизор считает «{ctx.slug}» живым, а отметки {beat_path} "
            f"нет — факты спорят",
            f"читай logs/chatter_{ctx.slug}.log: раннер стартовал, но отметку "
            f"живости не пишет",
            facts)
    if age > HEARTBEAT_MAX_AGE_SEC:
        return _conflict(
            "S12",
            f"состояние «alive», а отметке живости {age:.0f} с при пороге "
            f"{HEARTBEAT_MAX_AGE_SEC:.0f} с — файл отметки переживает смерть "
            f"процесса, и протухшая отметка «живым» не делает",
            f"читай logs/chatter_{ctx.slug}.log и проверь, жив ли процесс "
            f"раннера",
            facts)

    facts["log_present"] = log_path.is_file()
    if not log_path.is_file():
        return _open(
            "S12",
            f"лога {log_path} нет — строку «{ISOLATED_TOKEN_LINE}» искать "
            f"негде, а без неё не доказано, что токен доехал до процесса",
            f"подожди старта раннера и проверь {log_path}",
            facts)
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _conflict(
            "S12",
            f"{log_path} не читается: {exc} — доказательство доехавшего токена "
            f"недоступно",
            f"проверь доступ к {log_path} и повтори",
            facts)
    facts["isolated_token_line"] = ISOLATED_TOKEN_LINE in log_text
    if not facts["isolated_token_line"]:
        return _open(
            "S12",
            f"в {log_path} нет строки «{ISOLATED_TOKEN_LINE}» — раннер жив, а "
            f"контрол-бот с ЕГО токеном не поднялся: токен до процесса не "
            f"доехал (ловушка Ярины 16.08)",
            f"проверь .\\scripts\\reencrypt_env.ps1 -Check и перезапусти "
            f"клиента: .\\scripts\\chatter_client.ps1 -Slug {ctx.slug} "
            f"-Action stop, затем -Action start",
            facts)
    return _closed(
        "S12",
        f"«{ctx.slug}» alive, отметке {age:.0f} с (порог "
        f"{HEARTBEAT_MAX_AGE_SEC:.0f} с), строка изолированного токена в логе "
        f"есть",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S13 — первый дрил-прогон
# ─────────────────────────────────────────────────────────────────────────────

def probe_s13(ctx: Ctx) -> StepResult:
    """Файл прогона в `state/drills` и код возврата харнесса (§3 S13).

    ЧУЖОЙ ПРОГОН ЗДЕСЬ НЕ СЧИТАЕТСЯ. Путь `state/drills/<ts>.md` слага не
    содержит, а на машине лежат прогоны других клиентов; проба, смотрящая «есть
    ли свежий отчёт», объявила бы дрил состоявшимся, ничего не проверив — и это
    ложный зелёный на единственном шаге, который доказывает, что бот отвечает.
    Поэтому отчёт засчитывается, только если в его ТЕЛЕ назван этот слаг или
    его дрил-контакт (§12.7 п.2); каталог `state/drills/<slug>/` отделяет, тело
    доказывает.

    ЛЮБОЙ существующий результат ЗАПРЕЩАЕТ автоматический повтор. `OPEN`
    возвращается ровно в одном случае — прогона не было вовсе; всё остальное
    (красный, не состоявшийся, прерванный на середине) — `CONFLICT`, потому
    что повтор здесь это ВТОРАЯ трата денег клиента и посторонний трафик в его
    БД (§2.3, Д3). Прерванный прогон опознаётся по файлу прогресса: харнесс
    пишет отчёт ПОСЛЕ КАЖДОГО ШАГА именно на этот случай (§6).

    Смету проба НЕ считает. Ставки и правило холодного хода живут в харнессе, и
    вторая их копия разошлась бы молча ровно там, где цена печатается человеку.
    `act_s13` зовёт харнесс в режиме плана (без `--yes`) и берёт смету из его
    вывода (§12.9 п.5) — остановка всё равно называет цену ДО списания, как
    требует решение владельца q4.

    Вердикт НЕ смотрит на `ctx.drill_yes`/`ctx.drill_again`: флаг — разрешение
    действовать, а не факт на диске.
    """
    facts: dict = {"run_path": None, "rc": None, "runs_found": None,
                   "rejected_in_own_dir": None, "drill_ids": None}
    try:
        scenario_path, scenario = _scenario(ctx)
    except _Unreadable as exc:
        return _conflict(
            "S13",
            f"{exc} — по такому сценарию прогон не запустить и чужой отчёт от "
            f"своего не отличить",
            f"почини дрил-сценарий в {_client_dir(ctx)} и повтори",
            facts)
    if scenario is None:
        return _open(
            "S13",
            f"в {_client_dir(ctx)} нет дрил-сценария "
            f"({', '.join(n.format(slug=ctx.slug) for n in _SCENARIO_NAMES)}) "
            f"— прогонять нечего",
            f"положи сценарий дрила в {_client_dir(ctx)} (заготовку собирает "
            f"python -m chatter.onboard {ctx.slug} --brief …)",
            facts)
    facts["scenario_path"] = str(scenario_path)
    facts["scenario_name"] = scenario.name
    facts["scenario_contact"] = scenario.contact
    facts["steps"] = len(scenario.steps)

    drill_ids = _drill_contact_ids(ctx.slug)
    facts["drill_ids"] = drill_ids
    runs, rejected = _drill_runs(ctx, drill_ids)
    facts["runs_found"] = len(runs)
    facts["rejected_in_own_dir"] = rejected
    facts["search_dirs"] = [str(_state_dir(ctx) / "drills" / ctx.slug),
                            str(_state_dir(ctx) / "drills")]
    if not runs and rejected:
        # Деньги уже потрачены, а доказать это нечем. Вернуть здесь OPEN
        # значило бы отправить автомат платить второй раз за то же самое.
        return _conflict(
            "S13",
            f"в {_state_dir(ctx) / 'drills' / ctx.slug} лежат отчёты, которые "
            f"не называют этого клиента: {'; '.join(rejected)}. Прогон был, а "
            f"чей он — файл не говорит",
            f"прочитай эти отчёты; если они про {ctx.slug}, добейся, чтобы "
            f"прогон называл клиента (слаг или контакт "
            f"{scenario.contact or '<id>:' + ctx.slug} в теле отчёта), иначе "
            f"убери чужие файлы из каталога клиента",
            facts)
    if not runs:
        return _open(
            "S13",
            f"в {_state_dir(ctx) / 'drills'} нет отчёта прогона, в теле "
            f"которого назван «{ctx.slug}» — дрил этого клиента ещё не гоняли "
            f"(чужие отчёты рядом не считаются)",
            f"разреши ОДИН платный прогон флагом --drill-yes (смету печатает "
            f"сам харнесс ДО старта; контакт {scenario.contact or '—'}, шагов "
            f"{len(scenario.steps)})",
            facts)

    run_path, run_text = runs[0]
    facts["run_path"] = str(run_path)
    rc = _drill_rc(run_text)
    facts["rc"] = rc
    head = (run_text.splitlines() or [""])[0]
    title = _DRILL_TITLE_RE.match(head)
    # Подсказка, а не ворота: `name` сценария человек вправе переименовать, и
    # краснеть на этом значило бы держать сторожа на буквах чужого текста.
    facts["title_matches_scenario"] = bool(
        title and title.group("name").strip() == scenario.name.strip())
    if rc == 0:
        return _closed(
            "S13",
            f"прогон состоялся и зелёный: {run_path} (код возврата харнесса 0)",
            facts)
    if rc is None:
        return _conflict(
            "S13",
            f"отчёт {run_path} не содержит вердикта — прогон был начат и не "
            f"завершён. Деньги за начатые ходы уже списаны, автоматический "
            f"повтор потратил бы их второй раз",
            f"прочитай {run_path}; если прогон мёртв — перезапусти явно, с "
            f"--drill-again (смету напечатает харнесс ДО старта)",
            facts)
    return _conflict(
        "S13",
        f"прогон состоялся и НЕ зелёный (код возврата {rc}): {run_path}. "
        f"{'Шаги пропущены — результат не годится для приёмки' if rc == 2 else 'Есть красные проверки'}",
        f"разбери {run_path}, почини найденное и повтори прогон явно, с "
        f"--drill-again (смету напечатает харнесс ДО старта)",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S14 — вычитка текста дрила глазами
# ─────────────────────────────────────────────────────────────────────────────

def probe_s14(ctx: Ctx) -> StepResult:
    """Прочитал ли человек реплики прогона. Факта на диске НЕТ — и не будет.

    `EXPECT_KEYS` харнесса — ЗАКРЫТЫЙ словарь: он умеет проверить, что ответ
    содержит цену из knowledge или что карточка доехала, но «звучит ли это как
    живой администратор» проверкой не выражается. Все проверяемые следствия уже
    вынуты в C1–C14 автоприёмки; остаток — суждение, и автомат здесь означал бы
    LLM-судью над выходом LLM, который согласится с собой (§5.1).

    Поэтому проба честно возвращает `OPEN` всегда: закрыть этот шаг может
    только человек, и притворяться, что диск об этом что-то знает, — хуже, чем
    признать потолок. Цикл §12.3 этим не заклинивает: хвостовые человеческие
    шаги S14 и S15 в него не входят вовсе (§12.8 п.1) — когда закрыты S0…S13,
    команда печатает оставшееся за человеком и выходит с кодом 0. Проба тут
    нужна ради текста остановки и улик в facts, а не ради вердикта.
    """
    facts: dict = {"run_path": None, "rc": None, "closable_by_probe": False,
                   "rejected_in_own_dir": None}
    try:
        scenario_path, scenario = _scenario(ctx)
    except _Unreadable:
        scenario_path, scenario = None, None
    if scenario is not None:
        facts["scenario_path"] = str(scenario_path)
    runs, rejected = _drill_runs(ctx, _drill_contact_ids(ctx.slug))
    facts["rejected_in_own_dir"] = rejected
    if runs:
        facts["run_path"] = str(runs[0][0])
        facts["rc"] = _drill_rc(runs[0][1])
    where = facts["run_path"] or str(_state_dir(ctx) / "drills")
    return _open(
        "S14",
        "правдоподобие реплик харнесс не судит: EXPECT_KEYS закрыт, а «звучит "
        "ли это как живой администратор» проверкой не выражается — закрыть шаг "
        "может только человек",
        f"прочитай реплики прогона в {where} глазами: не обещает ли бот "
        f"лишнего, веришь ли ему как клиент",
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S15 — открытие трафика
# ─────────────────────────────────────────────────────────────────────────────

def probe_s15(ctx: Ctx) -> StepResult:
    """`funnel_gate` — ВНЕ команды навсегда (§3 S15, §5.5, Д14).

    Проба только СМОТРИТ на тумблер. Ни она, ни любой другой код `connect` не
    переводит его в `true` ни при каком флаге: за открытием стоит catch-up по
    непрочитанному за сутки, и цена ошибки измеряется в живых лидах.
    """
    # `funnel_gate: False` на ветке «конфиг не грузится» читалось бы как
    # «проверили, трафик закрыт» — а мы файла не открывали.
    facts: dict = {"funnel_gate": None, "closable_by_probe": False}
    try:
        cfg = _load_live_config(ctx)
    except ConfigError as exc:
        return _conflict(
            "S15",
            f"живой конфиг {_client_dir(ctx)} не грузится ({exc}) — состояние "
            f"funnel_gate неизвестно",
            f"почини конфиг клиента в {_client_dir(ctx)} (шаг S4)",
            facts)
    telegram = cfg.settings.telegram
    facts["funnel_gate"] = bool(telegram.funnel_gate) if telegram else False
    facts["allowlist"] = [int(x) for x in (telegram.allowlist if telegram else ())]
    if facts["funnel_gate"]:
        return _closed(
            "S15",
            "funnel_gate уже открыт владельцем — трафик идёт",
            facts)
    return _open(
        "S15",
        "funnel_gate закрыт: бот отвечает только allowlist'у. Открытие — "
        "команда владельца и только его: за ней стоит catch-up по "
        "непрочитанному за сутки",
        "открой трафик сам, командой пульта: /funnel_gate on confirm",
        facts)
