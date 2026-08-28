# -*- coding: utf-8 -*-
"""T7 `chatter.connect`: человеческие ворота и ворота денег (спека §7, Д3, Д5,
Д6, Д11, Д15, Д16, Д17).

Сторожа написаны ОТ СПЕКИ `docs/superpowers/specs/2026-08-20-t7-turnkey-connect.md`
автором, который планового кода не видел: `chatter/connect/**` при написании НЕ
открывался и не грепался ([[jarvis-guards-not-by-the-plan-author]]). Читались
только спека (особенно §2.3, §3, §4, §5, §7, §12) и ПРОДОВЫЕ модули, на которые
спека ссылается по имени: `chatter/payments/drill_gate.py` (канон дрил-контактов),
`chatter/config/loader.py` (форма `telegram.allowlist`, `control.*`),
`chatter/clients/registry.yaml` (форма реестра).

Пять классов ошибки, которые здесь ловятся, и цена каждого:

  1. **Деньги без спроса** (Д17). Дрил стоит $0.20–0.30, и решение владельца q4
     звучит буквально: «позавчера баланс кончился посреди прогона, и я узнал об
     этом от бота». Значит доказывать надо не «спросили», а «НЕ ПОТРАТИЛИ»:
     подставной `CommandRunner` не должен увидеть `drill_runner` НИ РАЗУ.
  2. **Деньги дважды** (Д3). Повтор «на всякий случай» — это не только $0.25, но
     и посторонний трафик в БД клиента (§2.3).
  3. **Токен, доказанный значением** (Д5, §12.5). Секрет, попавший в `facts`,
     журнал или вывод, уезжает в репозиторий вместе с ними. Поэтому здесь нет ни
     одного настоящего токена, а сторож проверяет, что синтетический НЕ ВИДЕН
     нигде — при том что вердикт про него всё равно выносится.
  4. **Молчаливая остановка** (Д11). Остановка без «что сделать» превращает один
     вход обратно в двенадцать команд: человек снова обязан помнить, чем
     продолжать. Проверяется СТРУКТУРНО — перебором всех шагов `STEPS` по
     нескольким мирам, — а не на трёх примерах, потому что забывают ровно ту
     ветку, о которой не подумали.
  5. **Согласие и бандл как формальность** (Д15, Д16). Бот читает ВСЮ личку
     аккаунта; §5.7 разрешает автомату ровно одно — требовать факт и отказываться
     идти дальше.

**Почему тексты не проверяются дословно.** §12.2 прямо запрещает: сторож на буквы
сообщения сделал бы правку формулировки красной. Проверяются непустота,
адресность (в тексте назван путь, скрипт, флаг или слаг) и `facts`.

**Где именно крутится пайплайн.** Утверждения делаются на трёх высотах, и это
не дублирование: `act_s13` — там, где деньги списываются; `walk()` — дословное
воспроизведение §12.3 (с поправками §12.7 п.1 и §12.10 п.1) поверх `STEPS`, где
проверяется ПОРЯДОК; `main()` из §12.7 п.3 — дверь, которой пользуется человек.
Шов `main(argv, *, runner=..., root=...)` контракт объявил ровно ради сторожей: без
него проверка денег через живую дверь означала бы живые подпроцессы, живой
телеграм и живой реестр, то есть не существовала бы.

**Что считается тратой.** Не «вызов харнесса»: по §12.9 п.5 `act_s13` зовёт
`drill_runner` и БЕЗ разрешения — в режиме плана, чтобы взять смету у самого
харнесса (наша копия ставок разошлась бы с его `COST_TURN_*` молча). Платит
ровно `--yes`, поэтому все денежные сторожа смотрят на `runner.paying_calls`.
Сторож на «раннер не звали вообще» был бы красным на исправном коде — тот же
класс, что mtime сессии в Д16.

**Про mtime сессии (Д16).** Спека дважды (S5 и S8) отвергает сверку времён:
`.session` переписывается Telethon при каждой записи, и после подъёма раннера
такой сторож краснел бы на здоровом подключении. Поэтому независимость вердикта
от mtime — ОТДЕЛЬНЫЙ ассерт: вердикт снимается, mtime сессии двигается в будущее
и в прошлое, вердикт снимается снова и сравнивается.

**Номера шагов** взяты ТОЛЬКО из таблицы §3 (правило §12.6 п.1): согласие S5,
логин S6, токен S7, маркер бандла S8, allowlist S9, дрил S13. Проза §4–§6 какое-то
время несла старые номера — здесь их нет ни одного.

**Пути фактов** — по §12.8 п.3: согласие живёт в `state/connect/<slug>.consent.md`,
рядом с маркером бандла, а НЕ в каталоге клиента (который создаёт S4 — и тогда
аккуратный человек, положивший согласие первым, вставал бы намертво на
ПРОТИВОРЕЧИИ S4). Отчёты прогонов — в `state/drills/<slug>/`, и тело отчёта
обязано называть клиента (§12.7 п.2).

**`False` против `None` в уликах** (§12.12 п.2): `False` значит «проверили,
нет»; «не проверяли» — это `None`. Поэтому у отсутствующего маркера
`slug_named` обязан быть `None`, а не `False`: иначе человек пойдёт искать в
маркере строку, которой там нет вовсе.

**Ключи `facts`** — из таблицы §12.6 п.2, выписаны литералом в `REQUIRED_FACTS`.
Именно они несут тонкие утверждения; `token_len` — тот самый признак, на который
Д5 опирается ВМЕСТО значения секрета.

Живого не касается ничего: весь мир строится в `tmp_path`, наружу ведёт только
подставной `CommandRunner`, `os.environ` не читается и не пишется.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

# Канон дрил-контактов — ПРОДОВАЯ константа (§7 Д6: «дрил-контакт берётся из
# payments/drill_gate.DRILL_CONTACTS»). Список НЕ переписывается литералом:
# вторая копия разъехалась бы с первой, и сторож зеленел бы на своей копии —
# ровно то, что уже месяц ловил `tests/test_drill_contacts_sync.py`.
from chatter.core import drill
from chatter.core.contact_ref import telegram_peer_of
from chatter.payments.drill_gate import DRILL_CONTACTS

# Каталог клиента берётся у сторожей T4, а не пишется заново, по той же причине.
from tests.test_onboard_checks import report_document, write_client

# Замороженные константы `chatter.onboard` — узкое исключение §12.9 п.7: запрет
# q5 про ПРАВА и радиус ошибки, а не про два имени файлов. Своя копия строки
# "drill.yaml" была бы вторым определением одной вещи.
from chatter.onboard.checks import REVIEWED_FILENAME
from chatter.onboard.drill_scenario import SCENARIO_FILE_NAME

# Ставки, смета и ФОРМАТ ОТЧЁТА — из САМОГО харнесса (§12.9 п.5): наша копия
# ставок разошлась бы с ним молча. `format_report` берётся по той же причине:
# отчёт прогона — артефакт, который производит харнесс, и переписанный в тесте
# «примерно такой» отчёт был бы вторым определением одного формата. Разошлись
# бы они тихо и ровно там, где проба читает вердикт: код возврата харнесса на
# диск не пишется вовсе, и эта строка — единственный его след.
from scripts.drill_runner import (COST_TURN_COLD, COST_TURN_WARM, Money,
                                  estimate_cost, format_report)

from chatter.connect import actions, probes, steps
from chatter.connect.__main__ import main as connect_main
from chatter.connect.model import Ctx, Owner, StepResult, Verdict, repo_tree

# ─────────────────────────────────────────────────────────────────────────────
# Константы стенда
# ─────────────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc).timestamp()

# Слаг стенда. Берётся НЕ произвольный: в `DRILL_CONTACTS` контакт привязан к
# ПЕРСОНЕ (`<id>:<persona>`), и у слага, которого там нет, дрил-контакта не
# существует вовсе — S9 честно отказывается выдумывать id стенда. Значит стенд,
# проверяющий запись allowlist, обязан стоять на слаге, у которого контакт есть;
# иначе он проверял бы отказ, а не запись.
SLUG = "yarina"
NEIGHBOUR_SLUG = "volska"
FOREIGN_SLUG = "demo"                # третий слаг: только для «чужого» маркера

OWNER_CHAT_ID = 700000001            # владелец: синтетический id, не живой
# Голова contact_id берётся ВЛАДЕЛЬЦЕМ, а не срезом: на форме
# `telegram:<id>:<persona>` срез по первому двоеточию даёт "telegram", и
# `int()` от него роняет модуль НА СБОРЕ — то есть уносит с собой всю суиту,
# а не свой файл (замер 28.08: одна эта строка обрывала прогон целиком).
DRILL_IDS = frozenset(telegram_peer_of(c) for c in DRILL_CONTACTS)
CLIENT_DRILL_IDS = frozenset(
    telegram_peer_of(c) for c in DRILL_CONTACTS if c.endswith(f":{SLUG}"))


def client_drill_contact(slug: str = SLUG) -> str:
    return sorted(c for c in DRILL_CONTACTS if c.endswith(f":{slug}"))[0]

# Приманки: id, которые ЕСТЬ в материале подключения (отчёт сборки, журнал,
# окружение) и которые автомат не имеет права вписать в allowlist. В жизни это
# ссылки на аккаунты клиента из брифа (Q8) — живые люди, которым бот начал бы
# отвечать.
DECOY_IN_REPORT = 111222333
DECOY_IN_JOURNAL = 444555666
DECOY_IN_ENV = 777888999
DECOYS = frozenset({DECOY_IN_REPORT, DECOY_IN_JOURNAL, DECOY_IN_ENV})

# Синтетический «токен»: длина как у настоящего, но это не токен. Ни одна строка
# вывода не имеет права его содержать (§12.5).
FAKE_TOKEN = "8100000001:SYNTHETIC-NOT-A-REAL-TOKEN-0000000000"
PER_CLIENT_ENV = f"CHATTER_CONTROL_BOT_TOKEN_{SLUG.upper()}"
SHARED_ENV = "CHATTER_CONTROL_BOT_TOKEN"          # общая на всех — ловушка Д5
NEIGHBOUR_ENV = f"CHATTER_CONTROL_BOT_TOKEN_{NEIGHBOUR_SLUG.upper()}"

DRILL_MARKER = "drill_runner"
# §12.9 п.5: без разрешения `act_s13` ВСЁ РАВНО зовёт харнесс — в режиме плана,
# ради сметы. Значит «не потратили» доказывается отсутствием ПЛАТЯЩЕГО вызова,
# а не отсутствием вызова вообще: у `drill_runner` платит ровно `--yes`
# (`scripts/drill_runner.py`: «Запуск ТОЛЬКО по явному --yes»).
PAYING_FLAG = "--yes"

AUTO_STEPS = ("S4", "S9", "S10", "S11", "S12", "S13")
ALL_STEP_IDS = tuple(f"S{i}" for i in range(16))
# §12.10 п.1: в карте они есть (её читает человек в `--plan`), в цикле — нет.
TAIL_HUMAN_IDS = ("S14", "S15")

# §12.6 п.2 — минимальная схема `facts` для шагов, за которые отвечает этот файл.
# Выписана ЛИТЕРАЛЬНО, а не выведена интроспекцией: список, полученный из
# реализации, согласен с ней по определению и молчит там, где она забыла
# ([[jarvis-literal-lists-not-introspection]]).
REQUIRED_FACTS: dict[str, tuple[str, ...]] = {
    "S5": ("consent_path", "consent_date"),
    "S7": ("env_name", "token_len", "env_enc_state"),
    "S8": ("marker_path", "slug_named"),
    "S9": ("allowlist", "owner_present", "drill_present"),
    "S13": ("run_path", "rc", "estimate_usd"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Подставной CommandRunner (§12.2)
#
# Единственный путь наружу. Он же — улика: «дрил не запускался» доказывается
# тем, что раннера с `drill_runner` в argv никто не звал. Заглушка, которая
# принимает что угодно и молча отвечает rc 0, нам не годится: она сделала бы
# зелёным и код, который зовёт харнесс, — поэтому каждый вызов записывается
# целиком, вместе с cwd и timeout.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CommandResult:
    rc: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class Call:
    argv: tuple[str, ...]
    cwd: Path | None
    timeout: float | None

    def mentions(self, needle: str) -> bool:
        return any(needle in str(a) for a in self.argv)


class FakeRunner:
    """`CommandRunner` из §12.2: один метод `run`, ничего живого.

    `replies` — подстрока argv -> ответ. `on_call` — побочный эффект (например,
    «харнесс дописал файл прогресса»), чтобы миры можно было строить честно.
    """

    def __init__(self, replies: dict[str, CommandResult] | None = None,
                 on_call=None, default: CommandResult | None = None) -> None:
        self.calls: list[Call] = []
        self._replies = dict(replies or {})
        self._on_call = on_call
        self._default = default or CommandResult(0, "", "")

    def run(self, argv, *, cwd=None, timeout=None) -> CommandResult:
        call = Call(tuple(str(a) for a in argv), cwd, timeout)
        self.calls.append(call)
        if self._on_call is not None:
            self._on_call(call)
        for needle, res in self._replies.items():
            if call.mentions(needle):
                return res(call) if callable(res) else res
        return self._default

    # ── улики ────────────────────────────────────────────────────────────────
    @property
    def drill_calls(self) -> list[Call]:
        return [c for c in self.calls if c.mentions(DRILL_MARKER)]

    @property
    def paying_calls(self) -> list[Call]:
        """Вызовы, которые СПИСЫВАЮТ деньги: харнесс с `--yes`."""
        return [c for c in self.drill_calls if PAYING_FLAG in c.argv]

    @property
    def plan_calls(self) -> list[Call]:
        return [c for c in self.drill_calls if PAYING_FLAG not in c.argv]

    def argv_log(self) -> str:
        return "\n".join(" ".join(c.argv) for c in self.calls) or "(вызовов не было)"


def plan_stdout(cold: int = 1, warm: int = 4) -> str:
    """Вывод харнесса в режиме плана — форматом САМОГО харнесса.

    Смету считает он (§12.9 п.5); наша копия ставок разошлась бы с ним молча,
    поэтому строка собирается из его же `COST_TURN_*`.
    """
    est = cold * COST_TURN_COLD + warm * COST_TURN_WARM
    return (f"смета прогона: {cold}×холодный ${COST_TURN_COLD:.4f} + "
            f"{warm}×тёплый ${COST_TURN_WARM:.4f} ≈ ${est:.2f} "
            f"(факт посчитаю после прогона по ходам дрила)\n"
            f"\nПЛАН ПРОГОНА — все реплики целиком, читать сверху вниз:\n"
            f"  1. лид: «добрий день»\n"
            f"\nэто ПЛАН. Запуск: добавь --yes (и будь у телефона — "
            f"каждый шаг ждёт твоей реплики Ольге)\n")


PLAN_ESTIMATE_USD = 1 * COST_TURN_COLD + 4 * COST_TURN_WARM


def enc_reply(state: str) -> CommandResult:
    """Ответ `reencrypt_env.ps1 -Check` — в форме САМОГО инструмента.

    Голое слово `in_sync` на stdout — мир, которого не бывает:
    `scripts/reencrypt_env.py::main` печатает `[reencrypt] статус до: <state>`
    (обёртка `.ps1` вывод не трогает), и по этой строке состояние читают ВСЕ,
    включая человека. Сторож, кормящий пробу голым словом, проверял бы разбор
    формата, который никто не производит.

    Код возврата — оттуда же: у `--check` он `0` РОВНО у `in_sync`, у всех
    прочих состояний `1`. `stale` — рабочее состояние машины, и `1` здесь не
    авария, а «из этого бандл не снимают».
    """
    return CommandResult(0 if state == "in_sync" else 1,
                         f"[reencrypt] статус до: {state}\n", "")


def default_replies() -> dict[str, CommandResult]:
    """Ответы, при которых внешние команды «отработали штатно».

    Нужны, чтобы мир мог быть доведён до S13: сторож про деньги обязан ловить
    отказ тратить, а не «до дрила и так не дошли».
    """
    return {
        "reencrypt_env": enc_reply("in_sync"),
        "registry_cli": CommandResult(0, json.dumps(
            {"runnable": True, "error": None, "clients": {}}, ensure_ascii=False), ""),
        "chatter_client": CommandResult(0, "catch-up radius: 0 messages\nstarted", ""),
        DRILL_MARKER: CommandResult(0, plan_stdout(), ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Мир на диске
# ─────────────────────────────────────────────────────────────────────────────

def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    for rel in ("chatter/clients", ".secrets", "state/connect", "state/drills",
                "logs", "build/onboard"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def write_registry(root: Path, clients: dict) -> Path:
    path = root / "chatter" / "clients" / "registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"clients": clients}, allow_unicode=True,
                                   sort_keys=False), encoding="utf-8")
    return path


def registry_entry(slug: str, *, enabled: bool = False) -> dict:
    return {"enabled": enabled, "personas": [slug],
            "session": f".secrets/{slug}.session", "db": f".secrets/{slug}.db"}


def telegram_block(allowlist: list[int]) -> str:
    """`telegram:` в той форме, в какой он живёт у клиентов на диске.

    Форма — предмет, а не украшение. Ни один живой `settings.yaml`
    (`chatter/clients/*/settings.yaml`) не выглядит как машинный дамп: список
    там ПОТОЧНЫЙ, в одну строку, а вокруг него десятки строк предупреждений
    про `funnel_gate` и порядок допуска. Половина документации продукта живёт
    именно здесь, поэтому конфиг правят ТЕКСТОМ — и на блочном списке (`- 1`
    строкой) правка честно отказывается: угаданная форма записи портит текст,
    который писал человек.

    Мир, собранный `yaml.safe_dump`, проверял бы этот отказ вместо записи.
    """
    ids = "[" + ", ".join(str(int(i)) for i in allowlist) + "]"
    return (
        "telegram:\n"
        "  # allowlist = override «отвечать ВСЕГДА», и он бьёт проверку на\n"
        "  # контакт (admission.py: denylist > allowlist > contact > stranger).\n"
        "  # При funnel_gate: false это ЕДИНСТВЕННЫЙ источник допуска: без\n"
        "  # записи бот молча игнорирует стенд, а выглядит это как «бот не\n"
        "  # отвечает».\n"
        f"  allowlist: {ids}\n"
        "  # denylist: [123456]      # id, которым НИКОГДА не отвечать\n"
        "  # Арка 3C — перевёрнутый гейт допуска. ВЫКЛЮЧЕН, и значение здесь —\n"
        "  # ФАКТ, а не цель: гардиан деплоит из рабочего дерева, и вписанный\n"
        "  # наперёд true поднял бы раннер с открытым гейтом БЕЗ слова\n"
        "  # владельца, а catch-up на старте веером ответил бы незнакомцам.\n"
        "  funnel_gate: false\n"
        "# Открывает трафик владелец командой пульта: /funnel_gate on confirm —\n"
        "# без рестарта и без правки файла.\n"
    )


def install_live_client(root: Path, slug: str = SLUG, *,
                        token_env: str | None = PER_CLIENT_ENV,
                        owner_chat_id: int | None = OWNER_CHAT_ID,
                        allowlist: list[int] | None = None) -> Path:
    """Живой каталог `chatter/clients/<slug>/` из ДОКАЗАННОЙ фикстуры T4.

    `write_client` кладёт каталог в форме `build/onboard/<slug>`; подключение
    переносит его в боевой (S4), поэтому здесь делается ровно перенос — а не
    вторая, своя версия «правильного каталога клиента».
    """
    build = write_client(root, slug=slug)
    live = root / "chatter" / "clients" / slug
    live.mkdir(parents=True, exist_ok=True)
    shutil.copytree(build, live, dirs_exist_ok=True)

    data = yaml.safe_load((live / "settings.yaml").read_text(encoding="utf-8"))
    # `telegram:` пишется ТЕКСТОМ, а не через дамп всего документа: см.
    # `telegram_block`. `funnel_gate: false` там прибит — §2.1 держит гейт
    # закрытым на всём протяжении подключения.
    data.pop("telegram", None)

    control = dict(data.get("control") or {})
    if token_env is None:
        control.pop("control_bot_token_env", None)
    else:
        control["control_bot_token_env"] = token_env
    if owner_chat_id is None:
        control.pop("owner_chat_id", None)
    else:
        control["owner_chat_id"] = owner_chat_id
    data["control"] = control

    (live / "settings.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        + telegram_block([] if allowlist is None else list(allowlist)),
        encoding="utf-8")

    # §12.11 п.3: сценарий дрила живёт в каталоге клиента — `act_s13` без него
    # отказывается платить (иначе деньги списаны, а шаг открыт).
    (live / SCENARIO_FILE_NAME).write_text(
        (build / SCENARIO_FILE_NAME).read_text(encoding="utf-8")
        if (build / SCENARIO_FILE_NAME).exists() else drill_scenario_yaml(slug),
        encoding="utf-8")
    return live


def drill_scenario_yaml(slug: str = SLUG) -> str:
    """Сценарий, который разбирает НАСТОЯЩИЙ `chatter.core.drill.parse_scenario`.

    Судья формата один, и это он: свой «примерно такой» YAML сделал бы сторожа
    зелёным на сценарии, который харнесс не примет, — то есть проверял бы
    деньги на пути, которого в жизни нет. Реплики намеренно НЕпохожи друг на
    друга: `parse_scenario` отвергает сценарий, шаг которого опознаётся как
    соседний (DEV-32).
    """
    contact = client_drill_contact(slug)
    text = yaml.safe_dump(
        {"name": f"дрил {slug}", "client": slug, "contact": contact,
         "steps": [{"say": "добрий день, цікавить детейлінг",
                    "expect": {"no_duplicate_reply": True}},
                   {"say": "скільки коштує полірування кузова",
                    "expect": {"cache": "miss"}}]},
        allow_unicode=True, sort_keys=False)
    drill.parse_scenario(text)      # отказ громкий и здесь, а не у харнесса
    return text


def live_allowlist(root: Path, slug: str = SLUG) -> list[int] | None:
    path = root / "chatter" / "clients" / slug / "settings.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tg = data.get("telegram") or {}
    raw = tg.get("allowlist")
    if raw is None:
        return None
    return [int(x) for x in raw]


def write_consent(root: Path, text: str, slug: str = SLUG) -> Path:
    """§12.8 п.3: согласие живёт в `state/connect/<slug>.consent.md`.

    В каталоге клиента ему не место: каталог создаёт S4, а согласие пишет
    человек ДО логина — тот, кто сделал правильно, создавал бы каталог с одним
    файлом, и S4 объявлял бы ПРОТИВОРЕЧИЕ. Подключение вставало бы намертво
    ровно у аккуратного.
    """
    path = root / "state" / "connect" / f"{slug}.consent.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def consent_text(when: datetime | None = None) -> str:
    """§12.7 п.4: первая строка начинается с ISO-даты, дальше — от кого."""
    day = (when or datetime.fromtimestamp(NOW, tz=timezone.utc) - timedelta(days=1))
    return (f"{day.date().isoformat()} — согласие на доступ бота ко ВСЕЙ личке "
            f"аккаунта получено от владелицы {SLUG} (голосовое в Telegram).\n")


def write_session(root: Path, slug: str = SLUG, *, mtime: float | None = None) -> Path:
    path = root / ".secrets" / f"{slug}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"SQLite format 3\x00synthetic-telethon-session")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def write_bundle_marker(root: Path, line: str, slug: str = SLUG) -> Path:
    path = root / "state" / "connect" / f"{slug}.bundle.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line, encoding="utf-8")
    return path


def bundle_line(*slugs: str) -> str:
    """Строка, которую печатает сам экспорт бандла (решение владельца q3, вар. б)."""
    return ("пересъём .jrvbak 2026-08-20 09:12\n"
            "сессии в бандле: " + ", ".join(f"{s}.session" for s in slugs) + "\n")


def drills_dir(root: Path, slug: str = SLUG) -> Path:
    """§12.7 п.2: свой каталог на клиента. Общая куча дала бы ложный зелёный —
    чужой прогон закрыл бы S13, не потратив ни цента и ничего не проверив."""
    return root / "state" / "drills" / slug


def write_drill_result(root: Path, ts: str = str(int(NOW) - 86_400),
                       slug: str = SLUG, *, names_client: str | None = None) -> Path:
    """Отчёт прогона — САМИМ `scripts.drill_runner.format_report` (§12.7 п.2).

    Отчёт производит харнесс, вердикт в него кладёт
    `chatter.core.drill.run_verdict`, и проба читает РОВНО эти маркеры: кода
    возврата харнесса на диске нет вовсе, единственный его след — строка
    вердикта. Свой «итог: rc=0» — второе определение одной вещи: оно
    разошлось бы с харнессом молча, и сторож про повторную трату зеленел бы на
    отчёте, которого в жизни не бывает.

    Имя файла — `<unix_ts>.md` и только оно (`_owner_line` харнесса про это же):
    слага в пути нет, и принадлежность доказывает ТЕЛО, а не каталог. Поэтому
    `names_client` подменяет клиента ЦЕЛИКОМ — и имя, и контакт: отчёт, в
    котором клиент чужой, а дрил-контакт наш, был бы нашим на вид.
    """
    named = slug if names_client is None else names_client
    scenario = drill.parse_scenario(drill_scenario_yaml(named))
    outcomes = tuple(
        drill.StepOutcome(say=st.say, checks=tuple(
            drill.CheckResult(key=key, ok=True, detail="стенд: проверка зелёная")
            for key in sorted(st.expect)))
        for st in scenario.steps)
    est = estimate_cost(scenario.steps)
    path = drills_dir(root, slug) / f"{ts}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        format_report(scenario.name, outcomes,
                      money=Money(estimate=est, drill=est, window=est),
                      client=named, contact=scenario.contact) + "\n",
        encoding="utf-8")
    return path


def drill_results(root: Path, slug: str = SLUG) -> list[Path]:
    return sorted(drills_dir(root, slug).glob("*.md"))


def write_lead_session(root: Path) -> Path:
    """§12.11 п.1, предпосылка ПЕРВАЯ: сессия тестового лида.

    Без неё автономного прогона нет, и S13 становится человеческим шагом с
    точной командой — тихого прогона не бывает.
    """
    path = root / ".secrets" / "drill_lead.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"SQLite format 3\x00synthetic-lead-session")
    return path


def write_lead_peers(root: Path, peer: int | None = None) -> Path:
    """§12.11 п.1, предпосылка ВТОРАЯ: кому тестовый лид шлёт реплики.

    Предпосылок у автономного прогона две, и мир с одной из них — не «почти
    готовый», а ДРУГОЙ: автомату разрешён только `--auto-lead` (харнесс —
    суфлёр, и запущенный из-под захваченного вывода он оставил бы человека у
    телефона без единой подсказки), поэтому без файла получателей шаг честно
    становится человеческим и не тратит ни цента. Сторож, который ждёт
    АВТОНОМНОГО платного прогона, в таком мире проверял бы отказ платить —
    то есть не проверял бы ничего.

    Получатель ровно ОДИН и это дрил-контакт клиента: два id в файле — это
    «неизвестно кому», а угадать нельзя, реплика уходит живому аккаунту
    необратимо. Тот же id лежит в allowlist мира: два числа на одну вещь
    разъехались бы молча.
    """
    peer = telegram_peer_of(client_drill_contact()) if peer is None else peer
    path = root / ".secrets" / "drill_lead_peers.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# дрил-контакт стенда, единственный разрешённый получатель\n"
                    f"{peer}\n", encoding="utf-8")
    return path


def write_journal(root: Path, slug: str = SLUG, extra: str = "") -> Path:
    """Журнал для ЧЕЛОВЕКА (§1): пробы его не читают никогда."""
    path = root / "state" / "connect" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# журнал подключения {slug}\n{extra}\n", encoding="utf-8")
    return path


def write_liveness(root: Path, slug: str = SLUG, *, status: str = "alive",
                   heartbeat_age_s: float = 5.0) -> Path:
    path = root / "state" / "chatter_clients.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "clients": {slug: {"status": status, "pid": 4242,
                           "heartbeat_ts": NOW - heartbeat_age_s}},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_log(root: Path, slug: str = SLUG, *, isolated_token: bool = True) -> Path:
    path = root / "logs" / f"chatter_{slug}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = ("control-bot poller starting (isolated token)"
            if isolated_token else "control-bot poller starting")
    path.write_text(f"2026-08-20 11:59:00 INFO runner starting\n"
                    f"2026-08-20 11:59:01 INFO {line}\n", encoding="utf-8")
    return path


def write_build(root: Path, slug: str = SLUG, *, reviewed: bool = True,
                decoy_id: int | None = DECOY_IN_REPORT) -> Path:
    """`build/onboard/<slug>/` с отчётом и меткой вычитки (факты S1–S3)."""
    build = write_client(root, slug=slug)
    doc = report_document()
    if decoy_id is not None:
        # Ссылки клиента на его собственные аккаунты — ровно тот материал, из
        # которого «само собой» вписался бы посторонний id.
        doc = dict(doc)
        doc["client_accounts"] = [str(decoy_id), f"@{slug}_manager"]
    (build / "report.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    if reviewed:
        (build / REVIEWED_FILENAME).write_text(
            "вычитано 2026-08-20, флаги C7, C12, C13, C16 названы и решены\n",
            encoding="utf-8")
    return build


def env_material(*, token_env: str | None = PER_CLIENT_ENV,
                 token_value: str | None = FAKE_TOKEN,
                 neighbour_token: bool = False,
                 decoy: bool = True) -> dict[str, str]:
    """Материал окружения (§3/S0). Настоящих секретов здесь нет и быть не может.

    Владельца здесь НЕТ намеренно: §12.7 п.5 говорит, что owner id берётся из
    `control.owner_chat_id` живого конфига, и только оттуда. Вместо владельца в
    окружении лежит ПРИМАНКА — если она доедет до allowlist, значит источник
    второй, и два источника одной вещи разъедутся молча.
    """
    env: dict[str, str] = {
        "TELEGRAM_API_ID": "1234567",
        "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
        "ANTHROPIC_API_KEY": "sk-ant-SYNTHETIC-0000000000000000",
    }
    if token_env is not None and token_value is not None:
        env[token_env] = token_value
    if neighbour_token:
        env[NEIGHBOUR_ENV] = "8100000002:SYNTHETIC-NEIGHBOUR-000000000000000"
    if decoy:
        env["TELEGRAM_OWNER_ID"] = str(DECOY_IN_ENV)
        env["CHATTER_OWNER_CHAT_ID"] = str(DECOY_IN_ENV)
        env["CHATTER_ALLOWLIST_EXTRA"] = str(DECOY_IN_ENV)
    return env


def make_ctx(root: Path, *, slug: str = SLUG, env: dict | None = None,
             runner: FakeRunner | None = None, now: float = NOW,
             drill_yes: bool = False, drill_again: bool = False) -> Ctx:
    """§12.8 п.4: поле называется `runner`, вызов — `ctx.runner.run(...)`.

    Прежнее `run` допускало два законных прочтения (`ctx.run(...)` против
    `ctx.run.run(...)`), и авторы разошлись на нём молча — тот же класс, что
    ловят Д11 и Д16.
    """
    return Ctx(root=root, slug=slug, now=now,
               env=dict(env if env is not None else env_material()),
               runner=runner if runner is not None else FakeRunner(default_replies()),
               drill_yes=drill_yes, drill_again=drill_again)


# ─────────────────────────────────────────────────────────────────────────────
# §12.3: порядок исполнения, воспроизведённый дословно
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Walk:
    rc: int
    visited: list[str] = field(default_factory=list)
    stop: StepResult | None = None
    results: list[StepResult] = field(default_factory=list)


def walk(step_list, ctx: Ctx) -> Walk:
    """§12.3 с поправками §12.7 п.1 (ожидание человека у автошага) и §12.10 п.1
    (хвостовые S14/S15 в цикл не входят — иначе 0 не достижим никогда)."""
    out = Walk(rc=0)
    for st in step_list:
        if st.id in TAIL_HUMAN_IDS:
            continue
        out.visited.append(st.id)
        r = st.probe(ctx)
        out.results.append(r)
        if r.verdict == Verdict.CONFLICT:
            return Walk(1, out.visited, r, out.results)
        if r.verdict == Verdict.CLOSED:
            continue                       # факт на диске старше ожидания
        if st.owner == Owner.HUMAN:
            return Walk(3, out.visited, r, out.results)
        ar = st.act(ctx)
        r2 = st.probe(ctx)          # доказательство, а не «act не упал»
        out.results.append(r2)
        if r2.verdict == Verdict.CLOSED:
            continue
        waiting = bool(getattr(r2, "waits_for_human", False)
                       or getattr(ar, "waits_for_human", False))
        return Walk(3 if waiting else 1, out.visited, r2, out.results)
    return out


def _closed_stub(step_id: str):
    # `why`/`todo` непусты даже у заглушки: §12.6 говорит, что `StepResult`
    # проверяет непустоту в конструкторе, и заглушка не имеет права быть
    # сконструирована способом, которым не может быть сконструирован настоящий
    # результат — иначе стенд проверял бы то, чего в жизни не бывает.
    def probe(_ctx: Ctx) -> StepResult:
        return StepResult(step_id=step_id, verdict=Verdict.CLOSED,
                          why="стенд: шаг объявлен закрытым",
                          todo="стенд: делать нечего", facts={})
    return probe


def _forbidden_act(step_id: str):
    def act(_ctx: Ctx) -> StepResult:
        raise AssertionError(f"стенд: act {step_id} не должен вызываться — "
                             f"его проба объявлена закрытой")
    return act


def isolate(*keep: str, spies: dict | None = None):
    """`STEPS`, где реальны только названные шаги, остальные объявлены закрытыми.

    Это и делает сторожа про деньги НЕпустым: без изоляции «дрил не запускался»
    было бы зелёным просто потому, что до S13 не дошли.
    """
    spies = spies or {}
    out = []
    for st in steps.STEPS:
        if st.id in keep:
            out.append(st)
        elif st.id in spies:
            out.append(dataclasses.replace(st, probe=spies[st.id]))
        else:
            out.append(dataclasses.replace(
                st, probe=_closed_stub(st.id),
                act=None if st.act is None else _forbidden_act(st.id)))
    return tuple(out)


def step_by_id(step_id: str):
    for st in steps.STEPS:
        if st.id == step_id:
            return st
    raise AssertionError(f"в STEPS нет шага {step_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Разбор StepResult
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# АДРЕСНОСТЬ «ЧТО СДЕЛАТЬ»: закрытый список признаков А1–А5 (§12.13 п.2)
#
# Список ЛИТЕРАЛЬНЫЙ: он переписан руками из таблицы §12.13, а не выведен из
# `chatter/connect/*` ([[jarvis-literal-lists-not-introspection]]). Выведенный
# согласился бы с реализацией по определению и промолчал бы ровно там, где она
# забыла. Поэтому каждый признак — отдельная функция со своим куском проверки и
# ссылкой на строку спеки: по коду сторожа видно, КАКОЙ пункт он держит.
#
# Одна регулярка на все пять была ДВУМЯ ЧИСЛАМИ НА ОДНУ ВЕЩЬ и уже разошлась с
# таблицей в обе стороны ([[jarvis-two-numbers-for-one-thing]]): пропускала
# `@BotFather` (такого признака в А1–А5 нет вовсе), не знала расширений кроме
# `.ps1`/`.py`, засчитывала голое имя скрипта без ключей вопреки А2 и не
# проверяла А5 ничем. Расширение списка — правка спеки И этого блока, а не
# решение автора текста на месте.
#
# Проверяется АДРЕСНОСТЬ, а не формулировка (§12.2, §12.13 п.4): сторож на
# буквы сообщения превратил бы правку текста в красное и был бы обойдён
# копипастой первой же строки.
# ─────────────────────────────────────────────────────────────────────────────

# ── А1 ── «путь к файлу или каталогу (разделитель `/`/`\` или расширение)»
#          образцы §12.13: chatter/clients/volska/settings.yaml, .secrets/,
#          state/drills/
#
# ОДНОГО СЛЕША НЕДОСТАТОЧНО, и это найдено состязательным замером (21.08):
# `allowlist/denylist`, `on/off`, `start/stop`, `500/503` — ASCII-токены со
# слешем, путями не являющиеся. Правило «разделитель внутри ASCII-токена»
# объявляло их адресом, то есть у сторожа был КАНАЛ ЛОЖНОГО ЗЕЛЁНОГО: автору
# текста хватило бы написать «выключи в allowlist/denylist», и остановка прошла
# бы, не назвав человеку ничего. Поэтому от токена требуется признак ИМЕНИ
# ФАЙЛА ИЛИ КАТАЛОГА — пять условий ниже, каждое отдельно и по имени.

# (1) РАСШИРЕНИЕ из литерального перечня. Он выписан из образцов §12.13 и из
# того, чем в этой арке вообще называют файлы; старая регулярка знала только
# `.ps1` и `.py`, и `settings.yaml` признаком не считала.
_A1_EXTENSIONS = (
    "yaml", "yml", "json", "jsonl", "py", "ps1", "log", "md", "txt",
    "enc", "env", "db", "session", "jrvbak", "xlsx", "toml", "ini",
    "csv", "exe",
)
_A1_EXTENSION = re.compile(r"\.(?:" + "|".join(_A1_EXTENSIONS) + r")\b")

# (2) БУКВА ДИСКА: `C:\jarvis\...`, `C:/jarvis/...`. В прозе не встречается.
_A1_DRIVE = re.compile(r"\b[A-Za-z]:[/\\]")

# (3) ОБРАТНЫЙ СЛЕШ между сегментами: `.\scripts\add_secret.ps1`. Русский текст
# обратным слешем «либо» не заменяет — в отличие от прямого.
_A1_BACKSLASH = re.compile(r"[A-Za-z0-9_.\-]\\[A-Za-z0-9_.\-]")

# (4) НАЧАЛО ОТ ИЗВЕСТНОГО КОРНЯ репозитория. Перечень литеральный: `chatter/`
# это путь, `allowlist/` — нет, и различить их может только список, а не форма.
_A1_ROOTS = ("chatter/", "scripts/", "state/", "logs/", "build/", "docs/",
             "tests/", ".secrets/", ".venv/")

# ПУНКТА «ТРИ И БОЛЕЕ СЕГМЕНТА» ЗДЕСЬ НЕТ НАМЕРЕННО. Он был и снят тем же
# замером: `on/off/auto`, `yes/no/maybe`, `start/stop/restart` — проза, а не
# пути, и правило по одной лишь ФОРМЕ токена их не отличит. Ни один путь
# планового кода на этот пункт не опирался: все начинаются от известного
# корня, несут расширение, букву диска или обратный слеш (замер: 30 из 30).


def a1_path(todo: str, slug: str = SLUG) -> bool:
    """А1: назван путь к файлу или каталогу.

    Четыре условия читаются подряд и по отдельности намеренно: одна длинная
    регулярка на всё — ровно та вещь, которую следующий автор не разберёт и
    поправит наугад.
    """
    return (bool(_A1_EXTENSION.search(todo))
            or bool(_A1_DRIVE.search(todo))
            or bool(_A1_BACKSLASH.search(todo))
            or any(root in todo for root in _A1_ROOTS))


# ── А2 ── «имя скрипта или команды ВМЕСТЕ хотя бы с одним ключом/аргументом»
#          образцы §12.13: chatter_client.ps1 -Action start -Slug volska,
#          Start-ScheduledTask -TaskName JarvisChatterGuardian,
#          registry_cli disable --slug volska
#
# ГОЛОЕ ИМЯ ПРИЗНАКОМ НЕ ЯВЛЯЕТСЯ. §12.13 п.3 бракует ровно такой текст: «имя
# это существительное, а не команда», человеку остаётся вспомнить, чем таск
# поднимают. Поэтому А2 требует ключа; форма самого имени — закрытый перечень
# из образцов спеки.
_A2_NAME = (
    r"(?:"
    r"[A-Z][a-z]+-[A-Z][A-Za-z]+"            # Verb-Noun: Start-ScheduledTask
    r"|[A-Za-z0-9_.\-]+\.(?:ps1|py)"         # скрипт: chatter_client.ps1
    r"|[a-z][a-z0-9]*(?:_[a-z0-9]+)+"        # snake_case CLI: registry_cli
    r"|python|pwsh|powershell(?:\.exe)?|pytest|git"   # известные раннеры
    r")"
)
_A2_COMMAND_WITH_KEY = re.compile(
    _A2_NAME
    + r"(?:\s+[A-Za-z0-9_.\-]+)*"            # подкоманды/позиционные: `disable`
    + r"\s+-{1,2}[A-Za-z]"                   # ...И ХОТЯ БЫ ОДИН КЛЮЧ
)


def a2_command_with_key(todo: str, slug: str = SLUG) -> bool:
    """А2: названа команда/скрипт И хотя бы один её ключ."""
    return bool(_A2_COMMAND_WITH_KEY.search(todo))


# ── А3 ── «флаг самой команды подключения»
#          образцы §12.13: --drill-yes, --drill-again, --root
#
# Список литеральный, а не собранный из argparse: собранный согласился бы с
# `__main__.py` по определению и промолчал бы, если флаг там переименуют.
_A3_CONNECT_FLAGS = ("--plan", "--drill-yes", "--drill-again", "--root")


def a3_connect_flag(todo: str, slug: str = SLUG) -> bool:
    """А3: назван флаг самой команды подключения."""
    return any(flag in todo for flag in _A3_CONNECT_FLAGS)


# ── А4 ── «слаг клиента — конкретный, из `ctx.slug` или реестра»
#          образцы §12.13: volska, yarina
#
# Регистр не важен: `CHATTER_CONTROL_BOT_TOKEN_YARINA` называет клиента ничуть
# не хуже, чем `yarina`.
def a4_client_slug(todo: str, slug: str = SLUG) -> bool:
    """А4: назван конкретный слаг клиента."""
    return bool(slug) and slug.lower() in todo.lower()


# ── А5 ── «слеш-команда пульта»
#          образцы §12.13: /funnel_gate on confirm, /clients
#
# Слеш-команда НЕ МОЖЕТ СТОЯТЬ ВНУТРИ ПУТИ, и это второе, что нашёл замер
# 21.08: на строке `впиши руками в C:/jarvis/chatter/clients/yarina/
# settings.yaml` зажигался А5 — лукбихайнд не знал двоеточия и принимал
# `/jarvis` за команду пульта. Вердикт от этого не менялся (горели А1 и А4), но
# признак, который врёт, попадает в покрытие, и по нему потом решают, что А5
# проверен. Поэтому: слеш обязан ОТКРЫВАТЬ токен (начало строки, пробел,
# скобка, кавычка), а за именем не должно идти ни ещё одного слеша, ни точки,
# ни буквы — иначе это путь, а не команда.
_A5_PULT_COMMAND = re.compile(
    r"(?:^|(?<=[\s(\[«\"']))"        # слеш ОТКРЫВАЕТ токен
    r"/[a-z][a-z0-9_]*"              # /funnel_gate, /clients, /allow
    r"(?![\w/\\.])"                  # ...и это не первый сегмент пути
)


def a5_pult_command(todo: str, slug: str = SLUG) -> bool:
    """А5: названа слеш-команда пульта."""
    return bool(_A5_PULT_COMMAND.search(todo))


# Таблица §12.13 п.2 целиком, в её порядке. Ровно пять пунктов.
ADDRESS_FEATURES: tuple[tuple[str, str, object], ...] = (
    ("А1", "путь к файлу или каталогу (разделитель или расширение)", a1_path),
    ("А2", "имя скрипта/команды вместе хотя бы с одним ключом", a2_command_with_key),
    ("А3", "флаг самой команды подключения", a3_connect_flag),
    ("А4", "слаг клиента", a4_client_slug),
    ("А5", "слеш-команда пульта", a5_pult_command),
)


def address_features(todo: str, slug: str = SLUG) -> tuple[str, ...]:
    """Какие именно пункты А1–А5 нашлись в тексте. Пусто — текст неадресен."""
    return tuple(fid for fid, _title, check in ADDRESS_FEATURES
                 if check(todo, slug))


def is_addressed(todo: str, slug: str = SLUG) -> bool:
    """«Адресность» по §12.4: назван хотя бы один токен из закрытого списка
    §12.13 — путь, команда с ключом, флаг подключения, слаг или пульт-команда.

    Это НЕ проверка формулировки (§12.2 её запрещает) — это проверка, что
    человеку названо КУДА идти, а не «исправьте конфигурацию». Непустоты
    недостаточно: «убедись, что таск живёт, и подожди цикл» грамматически
    «что сделать», а фактически совет, отправляющий человека в мануал."""
    return bool(address_features(todo, slug))


def text_blob(r: StepResult) -> str:
    return f"{r.why}\n{r.todo}\n{r.facts!r}"


def probe_all(step_list, ctx: Ctx) -> list[StepResult]:
    return [st.probe(ctx) for st in step_list]


def snapshot(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Миры для структурного перебора (Д11)
# ─────────────────────────────────────────────────────────────────────────────

def world_empty(tmp_path: Path) -> Path:
    """Голый корень: ничего нет вообще. Первый запуск на новой машине."""
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    return root


def world_skeleton(tmp_path: Path) -> Path:
    """Каталоги есть, фактов нет. Так выглядит репозиторий до подключения."""
    root = make_root(tmp_path)
    write_registry(root, {})
    return root


def world_partial(tmp_path: Path) -> Path:
    """Сборка есть, конфиг перенесён — дальше не сделано ничего."""
    root = make_root(tmp_path)
    write_build(root)
    install_live_client(root)
    write_registry(root, {NEIGHBOUR_SLUG: registry_entry(NEIGHBOUR_SLUG, enabled=True)})
    write_journal(root, extra=f"вписать id {DECOY_IN_JOURNAL} — НЕ вписывать автоматом")
    return root


def world_contradictory(tmp_path: Path) -> Path:
    """Факты спорят: реестр включён, конфига и сессии нет, маркер о чужом слаге,
    согласие датировано будущим, живость в `starting`."""
    root = make_root(tmp_path)
    write_registry(root, {SLUG: registry_entry(SLUG, enabled=True)})
    write_bundle_marker(root, bundle_line(NEIGHBOUR_SLUG, FOREIGN_SLUG))
    (root / "chatter" / "clients" / SLUG).mkdir(parents=True, exist_ok=True)
    write_consent(root, consent_text(
        datetime.fromtimestamp(NOW, tz=timezone.utc) + timedelta(days=40)))
    write_liveness(root, status="starting", heartbeat_age_s=4000.0)
    return root


def world_ready(tmp_path: Path) -> Path:
    """Всё, что подключение умеет создать, уже создано — кроме дрила.

    Мир нужен там, где сторож обязан дойти до ворот денег, а не остановиться
    раньше по постороннему поводу.
    """
    root = make_root(tmp_path)
    write_build(root)
    install_live_client(root, allowlist=[OWNER_CHAT_ID, sorted(CLIENT_DRILL_IDS)[0]])
    write_consent(root, consent_text())
    write_session(root)
    write_lead_session(root)
    write_lead_peers(root)
    write_bundle_marker(root, bundle_line(SLUG, NEIGHBOUR_SLUG))
    write_registry(root, {SLUG: registry_entry(SLUG, enabled=True),
                          NEIGHBOUR_SLUG: registry_entry(NEIGHBOUR_SLUG, enabled=True)})
    write_liveness(root)
    write_log(root)
    write_journal(root)
    return root


WORLD_BUILDERS = (world_empty, world_skeleton, world_partial,
                  world_contradictory, world_ready)


# ═════════════════════════════════════════════════════════════════════════════
# КОНТРАКТ §12.1 / §12.2: опора остальных сторожей
#
# Если STEPS окажется не тем, что описано в §3, все нижние сторожа проверят
# фантом. Поэтому опора зафиксирована ЛИТЕРАЛЬНО и отдельным тестом
# ([[jarvis-literal-lists-not-introspection]]): выведенный из реализации список
# согласился бы с ней по определению.
# ═════════════════════════════════════════════════════════════════════════════

def test_steps_are_the_sixteen_steps_of_the_map_in_order():
    assert tuple(st.id for st in steps.STEPS) == ALL_STEP_IDS


def test_exactly_six_steps_are_automatic_and_they_are_the_named_six():
    auto = tuple(st.id for st in steps.STEPS if st.owner == Owner.AUTO)
    assert auto == AUTO_STEPS


def test_the_tail_human_steps_are_named_literally(tmp_path):
    """§12.10 п.1: S14/S15 есть в карте (её читает человек в `--plan`), но в
    цикл не входят — иначе код 0 недостижим НИКОГДА и §4 расходится с §12.3.

    Константа литеральная и одна: вывести её из `owner == HUMAN` нельзя —
    человеческих шагов десять, а хвостовых ровно два.
    """
    assert steps.TAIL_HUMAN_IDS == TAIL_HUMAN_IDS
    ids = tuple(st.id for st in steps.STEPS)
    for sid in TAIL_HUMAN_IDS:
        assert sid in ids, f"{sid} пропал из карты — человеку нечего читать"
        assert step_by_id(sid).owner == Owner.HUMAN


def test_auto_is_equivalent_to_having_an_act():
    """Инвариант §3: AUTO ⇔ у шага есть `act`.

    Шаг без действия — человеческий по определению. Разъехавшись, эти два
    признака дают либо «автомат», который ничего не делает и всё равно не
    останавливается ради человека, либо человеческий шаг с автодействием —
    ровно тот `connect`, который однажды пересоберёт вычитанный конфиг.
    """
    for st in steps.STEPS:
        assert (st.owner == Owner.AUTO) == (st.act is not None), (
            f"{st.id}: owner={st.owner} act={st.act!r}")


def test_probe_and_act_names_match_the_contract():
    for i in range(16):
        assert hasattr(probes, f"probe_s{i}"), f"нет probes.probe_s{i} (§12.1)"
    for sid in AUTO_STEPS:
        name = f"act_s{sid[1:]}"
        assert hasattr(actions, name), f"нет actions.{name} (§12.1)"


@pytest.mark.parametrize("build_world", WORLD_BUILDERS,
                         ids=[w.__name__ for w in WORLD_BUILDERS])
@pytest.mark.parametrize("step_id", sorted(REQUIRED_FACTS))
def test_facts_carry_the_keys_the_contract_promised(tmp_path, build_world, step_id):
    """§12.6 п.2: `facts` — единственное, что сторожа читают для тонких
    утверждений, значит ключи обязаны быть, а не появляться по настроению шага.

    Проверяется во ВСЕХ мирах, включая пустой: улики нужнее всего там, где
    факта нет, — «`consent_path` только когда файл есть» превращает отсутствие
    улики в отсутствие проблемы.
    """
    root = build_world(tmp_path)
    r = step_by_id(step_id).probe(make_ctx(root))
    missing = [k for k in REQUIRED_FACTS[step_id] if k not in r.facts]
    assert not missing, f"{step_id}: в facts нет обязательных ключей {missing}"


# ═════════════════════════════════════════════════════════════════════════════
# Д11 — у КАЖДОЙ остановки непустое «что сделать», структурно
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("build_world", WORLD_BUILDERS,
                         ids=[w.__name__ for w in WORLD_BUILDERS])
def test_every_unclosed_step_says_what_to_do(tmp_path, build_world):
    """Перебор ВСЕХ шагов по нескольким мирам, а не три примера.

    Забывают ровно ту ветку, о которой не подумали: «каталога вообще нет»,
    «файл есть, но пуст», «факты спорят». Поэтому утверждение делается про
    каждый не-CLOSED результат каждого шага.
    """
    root = build_world(tmp_path)
    ctx = make_ctx(root)
    for st, r in zip(steps.STEPS, probe_all(steps.STEPS, ctx)):
        assert r.step_id == st.id, f"{st.id}: результат подписан {r.step_id!r}"
        assert isinstance(r.verdict, Verdict)
        if r.verdict == Verdict.CLOSED:
            continue
        assert r.why.strip(), f"{st.id}: пустое ПОЧЕМУ при вердикте {r.verdict}"
        assert r.todo.strip(), f"{st.id}: пустое ЧТО СДЕЛАТЬ при {r.verdict}"


@pytest.mark.parametrize("build_world", WORLD_BUILDERS,
                         ids=[w.__name__ for w in WORLD_BUILDERS])
def test_every_stop_names_where_to_go(tmp_path, build_world):
    """Адресность: в «что сделать» назван путь, скрипт, флаг или клиент.

    «Исправьте конфигурацию» непусто и бесполезно — это возврат к мануалу,
    ради ухода от которого арка и затевается.
    """
    root = build_world(tmp_path)
    ctx = make_ctx(root)
    for st, r in zip(steps.STEPS, probe_all(steps.STEPS, ctx)):
        if r.verdict == Verdict.CLOSED:
            continue
        assert is_addressed(r.todo), f"{st.id}: неадресное ЧТО СДЕЛАТЬ: {r.todo!r}"


@pytest.mark.parametrize("build_world", WORLD_BUILDERS,
                         ids=[w.__name__ for w in WORLD_BUILDERS])
def test_probes_write_nothing_at_all(tmp_path, build_world):
    """§12.5: `probes.py` не пишет на диск ни файла, ни строки журнала.

    Здесь это ещё и предусловие остальных сторожей: проба, меняющая мир,
    испортила бы перебор — каждый следующий шаг видел бы уже другой диск.
    """
    root = build_world(tmp_path)
    before = snapshot(root)
    probe_all(steps.STEPS, make_ctx(root))
    assert snapshot(root) == before


@pytest.mark.parametrize("build_world", WORLD_BUILDERS,
                         ids=[w.__name__ for w in WORLD_BUILDERS])
def test_no_step_result_ever_carries_the_secret_value(tmp_path, build_world):
    """§12.5 и Д5: доказываем ПРИЗНАКОМ, не значением.

    Токен лежит в `ctx.env`. Вердикт про него выносится — а сам он не имеет
    права появиться ни в `why`, ни в `todo`, ни в `facts`: всё это едет в
    вывод и в журнал, а журнал живёт в `state/` рядом с репозиторием.
    """
    root = build_world(tmp_path)
    ctx = make_ctx(root, env=env_material())
    for st, r in zip(steps.STEPS, probe_all(steps.STEPS, ctx)):
        blob = text_blob(r)
        assert FAKE_TOKEN not in blob, f"{st.id}: значение токена в выводе"
        assert FAKE_TOKEN.split(":", 1)[1] not in blob, f"{st.id}: хвост токена в выводе"
        assert ctx.env["TELEGRAM_API_HASH"] not in blob, f"{st.id}: api_hash в выводе"
        assert ctx.env["ANTHROPIC_API_KEY"] not in blob, f"{st.id}: ключ Anthropic в выводе"


def test_a_human_stop_is_reported_as_waiting_not_as_broken(tmp_path):
    """§4: код 3 «ЖДЁТ ЧЕЛОВЕКА» отделён от 1 «ПРОТИВОРЕЧИЕ».

    Слипшись, они приучают не смотреть на красное — а красное здесь означает
    «подключение сломано», и на него обязана быть другая реакция.
    """
    root = world_skeleton(tmp_path)
    write_build(root)
    install_live_client(root)
    w = walk(isolate("S5"), make_ctx(root))           # согласия нет
    assert w.rc == 3, f"остановка на человеческом шаге вернула {w.rc}"
    assert w.stop is not None and w.stop.step_id == "S5"
    assert w.stop.todo.strip()


# ═════════════════════════════════════════════════════════════════════════════
# «ФАКТА НЕТ» — ЭТО НЕ «ФАКТЫ СПОРЯТ»: тройка S4 (§12.8 п.2) на ВСЕХ шагах
#
# Разбор владельца 21.08 (не переобсуждается): `--plan` — ПЕРВЫЙ взгляд
# человека на инструмент, и четыре красных в первом же запуске учат не
# смотреть на красное. Это ровно то, против чего решался q2: «жду тебя» и
# «сломано» слипшись приучают не смотреть на красное — а слипание в обратную
# сторону («жду тебя», напечатанное как «сломано») стоит того же.
#
# Тройка S4 из §12.8 п.2 — «каталога нет → НЕ ЗАКРЫТ · есть и грузится →
# ЗАКРЫТ · есть и НЕ грузится → ПРОТИВОРЕЧИЕ» — не свойство S4, а общее
# правило: ПРОТИВОРЕЧИЕ означает «факты спорят ИЛИ факт нечитаем», и ни одно
# из двух не описывает факт, которого просто ещё нет.
#
# Сверка идёт в ОБЕ стороны, иначе починка выродится в «всё объявить не
# закрытым»: мир «ничего не делали» не имеет права дать ни одного CONFLICT, а
# мир «факты спорят» обязан дать их ровно там, где давал.
# ═════════════════════════════════════════════════════════════════════════════

#: Миры, в которых НИЧЕГО ещё не делали: голая машина и репозиторий до
#: подключения. Литерально, а не «все, кроме...»: список миров ещё вырастет.
VIRGIN_WORLDS = (world_empty, world_skeleton)

#: Шаги, читающие ЖИВОЙ КОНФИГ клиента, которого до S4 не существует.
#: Литеральный список: выведенный из кода согласился бы с ним по определению
#: ([[jarvis-literal-lists-not-introspection]]).
CONFIG_READING_STEPS = ("S4", "S7", "S9", "S15")


@pytest.mark.parametrize("build_world", VIRGIN_WORLDS,
                         ids=[w.__name__ for w in VIRGIN_WORLDS])
def test_nothing_done_yet_is_never_a_contradiction(tmp_path, build_world):
    """До первого шага спорить нечему: все вердикты — «не закрыт».

    Проверяется ПЕРЕБОРОМ всех шагов, а не на четырёх известных: забывают
    ровно ту ветку, о которой не подумали.
    """
    root = build_world(tmp_path)
    guilty = [(r.step_id, r.why)
              for r in probe_all(steps.STEPS, make_ctx(root))
              if r.verdict == Verdict.CONFLICT]
    assert not guilty, (
        "в мире, где ещё ничего не делали, «факты спорят» невозможно — "
        "напечатано «сломано» там, где ждут человека:\n"
        + "\n".join(f"  {sid}: {why}" for sid, why in guilty))


def test_facts_that_really_do_contradict_stay_contradictions(tmp_path):
    """Обратная сторона той же тройки, без которой первый сторож зеленеет от
    «объявим всё не закрытым».

    `world_contradictory`: каталог клиента ЕСТЬ и не грузится, реестр включает
    клиента, которого не поднять. Это не «шаг предстоит» — это правка чужой
    руки, и разбирать её человеку.
    """
    root = world_contradictory(tmp_path)
    verdicts = {r.step_id: r for r in probe_all(steps.STEPS, make_ctx(root))}
    for sid in CONFIG_READING_STEPS + ("S11",):
        assert verdicts[sid].verdict == Verdict.CONFLICT, (
            f"{sid}: факты спорят, а вердикт «{verdicts[sid].verdict.value}» — "
            f"починка ложных красных погасила настоящие: {verdicts[sid].why}")


@pytest.mark.parametrize("step_id", CONFIG_READING_STEPS)
def test_a_missing_client_dir_is_open_and_an_unloadable_one_is_a_conflict(
        tmp_path, step_id):
    """Два мира, различающиеся РОВНО одним: каталог клиента есть или нет.

    Это и есть предмет: «каталога нет» — след того, что S4 ещё не исполнялся,
    и он у КАЖДОГО шага, который этот каталог читает; «каталог есть и не
    грузится» — след человеческой правки, и он у них же.
    """
    absent = world_skeleton(tmp_path / "absent")
    broken = world_skeleton(tmp_path / "broken")
    (broken / "chatter" / "clients" / SLUG).mkdir(parents=True, exist_ok=True)

    r_absent = step_by_id(step_id).probe(make_ctx(absent))
    r_broken = step_by_id(step_id).probe(make_ctx(broken))

    assert r_absent.verdict == Verdict.OPEN, (
        f"{step_id}: каталога клиента нет вовсе — это «шаг предстоит», а не "
        f"«факты спорят»: {r_absent.why}")
    assert r_absent.todo.strip() and is_addressed(r_absent.todo), (
        f"{step_id}: неадресное ЧТО СДЕЛАТЬ: {r_absent.todo!r}")
    assert r_broken.verdict == Verdict.CONFLICT, (
        f"{step_id}: каталог есть и не грузится — это правка человека, "
        f"перезаписывать её нельзя: {r_broken.why}")


def test_a_missing_registry_entry_is_open_and_a_broken_enabled_one_is_a_conflict(
        tmp_path):
    """S11 читает не конфиг, а реестр, и тройка у него та же.

    Записи нет — её заводит S10, автоматический шаг: человеку сообщать не о
    чем, кроме того, где она появится. Запись есть, клиент включён и подняться
    не может — гардиан крутит «поднял — упал» каждые ~30 с, и это ПРОТИВОРЕЧИЕ.
    """
    absent = world_skeleton(tmp_path / "absent")
    broken = world_skeleton(tmp_path / "broken")
    write_registry(broken, {SLUG: registry_entry(SLUG, enabled=True)})

    r_absent = probes.probe_s11(make_ctx(absent))
    r_broken = probes.probe_s11(make_ctx(broken))

    assert r_absent.verdict == Verdict.OPEN, (
        f"записи в реестре ещё нет — её создаёт S10, спорить нечему: "
        f"{r_absent.why}")
    assert r_absent.facts.get("entry_present") is False, (
        f"улика не говорит, что записи нет: {r_absent.facts!r}")
    assert r_absent.todo.strip() and is_addressed(r_absent.todo), (
        f"неадресное ЧТО СДЕЛАТЬ: {r_absent.todo!r}")
    assert r_broken.verdict == Verdict.CONFLICT, (
        f"включён и не поднимается — это спор фактов: {r_broken.why}")


# ═════════════════════════════════════════════════════════════════════════════
# ВНЕШНИЙ ВЫЗОВ ИДЁТ ИЗ ДЕРЕВА КОДА, А НЕ ИЗ КОРНЯ ДАННЫХ
#
# Найдено РЕПЕТИЦИЕЙ §9.1 (21.08, вариант A), а не чтением: в песочнице S2 не
# закрывался никогда. `--check` запускается как `python -m chatter.onboard`, а
# cwd внешнего вызова был `ctx.root` — корень ДАННЫХ, где пакета `chatter` нет
# вовсе. Дочерний процесс падал на импорте и отдавал rc 1, а человеку
# печаталось «метка стоит, а автоприёмка красная»: его посылали чинить
# содержимое, которое зелёное (та же команда из дерева кода даёт rc 0).
#
# Правило уже записано в `model.script_path` и просто не доехало до cwd:
# ДАННЫЕ читаем откуда сказали (`--root`, `-Root`, путь аргументом), КОД
# исполняем только свой. Сторож структурный: перебором всех вызовов, а не на
# примере `--check`, — иначе следующий вызов заведут снова из `ctx.root`.
# ═════════════════════════════════════════════════════════════════════════════

def test_no_external_call_runs_from_the_data_root(tmp_path):
    """Каждый внешний вызов — из дерева МОДУЛЯ; корень данных едет аргументом."""
    root = world_ready(tmp_path)
    runner = FakeRunner(default_replies())
    ctx = make_ctx(root, runner=runner, env=env_material())

    probe_all(steps.STEPS, ctx)
    actions.act_s11(ctx)

    assert runner.calls, "ни одного внешнего вызова — сторож проверил бы пустоту"
    wrong = [(call.argv[0], str(call.cwd)) for call in runner.calls
             if Path(call.cwd or ".").resolve() != repo_tree().resolve()]
    assert not wrong, (
        "внешний вызов идёт из корня ДАННЫХ, а не из дерева кода — при "
        f"--root в песочнице дочерний процесс не найдёт пакет chatter: {wrong}")


def test_the_check_of_s2_is_run_from_the_tree_where_chatter_lives(tmp_path):
    """Прицельно про S2: именно он падал в репетиции.

    Отдельно от структурного сторожа выше, потому что цена у него своя: S2 —
    единственный шаг, который зовёт ПАКЕТ (`-m chatter.onboard`), а не файл по
    пути, и импорт у него разрешается из cwd.
    """
    root = world_ready(tmp_path)
    runner = FakeRunner(default_replies())
    probes.probe_s2(make_ctx(root, runner=runner))

    checks = [c for c in runner.calls if "chatter.onboard" in " ".join(c.argv)]
    assert checks, "автоприёмка не звалась вовсе"
    for call in checks:
        assert Path(call.cwd).resolve() == repo_tree().resolve(), (
            f"--check зовётся из {call.cwd}, а пакет chatter лежит в "
            f"{repo_tree()}: дочерний процесс упадёт на импорте и отдаст rc 1")
        assert str(root) in " ".join(call.argv), (
            "каталог сборки обязан ехать АРГУМЕНТОМ, раз cwd больше не он")


# ═════════════════════════════════════════════════════════════════════════════
# КОД БЕРЁТСЯ ОТ ДЕРЕВА МОДУЛЯ ДАЖЕ ЧЕРЕЗ ЧУЖОЙ СКРИПТ
#
# Вторая находка репетиции §9.1 (21.08). `scripts/reencrypt_env.ps1` — тонкая
# обёртка, и её `-Root` означает КОРЕНЬ РЕПОЗИТОРИЯ: оттуда она берёт и
# интерпретатор (`<Root>\.venv`), и сам код (`<Root>\scripts\reencrypt_env.py`).
# Проба S7 передавала туда `ctx.root` — корень ДАННЫХ. В песочнице обёртка
# падала на «venv не на месте» ДО того, как посмотреть на `.env`, отдавала rc 1
# без строки статуса, и S7 печатал ПРОТИВОРЕЧИЕ «ответ инструмента не понят»
# там, где правда — «заведи .env» (прямой прогон .py той же командой:
# «статус до: no_env»).
#
# Это тот же разбор, что уже записан в `model.script_path`, доведённый до
# конца: ключ «где лежат данные» не имеет права решать, «какой код исполнить».
# Поэтому машина зовёт `scripts/reencrypt_env.py` НАПРЯМУЮ — код от дерева
# модуля, данные аргументом `--root`; человеку в «что сделать» по-прежнему
# называется обёртка, он стоит в репозитории.
# ═════════════════════════════════════════════════════════════════════════════

def test_s7_runs_the_reencrypt_code_from_the_module_tree(tmp_path):
    """Код — от дерева модуля, корень данных — аргументом.

    Мир берётся `_token_world`, а не `world_ready`: во втором включённый сосед
    стоит в реестре БЕЗ конфига, и проба честно останавливается раньше — на
    «конфиги включённых соседей не читаются». Сторож на нём был бы зелёным по
    другой причине, ровно как тот, что мутационный гейт поймал слепым 21.08.
    """
    root = _token_world(tmp_path, token_env=PER_CLIENT_ENV,
                        neighbour_env=NEIGHBOUR_ENV, neighbour_enabled=True)
    runner = FakeRunner(default_replies())
    probes.probe_s7(make_ctx(root, runner=runner, env=env_material()))

    calls = [c for c in runner.calls if "reencrypt_env" in " ".join(c.argv)]
    assert calls, "проверка .env.enc не звалась вовсе"
    for call in calls:
        argv = list(call.argv)
        named = [a for a in argv if "reencrypt_env" in a]
        assert named, argv
        for a in named:
            assert Path(a).resolve() == (repo_tree() / "scripts" /
                                         Path(a).name).resolve(), (
                f"код взят не от дерева модуля: {a}")
        assert str(root) not in " ".join(
            a for a in argv if "reencrypt_env" in a or a.endswith("python.exe")), (
            "корень ДАННЫХ уехал в путь к КОДУ или к интерпретатору")
        assert str(root) in argv, (
            "корень данных обязан ехать аргументом --root, иначе проверялся бы "
            "чужой .env")


def test_the_live_runner_forces_utf8_on_children(tmp_path):
    """Дочерний процесс обязан писать UTF-8, иначе разбор его вывода — лотерея.

    Проба S7 достаёт «статус до: …» регуляркой из stdout, а `probe_s2` читает
    вывод автоприёмки. Кириллица, написанная в cp1251 и прочитанная как UTF-8,
    превращается в мусор — и вердикт становится «ответ не понят» на исправном
    инструменте. Раньше `PYTHONUTF8=1` ставила PowerShell-обёртка; прямой вызов
    её не наследует, значит его ставит тот, кто рождает процесс.
    """
    from chatter.connect.__main__ import SubprocessRunner

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os\nprint(os.environ.get('PYTHONUTF8', 'НЕТ'))\n",
        encoding="utf-8")
    res = SubprocessRunner().run([sys.executable, str(probe)],
                                 cwd=tmp_path, timeout=60.0)
    assert res.rc == 0, res.stderr
    assert res.stdout.strip() == "1", (
        f"дочерний процесс не получил PYTHONUTF8=1: {res.stdout!r}")


# ═════════════════════════════════════════════════════════════════════════════
# Д15 — согласие клиента (S5, §5.7, решение владельца q6)
# ═════════════════════════════════════════════════════════════════════════════

def test_consent_missing_is_a_stop(tmp_path):
    root = world_skeleton(tmp_path)
    install_live_client(root)
    r = probes.probe_s5(make_ctx(root))
    assert r.verdict != Verdict.CLOSED
    assert r.todo.strip() and is_addressed(r.todo)
    assert str(r.facts.get("consent_path", "")).endswith(f"{SLUG}.consent.md"), (
        f"улика не называет файл согласия (§12.8 п.3): {r.facts!r}")
    assert r.facts.get("consent_date") is None


@pytest.mark.parametrize("body, case", [
    ("", "пустой файл"),
    ("   \n\n\t\n", "только пробелы"),
    ("Согласие получено от владелицы, голосовое в Telegram.\n", "без даты"),
    ("Согласие получено 32.13.2026 от владелицы.\n", "дата не разбирается"),
])
def test_consent_without_a_parsable_date_is_a_stop(tmp_path, body, case):
    """§3/S5: «файл есть, непуст, дата разбирается и не в будущем».

    Пустой файл — самый дешёвый способ закрыть шаг, не поговорив с человеком:
    `touch CONSENT.md` занимает секунду и выглядит как факт.
    """
    root = world_skeleton(tmp_path)
    install_live_client(root)
    write_consent(root, body)
    r = probes.probe_s5(make_ctx(root))
    assert r.verdict != Verdict.CLOSED, f"{case}: шаг закрыт"
    assert r.todo.strip(), f"{case}: нечего делать"
    assert r.facts.get("consent_date") is None, (
        f"{case}: улика утверждает, что дата разобрана: {r.facts!r}")


def test_consent_dated_in_the_future_is_a_stop(tmp_path):
    """Дата из будущего — либо опечатка, либо заполнено «наперёд»; и то и другое
    означает, что разговора с человеком ещё не было."""
    root = world_skeleton(tmp_path)
    install_live_client(root)
    write_consent(root, consent_text(
        datetime.fromtimestamp(NOW, tz=timezone.utc) + timedelta(days=10)))
    r = probes.probe_s5(make_ctx(root))
    assert r.verdict != Verdict.CLOSED
    assert r.todo.strip()


def test_consent_with_a_date_and_a_source_closes_the_step(tmp_path):
    """Контрапозиция: без неё все ассерты выше прошли бы у пробы, которая
    просто всегда говорит «не закрыт»."""
    root = world_skeleton(tmp_path)
    install_live_client(root)
    write_consent(root, consent_text())
    r = probes.probe_s5(make_ctx(root))
    assert r.verdict == Verdict.CLOSED, f"why={r.why!r} todo={r.todo!r}"
    assert r.facts.get("consent_date"), f"дата не попала в улики: {r.facts!r}"


def test_connection_does_not_reach_the_login_without_consent(tmp_path):
    """Д15 целиком: «без CONSENT.md подключение НЕ ДОХОДИТ ДО ЛОГИНА».

    Порядок здесь и есть предмет: логин создаёт `.session` — полный доступ к
    аккаунту. Спросить согласие после логина значит спросить после того, как
    доступ уже взят.
    """
    root = world_skeleton(tmp_path)
    write_build(root)
    install_live_client(root)
    seen: list[str] = []

    def spy_s6(_ctx: Ctx) -> StepResult:
        seen.append("S6")
        return StepResult(step_id="S6", verdict=Verdict.CLOSED,
                          why="стенд: логин объявлен пройденным",
                          todo="стенд: делать нечего", facts={})

    w = walk(isolate("S5", spies={"S6": spy_s6}), make_ctx(root))
    assert seen == [], "до логина дошли без согласия"
    assert w.stop is not None and w.stop.step_id == "S5"
    assert "S6" not in w.visited


# ═════════════════════════════════════════════════════════════════════════════
# Д16 — маркер бандла (S8, решение владельца q3, вариант «б»)
# ═════════════════════════════════════════════════════════════════════════════

def test_bundle_marker_missing_is_a_stop(tmp_path):
    root = world_skeleton(tmp_path)
    write_session(root)
    r = probes.probe_s8(make_ctx(root))
    assert r.verdict != Verdict.CLOSED
    assert r.todo.strip() and is_addressed(r.todo)
    # §12.12 п.2: маркера нет — строку никто не смотрел. `False` тут утверждало
    # бы «проверили, слаг не назван», и человек пошёл бы искать в маркере то,
    # чего в нём нет вовсе.
    assert r.facts.get("slug_named") is None


def test_bundle_marker_empty_is_a_stop(tmp_path):
    root = world_skeleton(tmp_path)
    write_session(root)
    write_bundle_marker(root, "")
    r = probes.probe_s8(make_ctx(root))
    assert r.verdict != Verdict.CLOSED
    assert r.todo.strip()


def test_bundle_marker_without_this_slug_is_a_stop(tmp_path):
    """Сердцевина Д16: бандл сняли, но НЕ с этой сессией.

    Вариант (а) — «напомнить и не проверять» — владелец отверг именно потому,
    что через месяц напоминание становится фоном. Маркер, в котором названы
    чужие слаги, — это тот самый фон, только записанный в файл.
    """
    root = world_skeleton(tmp_path)
    write_session(root)
    write_bundle_marker(root, bundle_line(NEIGHBOUR_SLUG, FOREIGN_SLUG))
    r = probes.probe_s8(make_ctx(root))
    assert r.verdict != Verdict.CLOSED, f"чужой бандл принят за свой: {r.why!r}"
    assert r.todo.strip()
    assert r.facts.get("slug_named") is False, (
        f"улика утверждает, что слаг назван: {r.facts!r}")


def test_bundle_marker_naming_this_slug_closes_the_step(tmp_path):
    root = world_skeleton(tmp_path)
    write_session(root)
    write_bundle_marker(root, bundle_line(SLUG, NEIGHBOUR_SLUG))
    r = probes.probe_s8(make_ctx(root))
    assert r.verdict == Verdict.CLOSED, f"why={r.why!r} todo={r.todo!r}"
    assert r.facts.get("slug_named") is True


@pytest.mark.parametrize("line_slugs, case", [
    ((SLUG, NEIGHBOUR_SLUG), "маркер про этого клиента"),
    ((NEIGHBOUR_SLUG,), "маркер про чужого клиента"),
])
def test_bundle_verdict_does_not_move_with_the_session_mtime(tmp_path, line_slugs, case):
    """Д16, вторая половина: вердикт НЕ зависит от mtime сессии.

    Спека отвергает сверку времён дважды (S5 и S8) по одной причине: `.session`
    переписывается Telethon при КАЖДОЙ записи, и после подъёма раннера (S12)
    сторож на времени краснел бы на здоровом подключении. Сторож, который
    меняет вердикт сам по себе, — это фон, а не проверка.

    Поэтому mtime двигается в обе стороны — далеко в будущее и далеко в
    прошлое, — и вердикт обязан остаться тем же самым.
    """
    root = world_skeleton(tmp_path)
    session = write_session(root)
    write_bundle_marker(root, bundle_line(*line_slugs))
    ctx = make_ctx(root)

    def take():
        r = probes.probe_s8(ctx)
        return (r.verdict, r.facts.get("slug_named"))

    first = take()

    os.utime(session, (NOW + 86_400 * 30, NOW + 86_400 * 30))
    after_future = take()

    os.utime(session, (NOW - 86_400 * 365, NOW - 86_400 * 365))
    after_past = take()

    assert first == after_future == after_past, (
        f"{case}: вердикт поехал за mtime сессии: "
        f"{first} -> {after_future} -> {after_past}")


# ═════════════════════════════════════════════════════════════════════════════
# Д5 — токен контрол-бота (S7, §2.1)
#
# Цена ошибки названа в спеке и в живом конфиге Ярины: два раннера с ОДНИМ
# именем переменной читают ОДИН токен и бьются за getUpdates. Telegram отдаёт
# long-poll ровно одному, второй получает 409, и команды пульта ходят через раз
# БЕЗ ЕДИНОЙ ОШИБКИ В ЛОГЕ. Отладка такого начинается с «мне кажется, бот
# тупит».
# ═════════════════════════════════════════════════════════════════════════════

def _token_world(tmp_path: Path, *, token_env: str | None,
                 neighbour_env: str | None = NEIGHBOUR_ENV,
                 neighbour_enabled: bool = True) -> Path:
    """Сосед устанавливается ВСЕГДА и с валидным конфигом.

    Включённый в реестре сосед без каталога — это ПРОТИВОРЕЧИЕ само по себе
    (проба честно говорит «конфиги включённых соседей не читаются»), и мир,
    построенный так, проверял бы уникальность имени токена на шаге, который
    краснеет по другой причине.
    """
    root = make_root(tmp_path)
    install_live_client(root, token_env=token_env)
    install_live_client(root, slug=NEIGHBOUR_SLUG,
                        token_env=neighbour_env or NEIGHBOUR_ENV)
    write_registry(root, {
        SLUG: registry_entry(SLUG),
        NEIGHBOUR_SLUG: registry_entry(NEIGHBOUR_SLUG, enabled=neighbour_enabled),
    })
    write_session(root)
    return root


def test_a_shared_token_env_name_is_a_stop(tmp_path):
    """Имя без слага — общее на всех. Значение при этом на месте и непусто:
    ловушка в том, что «токен есть» тут выглядит как «шаг закрыт»."""
    root = _token_world(tmp_path, token_env=SHARED_ENV)
    env = env_material(token_env=SHARED_ENV)
    r = probes.probe_s7(make_ctx(root, env=env))
    assert r.verdict != Verdict.CLOSED, f"общее имя принято: {r.why!r}"
    assert r.todo.strip() and is_addressed(r.todo)
    assert r.facts.get("env_name") == SHARED_ENV
    # `token_len` здесь НЕ утверждается: проба вправе закоротить на имени, и
    # тогда по §12.12 п.2 длина обязана быть None — «не проверяли». Признак
    # вместо значения проверяется там, где значение действительно смотрят.
    assert FAKE_TOKEN not in text_blob(r)


def test_a_foreign_per_client_token_env_name_is_a_stop(tmp_path):
    """ЧУЖОЕ пер-клиентское имя: суффикс есть, но он соседский.

    Останов обязателен, и он наступает РАНЬШЕ проверки уникальности — на
    «в имени нет нашего слага». Тест назван по тому, что проверяет: под
    прежним именем («совпадение с включённым») он был зелёным по другой
    причине и не увидел бы снятую проверку совпадения вовсе.
    """
    root = _token_world(tmp_path, token_env=NEIGHBOUR_ENV,
                        neighbour_env=NEIGHBOUR_ENV, neighbour_enabled=True)
    env = env_material(token_env=NEIGHBOUR_ENV)
    r = probes.probe_s7(make_ctx(root, env=env))
    assert r.verdict != Verdict.CLOSED, f"чужое имя принято: {r.why!r}"
    assert r.todo.strip() and is_addressed(r.todo)
    assert SLUG.upper() not in (r.facts.get("env_name") or "").upper(), (
        f"мир построен не про то: имя {r.facts.get('env_name')!r} наше")


def test_a_token_env_name_shared_with_an_enabled_client_is_a_stop(tmp_path):
    """Имя НАШЕ и пер-клиентское — и его уже читает ВКЛЮЧЁННЫЙ сосед.

    Так дефект выглядит в жизни: конфиг скопировали вместе с именем
    переменной. Это тот же 409, только замаскированный — суффикс на месте,
    уникальности нет, и в логе тишина.

    Остановка обязана НАЗЫВАТЬ соседа: без имени человек в три ночи не найдёт,
    кто держит ту же переменную, а `--drill-yes` он уже набрал.
    """
    root = _token_world(tmp_path, token_env=PER_CLIENT_ENV,
                        neighbour_env=PER_CLIENT_ENV, neighbour_enabled=True)
    env = env_material(token_env=PER_CLIENT_ENV)
    r = probes.probe_s7(make_ctx(root, env=env))
    assert r.facts.get("env_name") == PER_CLIENT_ENV, (
        f"мир построен не про то: имя {r.facts.get('env_name')!r} не наше — "
        f"проба остановится раньше, на «имя не пер-клиентское»")
    assert r.verdict != Verdict.CLOSED, f"совпадение с включённым принято: {r.why!r}"
    assert r.todo.strip() and is_addressed(r.todo)
    assert NEIGHBOUR_SLUG in f"{r.why}\n{r.todo}", (
        f"сосед, держащий ту же переменную, не назван: {r.why!r} / {r.todo!r}")


def test_an_empty_token_value_is_a_stop(tmp_path):
    """Имя своё и уникальное, но переменной в материале нет.

    Это ловушка Ярины 16.08 наоборот: имя правильное, доехать нечему.
    """
    root = _token_world(tmp_path, token_env=PER_CLIENT_ENV)
    env = env_material(token_value=None)
    r = probes.probe_s7(make_ctx(root, env=env))
    assert r.verdict != Verdict.CLOSED
    assert r.todo.strip() and is_addressed(r.todo)
    assert r.facts.get("env_name") == PER_CLIENT_ENV
    assert r.facts.get("token_len") == 0, (
        f"переменной нет, а длина ненулевая: {r.facts!r}")


def test_the_probe_actually_reacts_to_the_env_name(tmp_path):
    """Два мира, различающиеся ТОЛЬКО именем переменной.

    Без этого сторожа проба, всегда отвечающая «не закрыт», прошла бы все три
    ассерта выше и не проверяла бы ничего.
    """
    shared_root = _token_world(tmp_path / "shared", token_env=SHARED_ENV)
    unique_root = _token_world(tmp_path / "unique", token_env=PER_CLIENT_ENV)
    shared = probes.probe_s7(make_ctx(
        shared_root, env=env_material(token_env=SHARED_ENV)))
    unique = probes.probe_s7(make_ctx(
        unique_root, env=env_material(token_env=PER_CLIENT_ENV)))
    assert shared.facts.get("env_name") == SHARED_ENV
    assert unique.facts.get("env_name") == PER_CLIENT_ENV
    assert (shared.verdict, shared.facts) != (unique.verdict, unique.facts), (
        "имя переменной не влияет ни на вердикт, ни на улики")


def test_a_unique_per_client_token_in_sync_closes_the_step(tmp_path):
    """Контрапозиция Д5: здоровый случай обязан закрываться.

    `-Check` = `in_sync` приходит подставным раннером — живой `reencrypt_env.ps1`
    сторожу недоступен и не нужен (§12.2).
    """
    root = _token_world(tmp_path, token_env=PER_CLIENT_ENV,
                        neighbour_env=NEIGHBOUR_ENV, neighbour_enabled=True)
    runner = FakeRunner(default_replies())
    r = probes.probe_s7(make_ctx(root, env=env_material(neighbour_token=True),
                                 runner=runner))
    assert r.verdict == Verdict.CLOSED, f"why={r.why!r} todo={r.todo!r}"
    assert r.facts.get("env_enc_state") == "in_sync", (
        f"состояние .env.enc не доказано: {r.facts!r}")
    assert FAKE_TOKEN not in text_blob(r)


def test_token_evidence_is_a_sign_not_the_value(tmp_path):
    """§12.5 прицельно: улика — «переменная непуста, длина 46», а не сама строка.

    `token_len` в §12.6 назван иллюстрацией правила, а не исключением из него:
    длина отвечает на вопрос «доехало ли» и не отвечает ни на один вопрос,
    который интересует того, кто читает чужой журнал.
    """
    root = _token_world(tmp_path, token_env=PER_CLIENT_ENV)
    r = probes.probe_s7(make_ctx(root, env=env_material()))
    blob = text_blob(r)
    assert r.facts.get("token_len") == len(FAKE_TOKEN)
    assert FAKE_TOKEN not in blob
    assert FAKE_TOKEN.split(":", 1)[1] not in blob


@pytest.mark.parametrize("state", ["no_env", "no_enc", "unreadable", "stale"])
def test_only_in_sync_closes_the_token_step(tmp_path, state):
    """§12.7 п.6: алфавит закрыт пятью значениями, закрывает шаг одно.

    `stale` опаснее прочих: это РАБОЧЕЕ состояние машины, и на глаз оно выглядит
    как «всё хорошо». Но `bootstrap_env` при наличии `.enc` читает ТОЛЬКО его —
    токен, записанный в plaintext `.env`, до процесса не доедет. Ровно эта
    ловушка съела вечер 16.08 на Ярине.
    """
    root = _token_world(tmp_path, token_env=PER_CLIENT_ENV)
    runner = FakeRunner({**default_replies(), "reencrypt_env": enc_reply(state)})
    r = probes.probe_s7(make_ctx(root, env=env_material(), runner=runner))
    assert r.verdict != Verdict.CLOSED, f"{state} закрыл шаг"
    assert r.facts.get("env_enc_state") == state
    assert r.todo.strip() and is_addressed(r.todo)


def test_an_enc_state_outside_the_alphabet_is_a_conflict(tmp_path):
    """§12.12 п.3: значение вне алфавита означает, что мы не поняли ответ
    инструмента, — это «факты спорят», а не «шаг предстоит».

    Разница дорогая: «не закрыт» отправляет человека доделывать шаг, который на
    самом деле неизвестно в каком состоянии.
    """
    root = _token_world(tmp_path, token_env=PER_CLIENT_ENV)
    runner = FakeRunner({**default_replies(), "reencrypt_env": enc_reply("unknown")})
    r = probes.probe_s7(make_ctx(root, env=env_material(), runner=runner))
    assert r.verdict == Verdict.CONFLICT, f"вердикт {r.verdict}: {r.why!r}"
    assert r.todo.strip()


# ═════════════════════════════════════════════════════════════════════════════
# Д6 — allowlist (S9, §3)
#
# При `funnel_gate: false` allowlist — ЕДИНСТВЕННЫЙ источник допуска
# (`chatter/core/admission.py`). Лишний id здесь означает, что бот начал
# отвечать живому человеку, которого никто не звал; недостающий владелец — что
# пульт не отвечает никому.
# ═════════════════════════════════════════════════════════════════════════════

def _allowlist_world(tmp_path: Path, *, owner: int | None = OWNER_CHAT_ID) -> Path:
    root = make_root(tmp_path)
    write_build(root)                       # в отчёте лежит приманка-id
    install_live_client(root, owner_chat_id=owner, allowlist=[])
    write_registry(root, {SLUG: registry_entry(SLUG)})
    write_consent(root, consent_text())
    write_session(root)
    write_bundle_marker(root, bundle_line(SLUG))
    write_journal(root, extra=f"клиент прислал id {DECOY_IN_JOURNAL}")
    return root


def test_act_s9_writes_only_the_owner_and_a_drill_contact(tmp_path):
    """Д6 целиком: произвольный id не вписывается автоматом.

    В мире разложены три приманки — id в отчёте сборки (ссылки клиента на свои
    аккаунты из брифа Q8), id в журнале и id в окружении. Каждая из них
    выглядит как «ну вот же нужный id».
    """
    root = _allowlist_world(tmp_path)
    ctx = make_ctx(root, env=env_material())
    actions.act_s9(ctx)

    written = live_allowlist(root)
    assert written is not None, "allowlist не записан вовсе"
    assert set(written) & DECOYS == set(), (
        f"в allowlist уехал посторонний id: {sorted(set(written) & DECOYS)}")
    assert set(written) <= ({OWNER_CHAT_ID} | CLIENT_DRILL_IDS), (
        f"в allowlist есть id вне «владелец + дрил-контакт»: {written}")
    assert OWNER_CHAT_ID in written, "владельца нет в allowlist"

    after = probes.probe_s9(ctx)
    assert after.facts.get("owner_present") is True
    assert after.facts.get("drill_present") is True
    assert sorted(int(x) for x in after.facts.get("allowlist", [])) == sorted(written), (
        f"улика расходится с файлом: {after.facts!r} против {written}")


def test_the_drill_contact_written_comes_from_the_gate_constant(tmp_path):
    """Дрил-контакт — не литерал в коде подключения, а канон
    `chatter/payments/drill_gate.DRILL_CONTACTS`.

    Список уже разъезжался: строка `8849893367:yarina` месяц жила только в одной
    из трёх копий канона. Вторая копия внутри `chatter/connect` была бы четвёртой.
    """
    root = _allowlist_world(tmp_path)
    actions.act_s9(make_ctx(root, env=env_material()))
    written = set(live_allowlist(root) or [])
    assert written & CLIENT_DRILL_IDS, (
        f"дрил-контакта нет в allowlist: {sorted(written)}; "
        f"канон для {SLUG}: {sorted(CLIENT_DRILL_IDS)}")


def test_without_an_owner_id_the_allowlist_step_stops(tmp_path):
    """«owner id обязателен» (Д6).

    Allowlist без владельца = пульт, до которого владельцу не достучаться:
    `/allow`, `/funnel_gate`, кнопки — всё за id-гейтом. Молча вписать один
    дрил-контакт и объявить шаг закрытым — худший исход, потому что выглядит
    как успех.
    """
    root = _allowlist_world(tmp_path, owner=None)
    ctx = make_ctx(root, env=env_material())

    before = probes.probe_s9(ctx)
    assert before.verdict != Verdict.CLOSED
    assert before.todo.strip() and is_addressed(before.todo)
    assert before.facts.get("owner_present") is False

    try:
        actions.act_s9(ctx)
    except Exception:
        # Громкий отказ — законный способ не идти дальше (DEV-18). Молчаливая
        # запись allowlist без владельца — нет; это и проверяется ниже.
        pass

    written = live_allowlist(root) or []
    assert OWNER_CHAT_ID not in written
    assert set(written) & DECOYS == set(), f"вписан посторонний id: {written}"
    assert probes.probe_s9(ctx).verdict != Verdict.CLOSED, (
        "шаг закрыт allowlist'ом без владельца")


def test_act_s9_leaves_the_funnel_gate_shut(tmp_path):
    """Смежная красная линия (§2.1): подключение не имеет права открыть трафик.

    Проверяется здесь потому, что S9 — единственный автошаг, который правит
    `telegram:` в живом конфиге, то есть единственный, у кого есть возможность.
    """
    root = _allowlist_world(tmp_path)
    actions.act_s9(make_ctx(root, env=env_material()))
    data = yaml.safe_load(
        (root / "chatter" / "clients" / SLUG / "settings.yaml").read_text(encoding="utf-8"))
    assert (data.get("telegram") or {}).get("funnel_gate") is False


def test_the_owner_id_comes_from_the_config_not_from_the_env(tmp_path):
    """§12.7 п.5: один источник — `control.owner_chat_id` живого конфига.

    В окружении лежит ДРУГОЙ id (`TELEGRAM_OWNER_ID`, `CHATTER_OWNER_CHAT_ID`).
    Если он доедет до allowlist, значит источников два, а два числа на одну вещь
    расходятся молча — и расходятся они здесь в сторону «бот отвечает не тому».
    """
    root = _allowlist_world(tmp_path)
    actions.act_s9(make_ctx(root, env=env_material()))
    written = set(live_allowlist(root) or [])
    assert DECOY_IN_ENV not in written, (
        f"owner id взят из окружения: {sorted(written)}")
    assert OWNER_CHAT_ID in written, "владелец из конфига не вписан"


def test_a_foreign_id_already_in_the_allowlist_survives_and_is_named(tmp_path):
    """§12.11 п.4: посторонние id СОХРАНЯЮТСЯ и называются вслух.

    Стереть чужую строку значит выключить бота живому человеку — а это ровно
    тот ущерб, от которого allowlist и охраняют. Поэтому «не вписывать своё»
    (Д6) и «не стирать чужое» — два разных требования, и второе здесь.
    """
    root = _allowlist_world(tmp_path)
    live = root / "chatter" / "clients" / SLUG / "settings.yaml"
    # Чужой id вписан ТУДА И ТАК, как его вписывает человек: в поточный список
    # одной строкой, с пометкой рядом. Перезаписать документ `safe_dump`
    # значило бы построить конфиг, которого не бывает: комментарии исчезли бы,
    # а список стал бы блочным — и правка честно отказалась бы его трогать.
    text = live.read_text(encoding="utf-8")
    assert "  allowlist: []\n" in text, "стенд: поточного allowlist в конфиге нет"
    text = text.replace(
        "  allowlist: []\n",
        f"  allowlist: [{DECOY_IN_REPORT}]   # менеджер клиента, /allow 12.08\n")
    live.write_text(text, encoding="utf-8")

    ctx = make_ctx(root, env=env_material())
    actions.act_s9(ctx)

    written = set(live_allowlist(root) or [])
    assert DECOY_IN_REPORT in written, "чужой id стёрт — бот выключен живому человеку"
    assert OWNER_CHAT_ID in written and written & CLIENT_DRILL_IDS
    named = probes.probe_s9(ctx).facts.get("allowlist") or []
    assert DECOY_IN_REPORT in [int(x) for x in named], (
        f"чужой id не назван вслух: {named}")


# ═════════════════════════════════════════════════════════════════════════════
# Д17 — без --drill-yes дрил не запускается НИ РАЗУ
#
# Решение владельца q4 дословно: «$0.2–0.3 не деньги, но позавчера баланс
# кончился посреди прогона, и я узнал об этом от бота». Значит проверяется не
# «спросили», а «не потратили»: подставной раннер не видел `drill_runner`.
# ═════════════════════════════════════════════════════════════════════════════

def _drill_world(tmp_path: Path, *, with_result: bool = False) -> Path:
    root = world_ready(tmp_path)
    if with_result:
        write_drill_result(root)
    return root


def test_act_s13_without_the_flag_never_pays(tmp_path):
    """Д17. Харнесс ЗОВЁТСЯ и без разрешения — в режиме плана, ради сметы
    (§12.9 п.5): ставки живут в `drill_runner`, и наша копия разошлась бы с
    ними молча. Значит «не потратили» доказывается отсутствием ПЛАТЯЩЕГО
    вызова, а платит ровно `--yes`.

    Остановка обязана быть «жду тебя», а не «сломано» (§12.7 п.1): денежные
    ворота — штатный путь, и код 1 на нём приучал бы не смотреть на красное.
    """
    root = _drill_world(tmp_path)
    runner = FakeRunner(default_replies())
    ctx = make_ctx(root, runner=runner, drill_yes=False)

    result = actions.act_s13(ctx)

    assert runner.paying_calls == [], (
        f"деньги списаны без разрешения:\n{runner.argv_log()}")
    assert drill_results(root) == [], "появился результат дрила"
    assert result.verdict != Verdict.CLOSED
    assert result.waits_for_human is True, (
        "ворота денег — ожидание человека, а не поломка (§12.7 п.1)")
    assert result.todo.strip(), "остановка ворот денег молчит, что делать"
    assert "--drill-yes" in result.todo, (
        f"остановка не называет флаг, которым продолжать: {result.todo!r}")

    # §2.3: смета печатается ДО списания. Это и есть разница между «увидел,
    # что сейчас потратится» и «узнал об этом от бота».
    estimate = result.facts.get("estimate_usd")
    assert isinstance(estimate, (int, float)) and estimate > 0, (
        f"смета не названа: {result.facts!r}")
    assert estimate == pytest.approx(PLAN_ESTIMATE_USD, abs=0.011), (
        f"смета не из харнесса: {estimate} против {PLAN_ESTIMATE_USD:.4f}")
    # §12.12 п.2: прогона не было — значит «не проверяли», то есть None.
    assert result.facts.get("run_path") is None
    assert result.facts.get("rc") is None


def test_drill_again_alone_does_not_authorise_spending(tmp_path):
    """`--drill-again` отвечает на вопрос «повторить ли», а не «тратить ли».

    Разрешение на трату — отдельное решение владельца (§5.8), и один флаг не
    имеет права работать за два.
    """
    root = _drill_world(tmp_path)
    runner = FakeRunner(default_replies())
    actions.act_s13(make_ctx(root, runner=runner, drill_yes=False, drill_again=True))
    assert runner.paying_calls == [], (
        f"--drill-again потратил деньги в одиночку:\n{runner.argv_log()}")


def test_the_pipeline_never_spends_without_the_flag(tmp_path):
    """То же утверждение на уровне ПОРЯДКА (§12.3), а не одной функции.

    S13 изолирован реальным, остальные шаги объявлены закрытыми — иначе «не
    потратили» было бы зелёным просто потому, что до денег не дошли. Поэтому
    здесь же проверяется, что до S13 действительно дошли.
    """
    root = _drill_world(tmp_path)
    runner = FakeRunner(default_replies())
    w = walk(isolate("S13"), make_ctx(root, runner=runner, drill_yes=False))

    assert "S13" in w.visited, "до ворот денег не дошли — сторож был бы пустым"
    assert runner.paying_calls == [], f"деньги списаны:\n{runner.argv_log()}"
    assert w.rc == 3, (
        f"штатная остановка перед тратой отдана как {w.rc}, а не «ЖДЁТ ЧЕЛОВЕКА»")
    assert w.stop is not None and w.stop.todo.strip()


def test_without_a_lead_session_s13_waits_and_does_not_pay(tmp_path):
    """§12.11 п.1: нет предпосылок автолида — S13 становится человеческим шагом
    с точной командой, но НЕ платит молча.

    Дрил — суфлёр: реплики печатаются человеку по ходу прогона, а
    `CommandRunner` вывод захватывает. Тихого платного прогона с невидимыми
    подсказками не бывает ни в одной ветке.
    """
    root = _drill_world(tmp_path)
    (root / ".secrets" / "drill_lead.session").unlink()
    runner = FakeRunner(default_replies())

    result = actions.act_s13(make_ctx(root, runner=runner, drill_yes=True))

    assert runner.paying_calls == [], (
        f"платный прогон без суфлёра:\n{runner.argv_log()}")
    assert result.verdict != Verdict.CLOSED
    assert result.waits_for_human is True
    assert result.todo.strip() and is_addressed(result.todo)


def test_the_permission_is_not_remembered_between_runs(tmp_path):
    """Флаг разрешает РОВНО ОДИН прогон и не запоминается (§4).

    Второй запуск делается в том же состоянии, что и первый: подставной харнесс
    возвращает «прогон не состоялся» и файла результата не оставляет. Значит
    если дрил всё-таки заплатит второй раз — это не идемпотентность, это
    записанное где-то разрешение тратить.
    """
    root = _drill_world(tmp_path)

    def aborted(call: Call) -> CommandResult:
        if PAYING_FLAG in call.argv:
            return CommandResult(2, "", "прогон оборван на первом шаге")
        return CommandResult(0, plan_stdout(), "")

    first_runner = FakeRunner({**default_replies(), DRILL_MARKER: aborted})
    actions.act_s13(make_ctx(root, runner=first_runner, drill_yes=True))
    assert first_runner.paying_calls, (
        f"с разрешением дрил так и не запустился:\n{first_runner.argv_log()}")
    assert drill_results(root) == [], (
        "стенд построен неверно: результат дрила появился, второй запуск был бы "
        "пропущен по идемпотентности, а не по отсутствию флага")

    second_runner = FakeRunner(default_replies())
    actions.act_s13(make_ctx(root, runner=second_runner, drill_yes=False))
    assert second_runner.paying_calls == [], (
        f"разрешение пережило запуск:\n{second_runner.argv_log()}")


def test_no_other_automatic_step_reaches_the_drill_harness(tmp_path):
    """Ворота денег стоят на S13 — значит других дорог к харнессу быть не должно,
    даже плановых.

    Иначе достаточно одного вызова в соседнем шаге, чтобы вся остановка перед
    дрилом стала украшением.
    """
    root = _drill_world(tmp_path)
    for sid in AUTO_STEPS:
        if sid == "S13":
            continue
        runner = FakeRunner(default_replies())
        act = step_by_id(sid).act
        try:
            act(make_ctx(root, runner=runner, drill_yes=False))
        except Exception:
            # Падение автошага — предмет других сторожей (Д2, Д9, Д10). Здесь
            # утверждение только про деньги, и оно проверяется по журналу
            # вызовов, а не по тому, чем шаг кончился.
            pass
        assert runner.drill_calls == [], (
            f"{sid} дотянулся до дрил-харнесса:\n{runner.argv_log()}")


# ═════════════════════════════════════════════════════════════════════════════
# Д17 через ЖИВУЮ дверь: `main()` из §12.7 п.3
#
# Шов объявлен контрактом ровно ради этого: без него сторож на деньги вынужден
# был бы поднимать живые подпроцессы, то есть не существовать.
# ═════════════════════════════════════════════════════════════════════════════

def test_plan_mode_pays_nothing_and_touches_nothing(tmp_path):
    """`--plan` отвечает на вопрос «где я», а не «всё ли хорошо» (§12.10 п.2),
    и ничего не исполняет — значит и журнала не заводит (§12.10 п.3).

    Прогон, который меняет диск, отвечая на вопрос о состоянии, — это второе
    представление состояния, гасящее первое.
    """
    root = _drill_world(tmp_path)
    runner = FakeRunner(default_replies())
    before = snapshot(root)

    rc = connect_main([SLUG, "--plan"], runner=runner, root=root)

    assert rc == 0, f"карта построена, а код выхода {rc}"
    assert runner.paying_calls == [], f"`--plan` заплатил:\n{runner.argv_log()}"
    assert snapshot(root) == before, "`--plan` изменил диск"


def test_the_cli_pays_nothing_without_the_flag(tmp_path):
    """Тот же Д17, но через дверь, которой пользуется человек."""
    root = _drill_world(tmp_path)
    runner = FakeRunner(default_replies())
    rc = connect_main([SLUG], runner=runner, root=root)
    assert runner.paying_calls == [], (
        f"CLI заплатил без --drill-yes (rc={rc}):\n{runner.argv_log()}")


# ═════════════════════════════════════════════════════════════════════════════
# Д3 — второй платный прогон только явным флагом (§2.3)
# ═════════════════════════════════════════════════════════════════════════════

def test_an_existing_drill_result_is_not_rerun_even_with_drill_yes(tmp_path):
    """Разрешение потратить ≠ приказ потратить.

    Повтор «на всякий случай» — это деньги клиента и посторонний трафик в его
    БД (§2.3), причём `drill_reset` деньги НЕ чистит: `quotes`/`invoices`/
    `payments` переживают сброс (§10).
    """
    root = _drill_world(tmp_path, with_result=True)
    runner = FakeRunner(default_replies())
    ctx = make_ctx(root, runner=runner, drill_yes=True)

    actions.act_s13(ctx)

    assert runner.paying_calls == [], (
        f"дрил оплачен второй раз без --drill-again:\n{runner.argv_log()}")
    assert probes.probe_s13(ctx).facts.get("run_path"), (
        "результат на диске есть, а улика его не называет")


def test_an_existing_drill_result_is_rerun_with_drill_again(tmp_path):
    """Контрапозиция: с обоими флагами повтор обязан состояться ровно один раз.

    Без этого ассерта Д3 прошёл бы у кода, который не умеет платить вообще.
    """
    root = _drill_world(tmp_path, with_result=True)
    runner = FakeRunner(default_replies())
    actions.act_s13(make_ctx(root, runner=runner, drill_yes=True, drill_again=True))
    assert len(runner.paying_calls) == 1, (
        f"ожидался ровно один платный прогон, их {len(runner.paying_calls)}\n"
        f"{runner.argv_log()}")


def test_a_finished_connection_does_not_spend_again(tmp_path):
    """Идемпотентность §1 на уровне порядка: повторный запуск завершённого
    подключения не повторяет ни одного действия, чей факт уже на диске —
    особенно платного.

    Код 0 достижим только потому, что хвостовые S14/S15 в цикл не входят
    (§12.10 п.1): они закрываются словом владельца, а не фактом.
    """
    root = _drill_world(tmp_path, with_result=True)
    runner = FakeRunner(default_replies())
    w = walk(isolate("S13"), make_ctx(root, runner=runner, drill_yes=True))

    assert "S13" in w.visited
    assert runner.paying_calls == [], f"повторная трата:\n{runner.argv_log()}"
    assert w.rc == 0, (
        f"завершённое подключение объявлено незакрытым (rc={w.rc}); "
        f"остановка: {w.stop!r}")


def test_a_report_from_another_client_does_not_close_the_step(tmp_path):
    """§12.7 п.2 и §12.9 п.6: каталог отделяет, ТЕЛО доказывает.

    Отчёт, не отвечающий на вопрос «чей прогон», — улика ни о чём: на машине с
    несколькими клиентами он закрыл бы единственный шаг, который доказывает,
    что бот вообще отвечает, не потратив ни цента.
    """
    root = _drill_world(tmp_path)
    write_drill_result(root, names_client=NEIGHBOUR_SLUG)
    runner = FakeRunner(default_replies())

    r = probes.probe_s13(make_ctx(root, runner=runner, drill_yes=False))

    assert r.verdict != Verdict.CLOSED, "чужой отчёт принят за свой"
    assert r.todo.strip()


def test_the_drill_result_is_read_from_disk_not_from_the_journal(tmp_path):
    """Смежное с Д7, но про деньги: журнал заводится для ЧЕЛОВЕКА и пробами не
    читается никогда.

    Журнал, утверждающий «дрил прогнан», при пустом каталоге прогонов не имеет
    права закрыть шаг: подделать журнал дешевле, чем прогнать дрил, и именно
    так второе представление состояния молча гасит первое.
    """
    root = _drill_world(tmp_path)
    write_journal(root, extra="S13: дрил прогнан, rc=0, результат в state/drills")
    runner = FakeRunner(default_replies())
    r = probes.probe_s13(make_ctx(root, runner=runner, drill_yes=False))
    assert r.verdict != Verdict.CLOSED, "шаг закрыт по записи в журнале"


# ─────────────────────────────────────────────────────────────────────────────
# МЕТА-СТОРОЖ НА САМ СПИСОК А1–А5 (§12.13 п.2 и п.4)
#
# Сверка идёт В ОБЕ СТОРОНЫ: текст С признаком обязан считаться адресным, текст
# БЕЗ единого признака — неадресным. Односторонняя сверка пропускает ровно тот
# класс дефекта, который тут уже случился: правило, которое зеленеет на всём.
#
# Образцы взяты из таблицы §12.13 (там они выписаны) плюс ловушки: голое имя
# скрипта без ключей, голое имя таска, «исправьте конфигурацию» и отсылка к
# «названным клиентам». Все они — тексты, а не результаты прогона: сторож на
# СПИСОК не имеет права зависеть от того, что сегодня возвращает плановый код,
# иначе он снова согласится с реализацией по определению.
# ─────────────────────────────────────────────────────────────────────────────

# (признак, текст-С-признаком, текст-БЕЗ-этого-признака, слаг)
ADDRESS_FEATURE_PAIRS = (
    # А1 — путь: разделитель…
    ("А1", "почини chatter/clients/volska/settings.yaml и повтори",
           "почини настройки клиента и повтори", "volska"),
    ("А1", "положи бандл в .secrets/ на ЭТОЙ машине",
           "положи бандл в защищённый каталог", "volska"),
    ("А1", "смотри отчёты прогонов в state/drills/",
           "смотри отчёты прогонов там, где их пишет харнесс", "volska"),
    ("А1", "разбери причину по logs/chatter_guardian.log",
           "разбери причину по логу супервизора", "volska"),
    # …ИЛИ расширение (без единого разделителя рядом)
    ("А1", "перезапиши REPORT.md в UTF-8",
           "перезапиши отчёт в UTF-8", "volska"),
    ("А1", "впиши control.owner_chat_id в settings.yaml",
           "впиши control.owner_chat_id в настройки", "volska"),
    # …ИЛИ буква диска и обратный слеш
    ("А1", "смотри C:\\jarvis\\logs на этой машине",
           "смотри логи на этой машине", "volska"),
    # …ИЛИ начало от известного корня репозитория
    ("А1", "положи заготовку в build/onboard/volska/",
           "положи заготовку рядом со сборкой", "volska"),
    # Прямой замер ложного зелёного: пара `allowlist/denylist` путём НЕ является.
    ("А1", "почини chatter/clients/volska/settings.yaml",
           "перечитай раздел про allowlist/denylist и реши сам", "volska"),

    # А2 — имя команды ВМЕСТЕ с ключом
    ("А2", "подними клиента: chatter_client.ps1 -Action start -Slug volska",
           "подними клиента через chatter_client", "volska"),
    ("А2", "подними таск: Start-ScheduledTask -TaskName JarvisChatterGuardian",
           "убедись, что таск JarvisChatterGuardian живёт", "volska"),
    ("А2", "выключи запись: registry_cli disable --slug volska",
           "выключи запись клиента в реестре", "volska"),
    ("А2", "собери конфиг: python -m chatter.onboard volska --brief файла",
           "собери конфиг онбордингом", "volska"),
    # Голое имя скрипта — НЕ А2, даже когда у имени есть расширение: расширение
    # это А1 (путь), а А2 требует ключа. Ловушка старой регулярки.
    ("А2", ".\\scripts\\reencrypt_env.ps1 -Check",
           "прогони reencrypt_env.ps1", "volska"),

    # А3 — флаг самой команды подключения
    ("А3", "разреши ОДИН платный прогон флагом --drill-yes",
           "разреши ОДИН платный прогон", "volska"),
    ("А3", "перезапусти прогон явно, с --drill-again",
           "перезапусти прогон явно", "volska"),
    ("А3", "укажи корень репозитория через --root",
           "укажи корень репозитория", "volska"),
    ("А3", "посмотри план: --plan",
           "посмотри план", "volska"),

    # А4 — слаг клиента
    ("А4", "перезапусти раннер volska: отметку живости пишет он сам",
           "перезапусти раннер этого клиента: отметку живости пишет он сам",
           "volska"),
    ("А4", "перезапусти раннер yarina: отметку живости пишет он сам",
           "перезапусти раннер названного клиента", "yarina"),

    # А5 — слеш-команда пульта
    ("А5", "открой трафик сам, командой пульта: /funnel_gate on confirm",
           "открой трафик сам, командой пульта", "volska"),
    ("А5", "посмотри список клиентов пультом: /clients",
           "посмотри список клиентов пультом", "volska"),
    # Слеш внутри пути — это А1, а не команда пульта.
    ("А5", "правь его точечно (пульт: /allow, /funnel_gate off)",
           "почини chatter/clients/registry.yaml", "volska"),
    # Замер 21.08: на пути с буквой диска А5 зажигался на `/jarvis`. Вердикт не
    # менялся (горят А1 и А4), но совравший признак уезжал в покрытие.
    ("А5", "открой трафик: /funnel_gate on confirm",
           "впиши руками в C:/jarvis/chatter/clients/yarina/settings.yaml: "
           "telegram.allowlist: [1]", "yarina"),
)

# Оба «должно быть» из §12.13 п.3 целиком: адресные варианты реальных текстов.
ADDRESS_SPEC_REWRITES = (
    ("S12", ("подними таск: Start-ScheduledTask -TaskName JarvisChatterGuardian, "
             "через 30 с повтори ту же команду; если таск падает — его лог в "
             "logs/chatter_guardian.log"), ("А1", "А2"), "volska"),
    ("S7", ("почини chatter/clients/volska/settings.yaml (полный список — "
            "facts.broken_slugs) либо выключи запись: registry_cli disable "
            "--slug volska"), ("А1", "А2", "А4"), "volska"),
)

# Ни одного признака А1–А5. Первые два — дословные тексты планового кода,
# забракованные в §12.13 п.3; остальные — ловушки того же класса.
ADDRESS_TRAPS = (
    # Тексты планового кода, забракованные §12.13 п.3.
    "убедись, что таск JarvisChatterGuardian живёт, и подожди один его цикл (~30 с)",
    "почини конфиги названных клиентов либо выключи их, затем повтори",
    # Ловушки того же класса, но русскоязычные и без слешей.
    "исправьте конфигурацию",
    "перезапусти chatter_client, затем посмотри ещё раз",
    "запусти таск JarvisChatterGuardian",
    "разберись с названными клиентами и повтори",
    "подожди немного и повтори ту же команду",
    "поправь настройки клиента и попробуй ещё раз",
    "убедись, что супервизор поднят, и подожди цикл",
    # ── ЗАМЕР 21.08, ДОСЛОВНО. Все шесть строк — тексты БЕЗ единого адреса, и
    # на двух из них прежнее правило А1 («разделитель внутри ASCII-токена»)
    # давало ЛОЖНОЕ ЗЕЛЁНОЕ. Прежние ловушки его не ловили, потому что все были
    # русскоязычными и без слешей: сторож зеленел на всём этом классе.
    "почини и/или выключи названных клиентов",
    "разберись с ошибкой 500/503 и повтори",
    "смотри вывод выше: rc 2/3 означает отказ",
    "перечитай раздел про allowlist/denylist и реши сам",
    "сделай это в 12/24 часа",
    "уточни у владельца, on/off ли воронка",
    # Тот же класс: латиница со слешем в прозе.
    "переведи клиента в start/stop и посмотри ещё раз",
    "выстави enabled/disabled по ситуации",
    "реши по схеме yes/no и повтори",
    # Три сегмента формой от пути не отличаются — поэтому пункта «три и
    # более сегмента» в А1 нет вовсе.
    "поставь режим on/off/auto по ситуации",
    "ответь yes/no/maybe и повтори",
    "выбери start/stop/restart и посмотри ещё раз",
    # Тот же класс: путь-обманка, но сегменты русскими буквами.
    "смотри раздел настройки/доступы — там всё написано",
    "проверь каталог клиента/настройки и реши сам",
)


def test_address_feature_list_is_exactly_five_named_items():
    """§12.13 п.2: «Списка ровно пять пунктов».

    Утверждение литеральное, а не `len(ADDRESS_FEATURES) > 0`: список закрыт
    затем, чтобы шестой признак нельзя было дописать «на месте», не тронув
    спеку. Сверяются и порядок, и имена.
    """
    assert tuple(fid for fid, _t, _c in ADDRESS_FEATURES) == (
        "А1", "А2", "А3", "А4", "А5")
    assert len({fid for fid, _t, _c in ADDRESS_FEATURES}) == 5


def test_every_feature_of_the_list_is_covered_by_samples():
    """Каждый из пяти признаков обязан быть проверен образцами в обе стороны.

    Без этого утверждения признак можно было бы завести и не проверить ни разу:
    список рос бы, а сторож молчал.
    """
    named = {fid for fid, _t, _c in ADDRESS_FEATURES}
    positive = {fid for fid, _p, _n, _s in ADDRESS_FEATURE_PAIRS}
    assert positive == named, f"без образцов остались: {sorted(named - positive)}"


@pytest.mark.parametrize(
    "feature,addressed_text,plain_text,slug", ADDRESS_FEATURE_PAIRS,
    ids=[f"{f}-{i}" for i, (f, _p, _n, _s) in enumerate(ADDRESS_FEATURE_PAIRS)])
def test_each_address_feature_reads_both_ways(feature, addressed_text,
                                              plain_text, slug):
    """Признак обязан ЗАЖИГАТЬСЯ на своём образце и МОЛЧАТЬ на тексте без него.

    Одна сторона ловит правило, которое перестало срабатывать; вторая — правило,
    которое зеленеет на всём подряд. Проверять надо обе: обе уже ломались.
    """
    lit = address_features(addressed_text, slug)
    assert feature in lit, (
        f"{feature} не зажёгся на своём же образце §12.13: {addressed_text!r} "
        f"(нашлось: {lit})")
    assert is_addressed(addressed_text, slug)
    assert feature not in address_features(plain_text, slug), (
        f"{feature} зажёгся на тексте БЕЗ этого признака: {plain_text!r}")


@pytest.mark.parametrize("step_id,text,expected,slug", ADDRESS_SPEC_REWRITES,
                         ids=[r[0] for r in ADDRESS_SPEC_REWRITES])
def test_spec_rewrites_light_up_exactly_the_named_features(step_id, text,
                                                           expected, slug):
    """§12.13 п.3: у обоих «должно быть» названы признаки, которые они несут.

    Спека сама подписала их как «(А2 + А1)» и «(А1 + А4 + А2)» — сверяем с
    подписью, а не «лишь бы адресно».
    """
    assert set(address_features(text, slug)) >= set(expected), (
        f"{step_id}: ожидались {expected}, нашлось "
        f"{address_features(text, slug)}")


@pytest.mark.parametrize("trap", ADDRESS_TRAPS)
def test_text_without_any_feature_is_not_addressed(trap):
    """Текст без единого признака А1–А5 обязан краснеть.

    Здесь и живёт цена арки: «убедись, что таск живёт, и подожди цикл» непусто,
    грамматически «что сделать», — и отправляет человека в три ночи в мануал,
    ровно туда, куда §9 п.2 обещал не отправлять.
    """
    assert address_features(trap) == (), (
        f"неадресный текст признан адресным: {trap!r} → "
        f"{address_features(trap)}")
    assert not is_addressed(trap)


def test_dropped_marker_at_botfather_is_not_an_address_by_itself():
    """`@BotFather` признаком НЕ является: в таблице §12.13 такого пункта нет.

    Старая регулярка его засчитывала — это и было расхождение списка и кода в
    сторону послабления. Плановый код от этого не страдает: во всех трёх местах,
    где он зовёт @BotFather, рядом стоит путь или слаг.
    """
    assert address_features("заведи отдельного бота у @BotFather") == ()
    assert is_addressed(
        "заведи отдельного бота у @BotFather и укажи имя переменной в "
        "chatter/clients/volska/settings.yaml", "volska")
