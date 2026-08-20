# -*- coding: utf-8 -*-
"""Шесть автоматических шагов подключения: S4, S9, S10, S11, S12, S13.

Спека: `docs/superpowers/specs/2026-08-20-t7-turnkey-connect.md` — §2 (ворота и
их цена), §3 (карта шагов), §6 (обрыв), §12 (КОНТРАКТ). Шесть и только шесть:
инвариант «AUTO <=> act is not None» держит `steps.py`, а сам список
автоматических шагов записан в §3 литералом. Седьмое действие здесь означало
бы, что человеческий шаг закрывает себя сам — ровно то, что §5 запрещает по
конструкции.

── ЧЕГО ЗДЕСЬ НЕТ И НЕ БУДЕТ ────────────────────────────────────────────────
* **`subprocess`.** Наружу — только через `ctx.runner` (§12.2). Это не стиль:
  подменяемый раннер и есть граница между «сторож проверил механику ворот» и
  «сторож поднял живого клиента за деньги владельца».
* **Записи в `chatter/clients/active.yaml`** — ни на байт (§0-бис, Д13).
  Гардиан этот файл не читает вовсе; правка мёртвого канала во время
  подключения — дефект, а не недоделка.
* **`funnel_gate: true`** ни при каком флаге (§5.5, Д14). Открытие трафика
  вне команды навсегда: за ним стоит catch-up по непрочитанному за сутки.
* **Ключа принудительного подъёма** у `chatter_client.ps1` (того, что
  пропускает замер catch-up радиуса) — не подставляется никогда (§2.3, Д4).
  У нового клиента радиус нулевой по построению; ненулевой радиус — это то,
  что человек обязан прочитать глазами, а не то, что обходят флагом.
* **Секретов в `facts`, в `why`, в `todo`.** Доказываем ПРИЗНАКОМ (имя
  переменной, длина, «непусто»), а не значением (§12.5).
* **Чтения журнала `state/connect/<slug>.md`.** Журнал пишет только
  `__main__.py`, и читать его не имеет права никто (§1, §12.6 п.4).

── ПОЧЕМУ КАЖДАЯ ЗАПИСЬ АТОМАРНА ───────────────────────────────────────────
`registry.yaml` читает гардиан каждые ~30 с; половина файла означала бы `fatal`
у ВСЕХ клиентов разом, включая живых (§6). `settings.yaml` читает раннер при
каждом подъёме. Поэтому обе записи идут «временный файл рядом + `os.replace`»,
и обе проверяются РАЗБОРОМ до подмены: файл, который не читается парсером,
до боевого имени не доезжает вовсе.

── ЧТО ДЕЙСТВИЕ ВОЗВРАЩАЕТ ──────────────────────────────────────────────────
`StepResult` с ключами `facts` из §12.6 п.2 — теми же именами, что у пробы.
Действие НЕ выносит вердикт шага: его выносит ПОВТОРНАЯ проба (§12.3),
«команда отработала» и «факт появился» — разные утверждения. Возврат `CLOSED`
здесь означает ровно «действие исполнено без отказа», и если факт при этом не
появился, порядок исполнения увидит это сам.

Особый случай ровно один и он назван в §5.8: ворота денег перед дрилом.
`act_s13` без `ctx.drill_yes` возвращает `OPEN` с `waits_for_human=True` —
это ШТАТНАЯ остановка «жду тебя» (код 3), а не поломка (код 1). Поле нужно
потому, что §12.3 такой ветки не описывал вовсе, а §4 и §5.8 требуют
останавливаться перед тратой ВСЕГДА (§12.7 п.1).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

import yaml

from chatter.config.loader import ConfigError, load_config
from chatter.connect.model import (
    DRILL_COST_CEILING_USD, ConnectContractError, Ctx, StepResult, Verdict,
    script_path)
from chatter.core.client_registry import (
    RegistryError, normalize_path, parse_registry)
from chatter.payments.drill_gate import DRILL_CONTACTS
# `session_available` зеркалит контракт `telethon_run.build_session` (сначала
# `.enc`, потом legacy plaintext). Своя проверка «файл на месте» после cutover
# P1/P2 отвечала бы «сессии нет» на живой сессии.
from chatter.registry_cli import session_available

# ─────────────────────────────────────────────────────────────────────────────
# Константы. Все — литералы с названной причиной; ни одна не берётся из
# окружения: подключение, меняющее поведение от переменной среды, невоспроизводимо.
# ─────────────────────────────────────────────────────────────────────────────

#: Файлы конфига, без которых `load_config` не поднимется (см. его же порядок
#: чтения). Список ЛИТЕРАЛЬНЫЙ и не выводится обходом каталога сборки: копия
#: «всего, что нашлось» уносила бы в боевой каталог и `report.json`, и метку
#: вычитки, и заготовку дрила — артефакты сборки, которым место в `build/`.
CONFIG_REQUIRED: tuple[str, ...] = (
    "persona.md", "knowledge.md", "playbook.md", "settings.yaml")

#: Необязательные: `examples.yaml` генератор пишет не всегда, `requisites.yaml`
#: у клиента может отсутствовать вовсе (у Ярины payments выключены).
#:
#: `drill.yaml` едет вместе с конфигом НЕ для красоты: факт шага S13 — отчёт
#: прогона, а сценарий, по которому прогон судят, проба ищет в КАТАЛОГЕ
#: КЛИЕНТА (четыре канонических имени, `checks._drill_scenarios`). Оставь мы
#: заготовку в `build/`, дрил был бы оплачен, а шаг остался бы открытым: «нет
#: сценария» при живом сценарии рядом.
CONFIG_OPTIONAL: tuple[str, ...] = (
    "examples.yaml", "requisites.yaml", "drill.yaml")

#: Префикс пер-клиентского имени переменной токена. Здесь он нужен ровно для
#: одного: вынуть СЛАГ ИЗ КОНФИГА (`facts["slug_in_config"]`, §12.6 п.2).
#: DEV-36 открыт — совпадение имени клиента уже зеленило чужой файл, поэтому
#: сверяется слаг внутри файла, а не только путь, по которому файл нашёлся.
TOKEN_ENV_PREFIX = "CHATTER_CONTROL_BOT_TOKEN_"

#: Чем зовём PowerShell. `.ps1` без интерпретатора не исполняется, а `pwsh` в
#: парке не стоит: гардиан и все скрипты живут на Windows PowerShell 5.1.
POWERSHELL = "powershell.exe"

#: Потолки времени внешних вызовов. Раннер поднимает СУПЕРВИЗОР за ~30 с,
#: скрипт лишь правит желаемое состояние и меряет радиус — минуты хватает с
#: запасом даже на холодный старт замера.
S11_TIMEOUT_S = 300.0

#: Ожидание живости: цикл гардиана ~30 с плюс холодный старт раннера
#: (расшифровка сессии, коннект Telethon). Пять минут — это «десять циклов
#: гардиана», а не круглое число: механика, а не вкус.
S12_WAIT_TIMEOUT_S = 300.0
S12_POLL_INTERVAL_S = 5.0

#: Смета дрила: харнесс печатает её сам за секунды.
S13_ESTIMATE_TIMEOUT_S = 300.0

#: Живой дрил ждёт ЧЕЛОВЕКА у телефона: час на первую реплику плюс по 15 минут
#: на каждый следующий шаг (`drill_runner.FIRST_STEP_TIMEOUT_SEC` и
#: `STEP_TIMEOUT_SEC`). Три часа — потолок ПОВЕРХ его собственных таймаутов,
#: чтобы зависший подпроцесс не держал подключение молча и бесконечно.
S13_RUN_TIMEOUT_S = 3 * 3600.0

#: Сессия тестового лида и файл разрешённых получателей. Имена — те же, что
#: у `scripts/drill_lead.py` и `scripts/drill_nightly.py`: это ОДИН стенд, и
#: третьего имени у его файлов быть не должно.
LEAD_SESSION_REL = ".secrets/drill_lead.session"
LEAD_PEERS_REL = ".secrets/drill_lead_peers.txt"

_ESTIMATE_RE = re.compile(r"≈\s*\$\s*([0-9]+(?:\.[0-9]+)?)")


class _EditError(Exception):
    """Точечную правку текстового конфига сделать не удалось.

    Внутренняя: наружу уходит `CONFLICT` с этим же текстом в `why`. Молча
    подставить «какую-нибудь» форму записи нельзя (DEV-18) — это чужой файл,
    который человек правит руками.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Общее
# ─────────────────────────────────────────────────────────────────────────────

def _ok(step_id: str, why: str, facts: dict[str, Any]) -> StepResult:
    """Действие исполнено. Вердикт шага выносит ПОВТОРНАЯ проба (§12.3)."""
    return StepResult(step_id=step_id, verdict=Verdict.CLOSED, why=why,
                      todo="", facts=facts)


def _open(step_id: str, why: str, todo: str, facts: dict[str, Any],
          *, awaits_human: bool = False) -> StepResult:
    """Действие не состоялось: факта нет, и его появление зависит от человека.

    `waits_for_human` (§12.7 п.1) — единственный признак, по которому порядок
    §12.3 отличает штатное «жду тебя» (код 3) от «сломано» (код 1). Ставится
    ровно там, где §5 называет шаг человеческим по конструкции: разрешение на
    трату, согласие, секрет, вычитка. Второй копии признака в `facts` нет
    намеренно — два представления одного факта гасят друг друга молча.
    """
    return StepResult(step_id=step_id, verdict=Verdict.OPEN, why=why,
                      todo=todo, facts=dict(facts),
                      waits_for_human=awaits_human)


def _conflict(step_id: str, why: str, todo: str,
              facts: dict[str, Any]) -> StepResult:
    """Факты спорят друг с другом либо действие отказало. Это дефект, не ожидание."""
    return StepResult(step_id=step_id, verdict=Verdict.CONFLICT, why=why,
                      todo=todo, facts=facts)


def _client_dir(ctx: Ctx) -> Path:
    return Path(ctx.root) / "chatter" / "clients" / ctx.slug


def _build_dir(ctx: Ctx) -> Path:
    return Path(ctx.root) / "build" / "onboard" / ctx.slug


def _registry_path(ctx: Ctx) -> Path:
    return Path(ctx.root) / "chatter" / "clients" / "registry.yaml"


def _newline_of(text: str) -> str:
    """Перевод строки ИСХОДНОГО файла.

    Не педантизм: CRLF-файл, переписанный в LF, делает дерево грязным целиком и
    глушит гейты — грабля записана отдельно ([[jarvis-crlf-dirty-file-blocks-gates]]).
    Правка одной строки не имеет права переписать все остальные.
    """
    return "\r\n" if "\r\n" in text else "\n"


def _join(lines: list[str], original: str) -> str:
    nl = _newline_of(original)
    tail = nl if original.endswith(("\n", "\r")) else ""
    return nl.join(lines) + tail


def _is_top_level_key(line: str) -> bool:
    return (bool(line.strip()) and not line[0].isspace()
            and not line.lstrip().startswith("#"))


def _block_bounds(lines: list[str], block: str) -> tuple[int, int]:
    """Границы блока верхнего уровня `block:` — [начало, конец) по строкам.

    Приём и его причина взяты у `chatter/config/yaml_edit.py`: правим ТЕКСТОМ,
    потому что `settings.yaml` и `registry.yaml` — половина документации
    продукта. `yaml.safe_dump` выкинул бы комментарии и переупорядочил ключи, и
    клиент открыл бы файл с машинным дампом вместо инструкции.
    """
    head = re.compile(rf"^{re.escape(block)}\s*:\s*(#.*)?$")
    start = next((i for i, l in enumerate(lines) if head.match(l)), None)
    if start is None:
        raise _EditError(f"нет блока '{block}:' на верхнем уровне")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _is_top_level_key(lines[i]):
            end = i
            break
    return start, end


def _atomic_write_text(path: Path, text: str) -> None:
    """Запись «временный файл рядом + `os.replace`» (§6, §12.5, Д9).

    Рядом, а не в `%TEMP%`: `os.replace` атомарен только в пределах одного тома.
    `fsync` до подмены — чтобы обрыв питания не оставил под боевым именем файл с
    нулями внутри: читателю (гардиану, раннеру) такой файл неотличим от битого
    конфига, а цена — `fatal` всему парку разом.
    """
    tmp = path.with_name(f"{path.name}.connect-tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Не глотаем (DEV-18): наверх уходит исходная беда, а мусор рядом с
        # боевым файлом убираем, чтобы следующий запуск не спорил сам с собой.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _slug_in_config(settings_text: str) -> str | None:
    """Слаг, НАЗВАННЫЙ ВНУТРИ конфига (DEV-36, §10).

    Единственное место, где `settings.yaml` называет клиента, — имя его
    персональной переменной токена: `CHATTER_CONTROL_BOT_TOKEN_<SLUG>`. Оно
    обязано быть пер-клиентским (§2.1), поэтому годится как отпечаток. Путь,
    по которому файл нашёлся, отпечатком не является: C7 автоприёмки уже
    зеленела на чужом файле по совпадению имени.
    """
    try:
        raw = yaml.safe_load(settings_text)
    except yaml.YAMLError:
        return None
    if not isinstance(raw, dict):
        return None
    control = raw.get("control")
    if not isinstance(control, dict):
        return None
    name = str(control.get("control_bot_token_env") or "").strip()
    if not name.startswith(TOKEN_ENV_PREFIX):
        return None
    tail = name[len(TOKEN_ENV_PREFIX):].strip()
    return tail.lower() or None


def _loads(ctx: Ctx) -> bool:
    """Грузится ли боевой каталог НАСТОЯЩИМ `load_config` (§2.1).

    Своей копии проверки нет и быть не может: раннер грузится именно им, и
    «наша проверка сказала ок» не то же самое, что «клиент поднимется».
    """
    try:
        load_config(Path(ctx.root) / "chatter" / "clients", ctx.slug)
        return True
    except (ConfigError, OSError, yaml.YAMLError):
        # Разбор чужого файла — это состояние мира, а не поломка кода: наверх
        # уходит признак, а причину назовёт проба, которая для этого и стоит.
        return False


def _drill_ids(slug: str) -> list[int]:
    """Telegram-id дрил-контактов ЭТОГО клиента из канона `DRILL_CONTACTS`.

    Форма записи канона — `<peer_id>:<персона>`, и суффикс это ПЕРСОНА: один и
    тот же дрил-аккаунт под двумя персонами даёт разные `contact_id`. Берём
    только записи своего слага: чужой суффикс увёл бы прогон к другому клиенту.

    Произвольный id сюда не попадает никогда и ниоткуда (Д6) — ни из
    аргументов, ни из окружения, ни из уже лежащего в файле списка. Список
    дрил-контактов — КОНСТАНТА модуля, а не параметр вызова, и причина
    записана в самом `drill_gate`: вызыватель, передающий своё множество,
    открывает шлюз тестовым активам в живой диалог.
    """
    out: set[int] = set()
    for entry in DRILL_CONTACTS:
        peer, _, persona = str(entry).partition(":")
        if persona == slug and peer.strip().lstrip("-").isdigit():
            out.add(int(peer))
    return sorted(out)


def _run(ctx: Ctx, argv: list[str], *, timeout: float):
    """Внешний вызов через ЕДИНСТВЕННЫЙ путь наружу.

    Обёртка нужна ради §12.6 п.3: `CommandRunner` при таймауте и ненайденном
    исполняемом БРОСАЕТ, а не возвращает выдуманный `rc`. Здесь исключение
    превращается в текст, а решение (CONFLICT) принимает вызывающее действие:
    «не смогли выполнить» и «выполнили, и оно сказало нет» обязаны различаться —
    тот же принцип, что у отказа замера радиуса в §2.3.

    Форма вызова одна: `ctx.runner.run(argv, cwd=..., timeout=...)` (§12.8 п.4).
    Поле переименовали именно потому, что `ctx.run(...)` и `ctx.run.run(...)`
    читались одинаково законно, и пара авторов разошлась на нём молча;
    временной поддержки прежнего имени здесь нет — §12.11 п.6.

    `isinstance` против конкретного класса не спрашивается (§12.7 п.7):
    подставной раннер сторожа обязан работать по ФОРМЕ, а не по родству.
    """
    return ctx.runner.run(list(argv), cwd=Path(ctx.root), timeout=timeout)


def _tail(text: str, limit: int = 400) -> str:
    """Хвост вывода одной строкой: `why` по контракту — ОДНА строка (§12.2)."""
    flat = " ".join(str(text or "").split())
    return flat[-limit:] if len(flat) > limit else flat


# ─────────────────────────────────────────────────────────────────────────────
# S4 — перенос конфига в каталог клиента
# ─────────────────────────────────────────────────────────────────────────────

def act_s4(ctx: Ctx) -> StepResult:
    """`build/onboard/<slug>/` → `chatter/clients/<slug>/`, ТОЛЬКО если целевого нет.

    🔴 Главное правило шага — не «скопировать», а «не перезаписать». Живой
    каталог клиента правит ЧЕЛОВЕК: §5.1 существует ровно ради этого, и
    расхождение с `build/` после переноса — норма, а не ворота (§3). Перенос
    поверх вычитанного конфига стёр бы работу человека молча и необратимо, и
    узнали бы мы об этом от лида. Поэтому существующий каталог здесь не
    трогается НИ ПРИ КАКИХ УСЛОВИЯХ — ни при `--drill-again`, ни при пустом
    целевом каталоге, ни при «в build новее».

    Копия собирается во временном каталоге РЯДОМ и подменяется одним
    `os.replace`: половина конфига на боевом пути — это `load_config`,
    падающий у гардиана, а не у нас (§6).
    """
    src = _build_dir(ctx)
    dst = _client_dir(ctx)
    facts: dict[str, Any] = {
        "client_dir": str(dst), "loads": False, "slug_in_config": None,
        "build_dir": str(src),
    }

    if dst.exists():
        # Проба не закрыта, а каталог уже есть — значит спорят не мы с диском,
        # а факты между собой (каталог есть, но не грузится / не тот слаг).
        # Разбирается это человеком: копировать поверх нельзя.
        settings = dst / "settings.yaml"
        facts["loads"] = _loads(ctx)
        if settings.exists():
            facts["slug_in_config"] = _slug_in_config(
                settings.read_text(encoding="utf-8", errors="replace"))
        return _conflict(
            "S4",
            f"каталог {dst} уже существует, но шаг не закрыт: конфиг не грузится "
            f"или назван не тот слаг. Перенос поверх стёр бы вычитанный человеком "
            f"конфиг молча и необратимо",
            f"посмотри {dst} и {src} глазами и почини живой конфиг руками "
            f"(или убери каталог, если он собран не для этого клиента)",
            facts)

    if not src.is_dir():
        return _open(
            "S4",
            f"каталога сборки {src} нет: переносить нечего, а сочинить конфиг "
            f"клиента подключение не имеет права",
            f"собери конфиг: python -m chatter.onboard {ctx.slug} --brief <файл.xlsx>",
            facts, awaits_human=True)

    missing = [name for name in CONFIG_REQUIRED if not (src / name).is_file()]
    if missing:
        return _open(
            "S4",
            f"в {src} нет файлов {', '.join(missing)} — без них `load_config` не "
            f"поднимет клиента, а гардиан будет поднимать и ронять раннер по кругу",
            f"пересобери конфиг: python -m chatter.onboard {ctx.slug} --brief <файл.xlsx>",
            facts, awaits_human=True)

    names = list(CONFIG_REQUIRED) + [
        n for n in CONFIG_OPTIONAL if (src / n).is_file()]
    tmp = dst.with_name(f".{ctx.slug}.connect-tmp-{os.getpid()}")
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        for name in names:
            shutil.copy2(src / name, tmp / name)
        # Одним движением: половины каталога на боевом пути не существует ни в
        # один момент. Если `dst` успел появиться между проверкой и этой
        # строкой — `os.replace` откажет, и это правильно: чужой каталог мы не
        # перезаписываем даже в гонке.
        os.replace(tmp, dst)
    except OSError as exc:
        try:
            if tmp.exists():
                shutil.rmtree(tmp)
        except OSError:
            pass
        return _conflict(
            "S4",
            f"перенос конфига не состоялся: {exc}",
            f"посмотри права и наличие {dst.parent}, затем повтори",
            facts)

    settings_text = (dst / "settings.yaml").read_text(
        encoding="utf-8", errors="replace")
    facts["slug_in_config"] = _slug_in_config(settings_text)
    facts["loads"] = _loads(ctx)
    facts["copied"] = names
    return _ok("S4", f"конфиг перенесён в {dst}: {', '.join(names)}", facts)


# ─────────────────────────────────────────────────────────────────────────────
# S9 — allowlist: владелец и дрил-контакт
# ─────────────────────────────────────────────────────────────────────────────

def _set_allowlist(text: str, ids: Iterable[int]) -> str:
    """`telegram.allowlist` = ровно `ids`, комментарии файла на месте.

    Поддерживается поточная форма (`allowlist: [1, 2]`) — та, которую пишет
    генератор T1–T6. Блочная (`- 1` списком) не переписывается молча: файл
    правил человек, и угаданная форма записи — это тихая порча чужого текста.
    """
    lines = text.splitlines()
    start, end = _block_bounds(lines, "telegram")
    value = "[" + ", ".join(str(int(i)) for i in ids) + "]"
    key_re = re.compile(r"^(?P<indent>\s+)allowlist\s*:\s*(?P<val>.*)$")

    indent = "  "
    last_content = start
    target: int | None = None
    for i in range(start + 1, end):
        line = lines[i]
        if line.strip() and not line.lstrip().startswith("#"):
            indent = line[:len(line) - len(line.lstrip())]
            last_content = i
            if target is None and key_re.match(line):
                target = i

    if target is None:
        lines.insert(last_content + 1, f"{indent}allowlist: {value}")
        return _join(lines, text)

    m = key_re.match(lines[target])
    raw = m.group("val")
    if not raw.lstrip().startswith("["):
        raise _EditError(
            "telegram.allowlist записан не поточным списком — правь руками: "
            "угаданная форма записи портит текст, который писал человек")
    close = raw.find("]")
    if close < 0:
        raise _EditError(
            "telegram.allowlist растянут на несколько строк — правь руками")
    tail = raw[close + 1:]
    lines[target] = f"{m.group('indent')}allowlist: {value}{tail}"
    return _join(lines, text)


def act_s9(ctx: Ctx) -> StepResult:
    """allowlist живого конфига: владелец обязателен, дрил-контакт из канона.

    При `funnel_gate: false` allowlist — ЕДИНСТВЕННЫЙ источник допуска
    (`chatter/core/admission.py`): без записи бот молча игнорирует стенд, и
    выглядит это как «бот не отвечает», хотя причина в гейте.

    🔴 ДОБАВЛЯЕТСЯ ровно `{owner_chat_id} ∪ дрил-контакты этого слага` —
    множество, каждый элемент которого доказуем: владелец назван в самом
    конфиге (тот же источник, что у пробы, иначе действие и проба зациклились
    бы), дрил-контакт взят из константы `DRILL_CONTACTS`. Произвольный id не
    вписывается ниоткуда (Д6): ни из аргументов, ни из окружения — таких
    каналов у команды нет вовсе.

    Уже лежащие в файле id СОХРАНЯЮТСЯ и называются вслух (в `why` и в
    `facts["kept_ids"]`). Молча стереть чужую строку из allowlist значит
    выключить бота живому человеку, которому он вчера отвечал; в проекте это
    делает `/allow remove` руками владельца, а не перенос конфига. Проба на
    посторонние id тоже не краснеет — она их считает.

    Штатный способ добавить кого-то потом — `/allow <id>` в пульте, оверлей
    поверх файла; правка файла для этого не нужна.
    """
    path = _client_dir(ctx) / "settings.yaml"
    facts: dict[str, Any] = {
        "allowlist": [], "owner_present": False, "drill_present": False,
        "settings_path": str(path),
    }
    if not path.is_file():
        return _open(
            "S9",
            f"нет {path}: allowlist писать некуда, конфиг клиента ещё не на месте",
            "закрой S4 (перенос конфига) — он идёт раньше по карте §3",
            facts, awaits_human=True)

    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return _conflict(
            "S9", f"{path} не разбирается как YAML: {exc}",
            f"почини {path} руками — правку в неразбираемый файл мы не пишем",
            facts)
    if not isinstance(raw, dict):
        return _conflict(
            "S9", f"{path}: верхний уровень не словарь",
            f"почини {path} руками", facts)

    control = raw.get("control") if isinstance(raw.get("control"), dict) else {}
    owner_raw = control.get("owner_chat_id")
    try:
        owner_id = int(owner_raw)
    except (TypeError, ValueError):
        return _open(
            "S9",
            "в settings.yaml нет `control.owner_chat_id` — владельца в allowlist "
            "внести неоткуда, а без него пульт и карточки клиента идут в никуда",
            "впиши control.owner_chat_id (id владельца в контрол-боте клиента) "
            "в settings.yaml",
            facts, awaits_human=True)

    drill = _drill_ids(ctx.slug)
    if not drill:
        return _open(
            "S9",
            f"в DRILL_CONTACTS нет ни одной записи вида `<id>:{ctx.slug}` — "
            f"дрил-контакт этому клиенту ещё не заводили, а выдумать id стенда "
            f"нельзя: реплики прогона ушли бы живому человеку",
            "внеси дрил-контакт в ОБЕ копии канона "
            "(chatter/payments/drill_gate.py и scripts/drill_reset.py)",
            facts, awaits_human=True)

    tg_before = raw.get("telegram") if isinstance(raw.get("telegram"), dict) else {}
    gate_before = tg_before.get("funnel_gate")
    kept: list[int] = []
    for item in (tg_before.get("allowlist") or []):
        try:
            value = int(item)
        except (TypeError, ValueError):
            # Нечисловой элемент чужой рукой: `load_config` на нём и так
            # упадёт, а «починить» его догадкой мы не имеем права.
            return _conflict(
                "S9",
                f"в telegram.allowlist лежит нечисловой элемент {item!r} — "
                f"конфиг с таким списком не грузится вовсе",
                f"почини telegram.allowlist в {path} руками", facts)
        if value not in (owner_id, *drill):
            kept.append(value)
    ids = sorted({owner_id, *drill, *kept})

    try:
        new_text = _set_allowlist(text, ids)
    except _EditError as exc:
        return _conflict(
            "S9", f"правка allowlist не удалась: {exc}",
            f"впиши allowlist руками: telegram.allowlist: {ids}", facts)

    # Разбор ДО подмены: боевой конфиг не имеет права ни секунды пролежать в
    # виде, который `load_config` не читает.
    try:
        check = yaml.safe_load(new_text)
        tg = check["telegram"]
        written = [int(x) for x in tg["allowlist"]]
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        return _conflict(
            "S9",
            f"после правки settings.yaml не разбирается ({exc}) — на диск такое "
            f"не кладём",
            f"впиши allowlist руками: telegram.allowlist: {ids}", facts)
    if written != ids:
        return _conflict(
            "S9",
            f"после правки allowlist читается как {written}, ожидался {ids}",
            f"впиши allowlist руками: telegram.allowlist: {ids}", facts)
    # Д14 структурно: шаг про allowlist не имеет права коснуться гейта воронки.
    # Проверка стоит здесь, а не в сторожах, потому что цена измеряется в
    # живых лидах: `funnel_gate: true` + catch-up = веер ответов незнакомцам
    # прямо на старте.
    if tg.get("funnel_gate") != gate_before:
        return _conflict(
            "S9",
            "правка allowlist задела funnel_gate — это запрещено (§5.5, Д14): "
            "трафик открывает владелец отдельной командой",
            f"впиши allowlist руками: telegram.allowlist: {ids}", facts)

    _atomic_write_text(path, new_text)
    facts.update({
        "allowlist": ids,
        "owner_present": owner_id in ids,
        "drill_present": bool(set(drill) & set(ids)),
        "owner_chat_id": owner_id,
        "drill_ids": drill,
        "kept_ids": kept,
    })
    return _ok(
        "S9",
        f"allowlist в {path} = {ids}: владелец {owner_id} и дрил-контакт "
        f"{'/'.join(str(i) for i in drill)}"
        + (f"; сохранены посторонние id {kept} — их вписывали руками, и "
           f"стирать их переносом конфига нельзя" if kept else ""),
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S10 — запись в реестр, enabled: false
# ─────────────────────────────────────────────────────────────────────────────

def _entry_lines(slug: str, indent: str, inner: str) -> list[str]:
    return [
        f"{indent}# Запись создана `python -m chatter.connect {slug}` (T7, шаг S10).",
        f"{indent}# enabled: false ДО подъёма — включает его S11 через",
        f"{indent}# scripts\\chatter_client.ps1, и только после того, как конфиг и",
        f"{indent}# сессия уже на диске. Включить раньше = гардиан уходит в цикл",
        f"{indent}# подъёма без сессии (§6 спеки T7).",
        f"{indent}{slug}:",
        f"{inner}enabled: false",
        f"{inner}personas: [{slug}]",
        f"{inner}session: .secrets/{slug}.session",
        f"{inner}db: .secrets/{slug}.db",
    ]


def act_s10(ctx: Ctx) -> StepResult:
    """Клиент в `chatter/clients/registry.yaml` с `enabled: false`.

    Реестр — ИСТОЧНИК ИСТИНЫ подключения (§0-бис): гардиан зовёт раннер планом
    отсюда, а `active.yaml` не читает вовсе. Поэтому пишем сюда и только сюда.

    `enabled: false` — не осторожность, а порядок §6: единственное опасное
    состояние подключения это «в реестре включён, а конфига или сессии нет», и
    гардиан в нём уходит в цикл подъёма. Включает клиента S11, ПОСЛЕ конфига
    (S4) и логина (S6), и делает это чужими руками — скриптом, в который вшит
    замер радиуса.

    Запись атомарна (Д9): гардиан читает файл каждые ~30 с, и полуфайл дал бы
    `fatal` ВСЕМ клиентам разом, включая живых. Поэтому новый текст сначала
    разбирается `parse_registry` — тем же кодом, что читает гардиан, — и лишь
    потом подменяет боевой файл одним `os.replace`.
    """
    path = _registry_path(ctx)
    facts: dict[str, Any] = {
        "entry_present": False, "runnable": False, "error": None,
        "registry_path": str(path),
    }
    if not path.is_file():
        return _conflict(
            "S10",
            f"реестра {path} нет — писать некуда, а сочинять файл, который "
            f"гардиан читает каждые ~30 с, подключение не имеет права",
            f"восстанови {path} из git", facts)

    text = path.read_text(encoding="utf-8")
    try:
        entries = parse_registry(text)
    except RegistryError as exc:
        return _conflict(
            "S10",
            f"реестр не разбирается ({exc}) — правку в сломанный реестр не "
            f"пишем: гардиан уже видит его как fatal у всего парка",
            f"почини {path} руками", facts)

    if any(e.slug == ctx.slug for e in entries):
        # Запись есть, но проба шаг не закрыла: спорят факты (конфликт путей,
        # чужая сессия, `enabled: true` без конфига). Переписывать чужую
        # запись нельзя — её мог править человек.
        return _conflict(
            "S10",
            f"запись «{ctx.slug}» в реестре уже есть, но шаг не закрыт: её "
            f"содержимое спорит с фактами на диске",
            f"посмотри запись «{ctx.slug}» в {path} глазами и почини руками",
            {**facts, "entry_present": True})

    session = f".secrets/{ctx.slug}.session"
    db = f".secrets/{ctx.slug}.db"
    # Ворота §2.1: session/db не совпадают с другим ВКЛЮЧЁННЫМ клиентом. Два
    # процесса на одной Telethon-сессии = гонка за запись `.session` вплоть до
    # разлогина аккаунта. Сравнение — канонической `normalize_path` из того же
    # модуля, что и валидация: своя копия сравнения путей молча разошлась бы с
    # ней на первом же пине вида «volska живёт на сессии demo».
    root = str(ctx.root)
    for other in entries:
        if not other.enabled:
            continue
        for field, mine in (("session", session), ("db", db)):
            if normalize_path(getattr(other, field), root=root) == normalize_path(
                    mine, root=root):
                return _conflict(
                    "S10",
                    f"{field} {mine} уже занят ВКЛЮЧЁННЫМ клиентом «{other.slug}»: "
                    f"два раннера на одном файле — гонка за запись вплоть до "
                    f"разлогина аккаунта",
                    f"дай клиенту свои пути или выключи «{other.slug}» в {path}",
                    facts)

    lines = text.splitlines()
    try:
        start, end = _block_bounds(lines, "clients")
    except _EditError as exc:
        return _conflict(
            "S10", f"реестр не той формы: {exc}",
            f"впиши запись «{ctx.slug}» в {path} руками", facts)

    indent, inner = "  ", "    "
    for i in range(start + 1, end):
        m = re.match(r"^(\s+)[A-Za-z0-9_.\-]+\s*:\s*(#.*)?$", lines[i])
        if m:
            indent = m.group(1)
            inner = indent * 2
            break

    last = start
    for i in range(start + 1, end):
        if lines[i].strip():
            last = i
    block = _entry_lines(ctx.slug, indent, inner)
    new_lines = lines[:last + 1] + [""] + block + lines[last + 1:]
    new_text = _join(new_lines, text)

    # Разбор ТЕМ ЖЕ кодом, что читает гардиан, ДО подмены боевого файла.
    try:
        parsed = parse_registry(new_text)
    except RegistryError as exc:
        return _conflict(
            "S10",
            f"собранная запись ломает реестр ({exc}) — на диск такое не кладём",
            f"впиши запись «{ctx.slug}» в {path} руками", facts)
    mine = next((e for e in parsed if e.slug == ctx.slug), None)
    if mine is None or mine.enabled or mine.session != session or mine.db != db:
        return _conflict(
            "S10",
            f"после правки запись «{ctx.slug}» читается не так, как писалась "
            f"({mine!r}) — на диск такое не кладём",
            f"впиши запись «{ctx.slug}» в {path} руками", facts)

    _atomic_write_text(path, new_text)
    facts.update({"entry_present": True, "session": session, "db": db,
                  "enabled": False})
    return _ok("S10",
               f"клиент «{ctx.slug}» записан в {path} с enabled: false "
               f"(session {session}, db {db})", facts)


# ─────────────────────────────────────────────────────────────────────────────
# S11 — подъём раннера
# ─────────────────────────────────────────────────────────────────────────────

def act_s11(ctx: Ctx) -> StepResult:
    """`scripts/chatter_client.ps1 -Slug <slug> -Action start`.

    Своей копии подъёма здесь нет НАМЕРЕННО (§2.3, требование владельца 17.08):
    в этот скрипт вшит замер catch-up радиуса, и вторая ручка, поднимающая
    клиента мимо него, обесценила бы замер целиком. Отказ замера = остановка;
    ключ принудительного подъёма (тот, что замер пропускает) подключение не
    подставляет НИКОГДА (Д4): у нового клиента радиус нулевой по построению, а
    если он вдруг не нулевой — это и есть то, что надо прочитать глазами.

    `-Root` передаётся явно: у скрипта дефолт `C:\\jarvis`, и репетиция §9.1 в
    отдельном корне без этого ключа правила бы ЖИВОЙ реестр.
    """
    # Скрипт — КОД, и берётся он от дерева модуля, а не от `ctx.root`
    # (см. `model.script_path`). Слаг и корень данных уезжают аргументами.
    script = script_path("chatter_client.ps1")
    # `radius_ok=False` — стартовое значение ВСЕХ ветвей отказа, и это не
    # экономия на `None`: с диска «замер сказал нет» и «замер не состоялся»
    # выглядят одинаково (клиент так и не включён), а причину знает только
    # действие — оно и говорит её в `why`. Пробе остаётся факт: чистого замера
    # не было (§12.9 п.4, замечание сторожа порядка).
    facts: dict[str, Any] = {
        "enabled": False, "radius_ok": False, "script": str(script)}
    if not script.is_file():
        return _conflict(
            "S11",
            f"нет {script}: поднимать клиента больше нечем, а свой подъём мимо "
            f"скрипта пропустил бы замер catch-up радиуса",
            f"восстанови {script} из git", facts)

    # ── СВОИ предусловия §2.1, а не «до нас проверили» ───────────────────────
    #
    # 🔴 Найдено сторожем прогоном ВНЕ ОЧЕРЕДИ: `act_s11` без сессии и с битым
    # конфигом выставлял `enabled: true`. В штатном порядке сюда не дойти —
    # S4 и S6 стоят раньше, — но «порядок вызовет меня правильно» это не
    # предусловие, а надежда: ровно она и есть мишень мутационного гейта
    # «снятая проверка порядка».
    #
    # Цена названа в §2.1 дословно: `enabled: true` при битом конфиге или без
    # сессии = гардиан поднимает и роняет раннер ПО КРУГУ каждые ~30 с, а
    # Telethon без сессии уходит в интерактивный запрос кода и висит вечно.
    # Проверяем ТЕМ ЖЕ кодом, которым это увидит гардиан: `load_config` и
    # `session_available` (правило `.enc` из `secret_loader`), а не своей
    # копией правил.
    try:
        entry = next((e for e in parse_registry(
            _registry_path(ctx).read_text(encoding="utf-8"))
            if e.slug == ctx.slug), None)
    except (OSError, RegistryError) as exc:
        return _conflict(
            "S11",
            f"реестр {_registry_path(ctx)} не читается ({exc}) — поднимать "
            f"клиента вслепую нельзя",
            f"почини {_registry_path(ctx)} руками", facts)
    if entry is None:
        return _open(
            "S11",
            f"записи «{ctx.slug}» в реестре нет — включать нечего, а скрипт "
            f"подъёма правит именно её",
            "закрой S10 (запись в реестр) — он идёт раньше по карте §3",
            facts)
    facts["session"] = entry.session
    if not _loads(ctx):
        return _conflict(
            "S11",
            f"конфиг {_client_dir(ctx)} не грузится `load_config` — включать "
            f"такого клиента нельзя: гардиан будет поднимать и ронять раннер "
            f"по кругу каждые ~30 с",
            f"почини конфиг клиента в {_client_dir(ctx)} (шаг S4)",
            facts)
    if not session_available(entry.session, root=str(ctx.root)):
        return _open(
            "S11",
            f"сессия «{entry.session}» недоступна (ни .enc, ни plaintext) — с "
            f"`enabled: true` без неё Telethon уйдёт в интерактивный запрос "
            f"кода и повиснет, а гардиан будет рестартовать раннер по кругу",
            "войди в аккаунт клиента (шаг S6): python -m chatter.telethon_login",
            facts, awaits_human=True)

    argv = [
        POWERSHELL, "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(script),
        "-Slug", ctx.slug, "-Action", "start", "-Root", str(ctx.root),
    ]
    facts["argv"] = argv
    try:
        res = _run(ctx, argv, timeout=S11_TIMEOUT_S)
    except Exception as exc:                                   # noqa: BLE001
        # §12.6 п.3: «не смогли выполнить» ≠ «выполнили, и оно сказало нет».
        return _conflict(
            "S11",
            f"подъём не состоялся: вызов {script.name} не выполнился ({exc!r})",
            f"проверь, что {POWERSHELL} доступен, и запусти скрипт руками: "
            f".\\scripts\\chatter_client.ps1 -Slug {ctx.slug} -Action start",
            facts)

    out = _tail(f"{res.stdout}\n{res.stderr}")
    if res.rc != 0:
        # Скрипт печатает 🔴-диалоги, которых коснётся catch-up. Решение по ним
        # принимает человек: ответить клиенту самому (тогда последнее слово
        # станет за ботом) — и повторить ту же команду.
        return _conflict(
            "S11",
            f"{script.name} вернул {res.rc}: подъём остановлен (замер радиуса "
            f"отказал или реестр не принял правку). Вывод: {out}",
            f"прочитай вывод скрипта глазами и разбери отказ; диалоги, "
            f"помеченные 🔴, ведёт человек — ответь в них сам",
            facts)

    # Факт подъёма — `enabled: true` в реестре (§3). Читаем его тем же
    # парсером, а не верим коду возврата: «скрипт отработал» и «желаемое
    # состояние изменилось» — разные утверждения.
    reg = _registry_path(ctx)
    try:
        entry = next((e for e in parse_registry(reg.read_text(encoding="utf-8"))
                      if e.slug == ctx.slug), None)
        facts["enabled"] = bool(entry and entry.enabled)
    except (OSError, RegistryError) as exc:
        return _conflict(
            "S11",
            f"после подъёма реестр {reg} не читается ({exc})",
            f"почини {reg} руками", facts)
    # `radius_ok = enabled`, и это не упрощение (§12.9 п.4): скрипт выставляет
    # `enabled: true` ТОЛЬКО после чистого замера, а ветку с пропуском замера
    # мы не выбираем ни при каком флаге. Своя проба радиуса была бы вторым
    # определением того же факта и вдобавок ломала бы идемпотентность — у
    # работающего клиента диалоги с последним словом лида появляются штатно.
    facts["radius_ok"] = facts["enabled"]
    facts["stdout_tail"] = out
    return _ok("S11", f"{script.name} отработал: {out}", facts)


# ─────────────────────────────────────────────────────────────────────────────
# S12 — приёмка живости
# ─────────────────────────────────────────────────────────────────────────────

def act_s12(ctx: Ctx, *, timeout_s: float = S12_WAIT_TIMEOUT_S,
            interval_s: float = S12_POLL_INTERVAL_S,
            sleep=time.sleep) -> StepResult:
    """Ограниченное по времени ожидание с повтором пробы. НИЧЕГО не пишет (§12.6 п.6).

    У приёмки живости нет своего действия, кроме терпения: процессами
    управляет супервизор (~30 с на цикл), и второй ручки, дёргающей их мимо
    реестра, в парке нет по решению 16.08.

    Судит ТА ЖЕ проба, что закрывает шаг, — второй копии условия живости здесь
    нет. Своя копия («alive и heartbeat свежий») разошлась бы с пробой молча, и
    ожидание закончилось бы успехом там, где шаг остался открытым
    ([[jarvis-two-numbers-for-one-thing]]). Отсюда же требование Д10: `starting`
    за `alive` не считается — потому что так считает проба.

    `ctx.now` двигается на РЕАЛЬНО ПРОШЕДШЕЕ время, а не подменяется
    `time.time()`: свежесть heartbeat считается от времени снаружи (§12.2), и
    подмена сломала бы воспроизводимость там, где время задано намеренно.
    """
    # Импорт внутри: `probes` пишет другой автор, и его отсутствие не должно
    # ронять пять остальных действий на импорте пакета.
    from chatter.connect.probes import probe_s12

    started = time.monotonic()
    result = probe_s12(ctx)
    attempts = 1
    while result.verdict == Verdict.OPEN:
        elapsed = time.monotonic() - started
        if elapsed >= timeout_s:
            break
        sleep(max(0.0, min(interval_s, timeout_s - elapsed)))
        elapsed = time.monotonic() - started
        result = probe_s12(replace(ctx, now=ctx.now + elapsed))
        attempts += 1

    waited = round(time.monotonic() - started, 1)
    facts = {**dict(result.facts), "waited_s": waited, "attempts": attempts}
    if result.verdict == Verdict.CLOSED:
        return _ok("S12", result.why, facts)
    if result.verdict == Verdict.CONFLICT:
        # Противоречие ожиданием не лечится: ждать «пока факты перестанут
        # спорить» — это ждать вечно.
        return _conflict("S12", result.why, result.todo, facts)
    return _conflict(
        "S12",
        f"за {waited:.0f} с ({attempts} проб) клиент так и не стал живым: "
        f"{result.why}",
        result.todo,
        facts)


# ─────────────────────────────────────────────────────────────────────────────
# S13 — первый дрил-прогон (ВОРОТА ДЕНЕГ)
# ─────────────────────────────────────────────────────────────────────────────


def _drill_paths(ctx: Ctx) -> tuple[Path, Path, Path]:
    """БД, лог и каталог результатов прогона — все от `ctx.root`.

    Путь БД берётся ИЗ РЕЕСТРА, а не собирается из слага: у клиента он может
    быть пришпилен (volska живёт на сессии и БД demo-аккаунта), и своя догадка
    увела бы судью дрила в чужую базу.

    🔴 Каталог результатов ПЕР-КЛИЕНТСКИЙ (§12.7 п.2). Имя файла отчёта —
    голая метка времени (`<ts>.md`), слага в нём нет, а на машине лежат
    прогоны других клиентов: в общем каталоге проба увидела бы ЧУЖОЙ файл и
    объявила дрил состоявшимся, не потратив ни цента и ничего не проверив —
    ложный зелёный на единственном шаге, который доказывает, что бот отвечает.
    Каталог отделяет; тело файла доказывает (вторая половина — у пробы).
    """
    root = Path(ctx.root)
    db = root / ".secrets" / f"{ctx.slug}.db"
    try:
        entry = next((e for e in parse_registry(
            (root / "chatter" / "clients" / "registry.yaml").read_text(
                encoding="utf-8")) if e.slug == ctx.slug), None)
        if entry is not None:
            # Без `normalize_path`: она даёт вид ДЛЯ СРАВНЕНИЯ (normcase), а не
            # для открытия файла — харнессу нужен путь как он есть.
            db = (Path(entry.db) if os.path.isabs(entry.db)
                  else root / entry.db)
    except (OSError, RegistryError):
        # Реестр к этому шагу уже прочитан шагами S10 и S11, и его беда
        # всплывёт там. Здесь остаётся путь по умолчанию — тот же, что
        # записывает S10.
        pass
    log = root / "logs" / f"chatter_{ctx.slug}.log"
    out = root / "state" / "drills" / ctx.slug
    return db, log, out


def _estimate_usd(stdout: str) -> float | None:
    """Смета из строки харнесса «смета прогона: … ≈ $0.29 …».

    Своей копии ставок здесь нет и быть не может (§12.9 п.5): цена хода живёт
    в `scripts/drill_runner.py` (тёплый префикс против холодного), и второе
    определение той же вещи разошлось бы молча — ровно так плоская ставка
    занижала смету в 3.4 раза.
    """
    m = _ESTIMATE_RE.search(stdout or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _lead_peer(root: Path) -> int | None:
    """Кому пишет тестовый лид: ЕДИНСТВЕННЫЙ id из `drill_lead_peers.txt`.

    Правило то же, что у `scripts/drill_nightly.resolve_peer`, и повторено оно
    здесь по одной причине: прод-пакет не импортирует из `scripts/` —
    направление зависимости было бы вывернуто (тот же довод, что у копии
    `DRILL_CONTACTS` в `payments/drill_gate`).

    Два id в файле — это `None`, а не «возьмём первый»: догадка тут стоит
    сообщения, ушедшего ЧУЖОМУ живому аккаунту, и она необратима — сообщение
    уже увидели. Нечисловая строка тоже `None`: чинить чужой файл догадкой
    подключение не имеет права.
    """
    path = Path(root) / LEAD_PEERS_REL
    if not path.is_file():
        return None
    peers: list[int] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not line.lstrip("-").isdigit():
            return None
        peers.append(int(line))
    return peers[0] if len(peers) == 1 else None


def _quoted(value: str) -> str:
    """Путь для команды, которую человек скопирует в свою консоль."""
    return f'"{value}"' if " " in value else value


def act_s13(ctx: Ctx) -> StepResult:
    """Дрил-прогон харнессом. Останавливается перед тратой ВСЕГДА (q4, Д17).

    Порядок жёсткий, и вывернуть его нельзя:

    1. сценарий берём ОТТУДА, ГДЕ ЕГО ЧИТАЕТ ПРОБА (каталог клиента). Нет его
       там — не платим: прогон, результат которого нечем доказать, это
       выброшенные деньги;
    2. результат этого клиента уже есть, а `--drill-again` не назван — не
       платим (Д3): повтор «на всякий случай» это деньги клиента и посторонний
       трафик в его БД;
    3. зовём харнесс В РЕЖИМЕ ПЛАНА (без `--yes`) — он печатает план и СМЕТУ,
       не тратя ни цента, и считает её СВОИМИ ставками (§12.9 п.5);
    4. без `--drill-yes` останавливаемся, назвав смету в тексте остановки:
       человек обязан видеть трату ДО списания, а не узнавать о ней от бота
       (решение владельца q4);
    5. и только теперь — платный прогон с `--yes`.

    Флаг `--drill-yes` не запоминается нигде (§4): следующий запуск снова
    остановится перед тратой. Повтор при готовом результате разрешает только
    `--drill-again`, и решает это порядок исполнения, а не проба (§12.9 п.8).
    """
    # Импорт внутри: `probes` пишет другой автор, и его отсутствие не должно
    # ронять пять остальных действий на импорте пакета.
    from chatter.connect.probes import probe_s13

    db, log, out_dir = _drill_paths(ctx)
    # Спрашиваем СВОЮ ЖЕ пробу, где лежит сценарий и был ли прогон: свой список
    # имён сценария и своё правило «чей отчёт» были бы вторым определением
    # одной вещи и разъехались бы с пробой молча — а закрывает шаг она.
    seen = probe_s13(ctx)
    known = dict(seen.facts)
    facts: dict[str, Any] = {
        "run_path": known.get("run_path"),
        "rc": known.get("rc"),
        "estimate_usd": 0.0,
        "db": str(db), "out_dir": str(out_dir),
        "scenario": known.get("scenario_path"),
        "runs_found": known.get("runs_found"),
    }

    scenario_raw = known.get("scenario_path")
    scenario = Path(scenario_raw) if scenario_raw else None
    if scenario is None or not scenario.is_file():
        # Заготовку собирает `chatter.onboard` в `build/onboard/<slug>/`, а
        # оттуда её переносит S4 вместе с конфигом. Если её нет и там —
        # сочинять реплики стенда подключение не имеет права: они уйдут
        # живому человеку.
        build_copy = Path(ctx.root) / "build" / "onboard" / ctx.slug / "drill.yaml"
        return _open(
            "S13",
            "дрил-сценария нет там, где его читает проба (каталог клиента): "
            "платить за прогон, результат которого нечем доказать, нельзя"
            + (f"; заготовка лежит в {build_copy} и не перенесена"
               if build_copy.is_file() else ""),
            f"положи вычитанный сценарий в "
            f"{Path(ctx.root) / 'chatter' / 'clients' / ctx.slug} "
            f"(заготовку собирает python -m chatter.onboard {ctx.slug})",
            facts, awaits_human=True)

    runs_found = known.get("runs_found")
    already = bool(runs_found) if runs_found is not None else bool(
        list(out_dir.glob("*.md")) if out_dir.is_dir() else [])
    if already and not ctx.drill_again:
        return _open(
            "S13",
            f"прогон этого клиента уже есть ({facts['run_path'] or out_dir}): "
            f"второй платный прогон «на всякий случай» — это деньги клиента и "
            f"посторонний трафик в его БД",
            "если прогон нужен повторно — добавь --drill-again вместе с --drill-yes",
            facts, awaits_human=True)

    # Харнесс — КОД (см. `model.script_path`): в репетиционном корне §9.1
    # его нет вовсе, а взятый из `--root` он превратил бы ключ «где данные»
    # в «какой код исполнить». БД, лог, отчёты и сценарий — из `ctx.root`.
    runner = script_path("drill_runner.py")
    if not runner.is_file():
        return _conflict(
            "S13", f"нет харнесса {runner}: ни смету посчитать, ни прогон судить",
            f"восстанови {runner} из git", facts)

    # Интерпретатор — ТОТ ЖЕ, что исполняет подключение: харнесс импортирует
    # `chatter.core.drill`, и чужой python в PATH принёс бы чужой venv.
    base = [sys.executable, str(runner), str(scenario),
            "--db", str(db), "--log", str(log), "--out", str(out_dir)]

    # ── смета: ПЛАН, а не прогон ─────────────────────────────────────────────
    plan = base + ["--dry-run"]
    # Предохранитель против правки, которая однажды «объединит ветки». Не
    # `assert`: под `python -O` проверка исчезла бы ровно там, где её цена —
    # деньги владельца, потраченные без его слова.
    if "--yes" in plan:
        raise ConnectContractError(
            "S13: смета не имеет права запускать платный прогон (§4, Д17)")
    try:
        preview = _run(ctx, plan, timeout=S13_ESTIMATE_TIMEOUT_S)
    except Exception as exc:                                   # noqa: BLE001
        # §12.6 п.3: «не смогли выполнить» ≠ «выполнили, и оно сказало нет».
        return _conflict(
            "S13",
            f"смету снять не удалось: план прогона не выполнился ({exc!r}), а "
            f"тратить, не назвав цену, запрещено (решение владельца q4)",
            f"запусти руками: {sys.executable} {runner} {scenario} --dry-run",
            facts)
    if preview.rc != 0:
        return _conflict(
            "S13",
            f"план прогона вернул {preview.rc}: "
            f"{_tail(preview.stdout + ' ' + preview.stderr)}",
            f"запусти руками: {sys.executable} {runner} {scenario} --dry-run",
            facts)

    est = _estimate_usd(preview.stdout)
    facts["estimate_read"] = est is not None
    # Смету не выдумываем: не прочли — называем ПОТОЛОК §2.3 и говорим, что это
    # потолок. Врать точностью в строке про деньги нельзя.
    facts["estimate_usd"] = DRILL_COST_CEILING_USD if est is None else est
    money = (f"~${facts['estimate_usd']:.2f}"
             + ("" if est is not None
                else " (потолок §2.3: смету от харнесса прочитать не удалось)"))

    # ── предпосылки АВТОНОМНОГО прогона (§12.11 п.1) ─────────────────────────
    #
    # 🔴 Харнесс — СУФЛЁР: в ручном режиме он печатает реплики человеку ПО ХОДУ
    # прогона, а `CommandRunner` вывод захватывает. Запусти мы его отсюда
    # вручную — человек сидел бы у телефона и не видел ни одной подсказки, а
    # шаг, задуманный автоматическим, стал бы «жди, покажем потом».
    #
    # Поэтому автомату разрешён ТОЛЬКО автономный режим: реплики шлёт процесс
    # лида (`--auto-lead`), человек не нужен, и харнесс сам снимает часовой
    # таймер первого шага, заведённый ради идущего к телефону. Нет предпосылок
    # — шаг честно становится ЧЕЛОВЕЧЕСКИМ, с точной командой для СВОЕЙ
    # консоли, где суфлёр виден. Тихого прогона с невидимыми подсказками не
    # бывает ни в одной ветке.
    lead_available = session_available(LEAD_SESSION_REL, root=str(ctx.root))
    peer = _lead_peer(Path(ctx.root))
    facts["lead_session_available"] = bool(lead_available)
    facts["lead_peer_known"] = peer is not None
    if not lead_available or peer is None:
        missing = []
        if not lead_available:
            missing.append(
                f"сессии тестового лида {LEAD_SESSION_REL} нет (ни .enc, ни "
                f"plaintext)")
        if peer is None:
            missing.append(
                f"в {LEAD_PEERS_REL} не ровно один разрешённый получатель — "
                f"кому слать реплики, взять неоткуда, а угадать нельзя: "
                f"сообщение уходит живому аккаунту")
        manual = " ".join(_quoted(str(x)) for x in (
            sys.executable, runner, scenario, "--db", db, "--log", log,
            "--out", out_dir, "--yes"))
        return _open(
            "S13",
            f"автономный прогон невозможен: {'; '.join(missing)}. Запускать "
            f"суфлёр отсюда нельзя — его подсказки уходят в захваченный вывод, "
            f"и человек у телефона не увидит ни одной. Смета прогона {money}",
            f"прогони дрил САМ, в своей консоли, где суфлёр виден: {manual}",
            facts, awaits_human=True)

    if not ctx.drill_yes:
        # 🔴 ВОРОТА ДЕНЕГ. Причина конкретная и названа владельцем: позавчера
        # баланс кончился посреди прогона, и он узнал об этом от бота.
        return _open(
            "S13",
            f"дрил — платный прогон, сейчас спишется {money}, и человек обязан "
            f"видеть это ДО списания, а не узнавать от бота",
            "разреши трату: повтори эту же команду с флагом --drill-yes "
            "(прогон идёт сам, человек у телефона не нужен)",
            facts, awaits_human=True)

    # ── платный прогон ───────────────────────────────────────────────────────
    # `--lead-peer` сверяется с allowlist'ом ВНУТРИ `drill_lead` — это его
    # предохранитель, и обходить его подстановкой мы не пытаемся.
    # `--lead-peer-username` не подставляем: username аккаунта персоны брать
    # неоткуда, а выдуманный увёл бы реплики стенда к чужому человеку. Если
    # Telethon не сможет резолвить голый id, харнесс скажет это вслух, и
    # прогон уйдёт человеку в его консоль — веткой выше.
    argv = base + ["--yes", "--auto-lead",
                   "--lead-session", str(Path(ctx.root) / LEAD_SESSION_REL),
                   "--lead-peer", str(peer)]
    facts["argv"] = argv
    before = {p.name for p in out_dir.glob("*.md")} if out_dir.is_dir() else set()
    try:
        res = _run(ctx, argv, timeout=S13_RUN_TIMEOUT_S)
    except Exception as exc:                                   # noqa: BLE001
        return _conflict(
            "S13",
            f"прогон не состоялся: харнесс не выполнился ({exc!r}). Часть денег "
            f"могла быть уже потрачена — смотри {out_dir}",
            f"прочитай {out_dir} глазами и разберись, начинался ли прогон, "
            f"прежде чем платить второй раз",
            facts)

    after = {p.name for p in out_dir.glob("*.md")} if out_dir.is_dir() else set()
    fresh = sorted(after - before)
    facts["rc"] = res.rc
    if fresh:
        facts["run_path"] = str(out_dir / fresh[-1])
    facts["stdout_tail"] = _tail(res.stdout + " " + res.stderr)

    if res.rc != 0 or not fresh:
        # Код возврата харнесса — это ВЕРДИКТ, а не счётчик красного: 2 =
        # прогон не состоялся (шаг пропущен по тайм-ауту), 1 = красные
        # проверки на полном прогоне. Оба означают, что читать надо отчёт, а
        # не платить за второй прогон.
        return _conflict(
            "S13",
            f"харнесс вернул {res.rc}"
            + (f", отчёт {facts['run_path']}" if fresh
               else ", отчёта не появилось")
            + f": {facts['stdout_tail']}",
            f"прочитай отчёт прогона в {out_dir} глазами; повтор стоит денег и "
            f"без разбора причины ничего не изменит",
            facts)

    return _ok("S13",
               f"дрил пройден: {facts['run_path']}, код возврата {res.rc}, "
               f"смета {money}", facts)
