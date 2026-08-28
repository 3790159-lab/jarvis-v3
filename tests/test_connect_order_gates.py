# -*- coding: utf-8 -*-
"""Сторожа Д2/Д4/Д8/Д10/Д13/Д14 арки T7 (`chatter.connect`).

Спека: `docs/superpowers/specs/2026-08-20-t7-turnkey-connect.md`, таблица §7.
Файл написан ОТ СПЕКИ автором, который планового кода не видел
([[jarvis-guards-not-by-the-plan-author]]): единственное, на что здесь можно
опираться, — §12 (КОНТРАКТ МОДУЛЯ). Всё остальное — фикстуры на диске.

Что здесь ловится:

* **Д2**  порядок: при отсутствии валидного конфига ИЛИ сессии `enabled: true`
  не выставляется НИ ПРИ КАКОМ пути;
* **Д4**  радиус: отказ замера catch-up останавливает подъём, и `-Force` у
  `chatter_client.ps1` не подставляется автоматически НИ В ОДНОЙ ветке;
* **Д8**  живой клиент не трогаем: слаг уже `alive` с открытым `funnel_gate` →
  команда отказывается ЦЕЛИКОМ, а не «доподключает»;
* **Д10** живость: `starting` за `alive` не считается, протухший heartbeat не
  считается;
* **Д13** `chatter/clients/active.yaml` не изменяется ни на одном шаге;
* **Д14** `funnel_gate` не переводится в `true` ни на одном шаге и ни при каком
  флаге.

── ПОЧЕМУ СТОЛЬКО МЕТА-ТЕСТОВ ───────────────────────────────────────────────

Д13 и Д14 — сторожа на ОТСУТСТВИЕ действия, и такой сторож зеленеет ПО
ПОСТРОЕНИЮ двумя способами сразу:

1. проверка слепа (сравнивали «нет вызова с таким именем» вместо байтов);
2. проверять было нечего (конвейер остановился на первом же шаге и ни одного
   действия не выполнил).

Поэтому здесь есть `test_meta_*`:

* `test_meta_active_yaml_guard_*` и `test_meta_funnel_gate_guard_*` мутируют
  диск РУКАМИ ТЕСТА и требуют, чтобы помощник покраснел — это доказательство
  способа (1);
* `test_meta_healthy_root_reaches_*` требуют, чтобы на здоровом корне конвейер
  ДОШЁЛ до подъёма и до дрила — это доказательство способа (2). Если они
  красные, все остальные утверждения этого файла ничего не стоят: читать
  сначала их.

── ЖИВОГО НИЧЕГО ────────────────────────────────────────────────────────────

Свой корень в `tmp_path`, свой реестр, свои `.secrets`. Подпроцессов нет
вовсе: §12.2 обязывает пускать `registry_cli` / `chatter_client.ps1` /
`drill_runner.py` / `reencrypt_env.ps1` ТОЛЬКО через `Ctx.run`, и сюда
подставляется `FakeRunner`, который пишет argv в список. Ни Telegram, ни
денег, ни сети.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest
import yaml

# Планового кода ещё нет — импорт красный, и это штатное состояние до его
# появления. Заглушек реализации в этом файле нет намеренно.
from chatter.connect.model import Ctx, Owner, StepResult, Verdict  # noqa: F401
from chatter.connect.steps import STEPS

from chatter.core.client_registry import parse_registry
from chatter.payments.drill_gate import DRILL_CONTACTS
from chatter.registry_cli import build_plan, session_available


# ─────────────────────────────────────────────────────────────────────────────
# Константы фикстуры
# ─────────────────────────────────────────────────────────────────────────────

# Слаг взят из канона `DRILL_CONTACTS`: у S9 дрил-контакт берётся оттуда по
# суффиксу клиента (Д6), и слаг-фантазия сделал бы S9 недостижимым, а вместе с
# ним — и все шаги после него.
SLUG = "yarina"
DRILL_CONTACT_ID = 8849893367
OWNER_CHAT_ID = 545893540
OWNER_TG_ID = 237616472
TOKEN_ENV = "CHATTER_CONTROL_BOT_TOKEN_YARINA"
# Включённый сосед по реестру: у него СВОЙ токен и свои пути.
NEIGHBOUR_SLUG = "volska"
NEIGHBOUR_TOKEN_ENV = "CHATTER_CONTROL_BOT_TOKEN_VOLSKA"

ACTIVE_REL = "chatter/clients/active.yaml"
REGISTRY_REL = "chatter/clients/registry.yaml"

# Строка-доказательство перешифровки (§2.2). Именно она закрыла шаг у Ярины.
ISOLATED_TOKEN_LINE = "control-bot poller starting (isolated token)"

_GATE_TRUE_RE = re.compile(r"funnel_gate\s*:\s*(true|yes|on|1)\b", re.IGNORECASE)
_FORCE_RE = re.compile(r"^[-/]force$", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Фейковый CommandRunner (§12.2)
# ─────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class FakeCommandResult:
    rc: int = 0
    stdout: str = ""
    stderr: str = ""


class RunnerUnavailable(RuntimeError):
    """§12.6.3: таймаут, ненайденный исполняемый, любая невозможность выполнить
    — это ИСКЛЮЧЕНИЕ, а не `CommandResult` с выдуманным `rc`.

    Различие несёт весь смысл Д4: «замер сказал нет» и «замер не состоялся» —
    разные события, и слипшись они делают молчание инструмента разрешением.
    """


class FakeRunner:
    """`run(argv, *, cwd, timeout) -> CommandResult` из §12.2.

    Отвечает по СОДЕРЖАНИЮ argv, а не по порядку вызовов: порядок внешних
    команд контрактом не задан, и сторож, завязанный на него, краснел бы от
    безобидной перестановки.

    Две детали сняты с живых скриптов, а не придуманы:

    * `enabled: true` в реестр пишет САМ `scripts/chatter_client.ps1`
      (`Set-Enabled` после `Assert-CatchupRadius`), и только при успешном
      замере — иначе Д2 проверял бы выдумку, а не механику;
    * поднятый раннер через ~30 с получает от гардиана запись в
      `state/chatter_clients.json` и пишет строку `isolated token` в свой лог.
      Без этой части приёмка живости (S12) не смогла бы закрыться НИКОГДА, и
      всё, что стоит после неё, осталось бы непроверенным.
    """

    def __init__(self, root: Path, *, start_rc: int = 0, drill_rc: int = 0,
                 check_rc: int = 0, env_state: str = "in_sync",
                 start_raises: bool = False) -> None:
        self.root = Path(root)
        self.start_rc = start_rc
        self.drill_rc = drill_rc
        self.check_rc = check_rc
        self.env_state = env_state
        self.start_raises = start_raises
        self.calls: list[list[str]] = []

    # -- разбор argv ---------------------------------------------------------

    @staticmethod
    def _flat(argv) -> str:
        return " ".join(str(a) for a in argv).lower()

    def calls_matching(self, needle: str) -> list[list[str]]:
        n = needle.lower()
        return [c for c in self.calls if n in self._flat(c)]

    @property
    def start_calls(self) -> list[list[str]]:
        return [c for c in self.calls
                if "chatter_client" in self._flat(c) and "start" in self._flat(c)]

    @property
    def drill_calls(self) -> list[list[str]]:
        flat = self._flat
        return [c for c in self.calls if "drill" in flat(c)]

    # -- сам вызов -----------------------------------------------------------

    def run(self, argv, *, cwd=None, timeout=None):
        argv = [str(a) for a in argv]
        self.calls.append(list(argv))
        flat = self._flat(argv)

        if "registry_cli" in flat or "registry-cli" in flat:
            return self._registry_plan()

        if "chatter_client" in flat:
            if "start" in flat:
                return self._start()
            return FakeCommandResult(0, "", "")

        if "reencrypt_env" in flat:
            # Дословный формат живого scripts/reencrypt_env.py --check:
            # строка «[reencrypt] статус до: <состояние>», rc 0 ровно при
            # in_sync. Фейк, печатающий похожее, проверял бы разбор, которого
            # в проде нет.
            return FakeCommandResult(
                0 if self.env_state == "in_sync" else 1,
                f"[reencrypt] статус до: {self.env_state}\n", "")

        if "drill" in flat:
            return self._drill()

        if "onboard" in flat and "check" in flat:
            return FakeCommandResult(self.check_rc, "автоприёмка: красных нет\n", "")

        return FakeCommandResult(0, "", "")

    # §12.2 + §12.8.4: путь наружу ОДИН и написан одним способом —
    # `ctx.runner.run(argv, cwd=..., timeout=...)`. Алиаса `__call__`
    # здесь НЕТ намеренно: пока имя поля было `run`, обе трактовки были
    # законны, и терпимость помогала; теперь она бы прятала код, зовущий
    # раннер не по контракту, — то есть глушила бы сторожа вместо кода.

    # -- отдельные команды ---------------------------------------------------

    def _registry_plan(self) -> FakeCommandResult:
        """НАСТОЯЩИЙ `build_plan` на файле фикстуры.

        Слепок вместо живого разбора разъехался бы с реестром ровно тогда,
        когда его правит проверяемый код.
        """
        path = self.root / REGISTRY_REL
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        plan = build_plan(
            text,
            root=str(self.root),
            session_available=lambda s: session_available(s, root=str(self.root)),
            client_dir_exists=lambda slug: (
                self.root / "chatter" / "clients" / slug).is_dir(),
        )
        return FakeCommandResult(0, json.dumps(plan, ensure_ascii=False), "")

    def _start(self) -> FakeCommandResult:
        if self.start_raises:
            raise RunnerUnavailable(
                "powershell не найден — замер радиуса выполнить нечем")
        if self.start_rc != 0:
            # Живой текст отказа. В нём НАМЕРЕННО есть подсказка про `-Force`:
            # это ровно та приманка, ради которой Д4 и написан — скрипт сам
            # предлагает обойти замер, и код подключения обязан на неё не
            # поддаться.
            out = (
                "[chatter_client] ОТКАЗ: при подъёме catch-up ответит на диалоги выше.\n"
                "  Ответь клиенту сам либо подними осознанно: -Action start -Force\n"
            )
            return FakeCommandResult(self.start_rc, out, "")
        set_registry_enabled(self.root, SLUG, True)
        # То, что через ~30 с делает гардиан, а следом — сам раннер.
        write_live_state(self.root, SLUG, state="alive", now=time.time(),
                         heartbeat_age=1.0)
        log = self.root / "logs" / f"chatter_{SLUG}.log"
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        if ISOLATED_TOKEN_LINE not in text:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(text + f"2026-08-20 10:00:00 {ISOLATED_TOKEN_LINE}\n",
                           encoding="utf-8")
        return FakeCommandResult(
            0, f"[chatter_client] {SLUG} -> enabled: true\nрадиус переответа: 0\n", "")

    def _drill(self) -> FakeCommandResult:
        # §12.7.2: каталог отделяет прогон от чужого, ТЕЛО доказывает, чей он.
        # На машине уже лежат дрилы других клиентов, и файл без имени клиента
        # закрыл бы шаг, не потратив ни цента и ничего не проверив.
        drills = self.root / "state" / "drills" / SLUG
        drills.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{len(self.drill_calls):02d}"
        (drills / f"{stamp}.md").write_text(
            f"# Дрил: {SLUG}\nclient: {SLUG}\n"
            f"contact: {DRILL_CONTACT_ID}:{SLUG}\n"
            f"EXPECT_KEYS: закрыт\nстоимость: $0.2287\n",
            encoding="utf-8")
        return FakeCommandResult(
            self.drill_rc, f"смета: $0.2951\nдрил завершён ({SLUG})\n", "")


# ─────────────────────────────────────────────────────────────────────────────
# Корень на диске
# ─────────────────────────────────────────────────────────────────────────────

ACTIVE_YAML_TEXT = """\
# ⚠️ ГАРДИАНОМ ЭТОТ ФАЙЛ БОЛЬШЕ НЕ ИСПОЛЬЗУЕТСЯ (§0-бис спеки T7).
# Живёт ровно для ручного `python -m chatter.telethon_run` без аргументов.
clients: [demo]
"""

# Дрил-сценарий: заготовку генерирует chatter.onboard (решение владельца
# q1 от 17.08), контакт берётся из канона DRILL_CONTACTS, а не выдумывается.
DRILL_SCENARIO = "|".join([
    "name: Ярина стенд (цена)",
    "client: " + SLUG,
    'contact: "telegram:8849893367:yarina"',
    "steps:",
    '  - say: "Скільки коштує керамічне покриття?"',
    "    expect:",
    "      no_duplicate_reply: true",
    "",
]).replace("|", chr(10))

PERSONA_MD = "Ярина, 26, адміністраторка студії детейлінгу.\n"
KNOWLEDGE_MD = "Кераміка: від 8000 грн. Термін: 2 дні.\n"
PLAYBOOK_MD = "1. Привітатися.\n2. Уточнити авто.\n"


def settings_text(*, funnel_gate: bool = False, allowlist=(),
                  broken: bool = False, token_env: str = TOKEN_ENV,
                  persona: str = "Ярина") -> str:
    if broken:
        # Невалидный YAML: `load_config` обязан отказать. Именно этот случай
        # §2.1 называет ценой «гардиан поднимает и роняет раннер по кругу».
        return "model: [не закрытая скобка\nlanguage: uk\n"
    doc = {
        "model": "claude-sonnet-5",
        "language": "uk",
        "owner_id": "Старший майстер",
        "persona_name": persona,
        "currency": "грн",
        "telegram": {
            "allowlist": list(allowlist),
            "funnel_gate": bool(funnel_gate),
        },
        "control": {
            "control_bot_token_env": token_env,
            "owner_chat_id": OWNER_CHAT_ID,
        },
        "payments": {"enabled": False},
    }
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def registry_text(*, entry: str | None = "disabled") -> str:
    """`entry`: None — записи о слаге нет; 'disabled'/'enabled' — есть."""
    head = (
        "# Реестр клиентов chatter: КТО ДОЛЖЕН ЖИТЬ под гардианом.\n"
        "clients:\n"
        "  volska:\n"
        "    enabled: true\n"
        "    personas: [volska]\n"
        "    session: .secrets/demo.session\n"
        "    db: .secrets/demo.db\n"
    )
    if entry is None:
        return head
    return head + (
        f"\n  {SLUG}:\n"
        f"    enabled: {'true' if entry == 'enabled' else 'false'}\n"
        f"    personas: [{SLUG}]\n"
        f"    session: .secrets/{SLUG}.session\n"
        f"    db: .secrets/{SLUG}.db\n"
    )


def set_registry_enabled(root: Path, slug: str, value: bool) -> None:
    """Точечная правка текстом — тем же приёмом, что и живой `Set-Enabled`."""
    path = Path(root) / REGISTRY_REL
    lines = path.read_text(encoding="utf-8").splitlines()
    in_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{slug}:"):
            in_block = True
            continue
        if in_block:
            if stripped.startswith("enabled:"):
                indent = line[: len(line) - len(line.lstrip())]
                lines[i] = f"{indent}enabled: {'true' if value else 'false'}"
                break
            if stripped and not line.startswith(" " * 4):
                break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def registry_enabled(root: Path, slug: str) -> bool | None:
    """`enabled` слага по НАСТОЯЩЕМУ разбору реестра. None — записи нет."""
    path = Path(root) / REGISTRY_REL
    if not path.exists():
        return None
    try:
        entries = parse_registry(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for e in entries:
        if e.slug == slug:
            return bool(e.enabled)
    return None


def write_live_state(root: Path, slug: str, *, state: str, now: float,
                     heartbeat_age: float | None) -> None:
    """`state/chatter_clients.json` + отметка живости.

    Обе формы свежести пишутся согласованно (поле `heartbeat_ts` и mtime
    файла отметки): контракт не говорит, какую из них читает проба S12, и
    сторож, выбравший одну, молча позеленел бы на второй.
    """
    state_dir = Path(root) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    hb_ts = None if heartbeat_age is None else int(now - heartbeat_age)
    payload = {
        "updated_ts": int(now),
        "fatal": None,
        "clients": {
            slug: {
                "desired": "enabled",
                "state": state,
                "pid": 4242 if state in ("alive", "starting") else None,
                "heartbeat_ts": hb_ts,
                "last_transition_ts": int(now - 60),
                "consecutive_fail": 0,
                "last_error": None,
            }
        },
    }
    (state_dir / "chatter_clients.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    hb = state_dir / f"chatter_heartbeat_{slug}.txt"
    if hb_ts is None:
        if hb.exists():
            hb.unlink()
        return
    hb.write_text(f"{hb_ts}\n", encoding="utf-8")
    os.utime(hb, (hb_ts, hb_ts))


def make_root(
    tmp_path: Path,
    *,
    config: str = "ok",           # "ok" | "broken" | "absent"
    build_config: str = "ok",     # то же для build/onboard/<slug>/
    session: bool = True,
    consent: bool = True,
    bundle: bool = True,
    token: bool = True,
    registry_entry: str | None = None,   # None | "disabled" | "enabled"
    allowlist=None,
    funnel_gate: bool = False,
    live_state: str | None = None,       # None | "alive" | "starting" | "down"
    heartbeat_age: float | None = 5.0,
    token_log: bool = True,
    drill_result: bool = False,
    now: float | None = None,
) -> Path:
    """Корень подключения целиком. Здоровый по умолчанию — кроме реестра,
    подъёма и дрила: их и должен делать проверяемый код."""
    now = time.time() if now is None else now
    root = tmp_path / "root"
    # По умолчанию allowlist ПУСТ, и это не мелочь фикстуры: S9 — автошаг, и
    # заранее заполненный allowlist делал бы его пробу закрытой, а действие —
    # никогда не исполняемым. Тогда Д13/Д14 на полном прогоне сторожили бы
    # шаг, который не работает: сторож зелен по построению ровно так.
    allow = [] if allowlist is None else list(allowlist)

    (root / "chatter" / "clients").mkdir(parents=True, exist_ok=True)
    (root / ".secrets").mkdir(parents=True, exist_ok=True)
    (root / "state" / "connect").mkdir(parents=True, exist_ok=True)
    (root / "state" / "drills").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    # Инструменты подключения ЛЕЖАТ на месте — как в настоящем корне. Пустой
    # каталог scripts/ проверял бы ветку «инструмента нет», а не ворота;
    # `chatter_catchup_radius.py` положен НАМЕРЕННО: он существует, но звать
    # его напрямую подключению нельзя (замер вшит в chatter_client.ps1, §2.3).
    for tool in ("chatter_client.ps1", "reencrypt_env.ps1", "drill_runner.py",
                 "chatter_catchup_radius.py"):
        (root / "scripts" / tool).write_text("# заглушка стенда", encoding="utf-8")

    # active.yaml — ЛОЖНЫЙ КАНАЛ (§0-бис). Лежит здесь ровно затем, чтобы Д13
    # было что сторожить.
    (root / ACTIVE_REL).write_text(ACTIVE_YAML_TEXT, encoding="utf-8")
    (root / REGISTRY_REL).write_text(registry_text(entry=registry_entry),
                                     encoding="utf-8")

    def _client_files(dirpath: Path, mode: str) -> None:
        if mode == "absent":
            return
        dirpath.mkdir(parents=True, exist_ok=True)
        (dirpath / "persona.md").write_text(PERSONA_MD, encoding="utf-8")
        (dirpath / "knowledge.md").write_text(KNOWLEDGE_MD, encoding="utf-8")
        (dirpath / "playbook.md").write_text(PLAYBOOK_MD, encoding="utf-8")
        (dirpath / "settings.yaml").write_text(
            settings_text(funnel_gate=funnel_gate, allowlist=allow,
                          broken=(mode == "broken")),
            encoding="utf-8")

    _client_files(root / "chatter" / "clients" / SLUG, config)
    if config != "absent":
        (root / "chatter" / "clients" / SLUG / "drill.yaml").write_text(
            DRILL_SCENARIO, encoding="utf-8")

    # ВКЛЮЧЁННЫЙ сосед по реестру — с полным конфигом и СВОИМ именем
    # env-переменной токена. Без него уникальность имени токена (§2.1) не с чем
    # сверять: сосед, чей конфиг не читается, — это отдельная беда, и корень,
    # который её содержит, проверяет не то, что задумано.
    nb = root / "chatter" / "clients" / NEIGHBOUR_SLUG
    nb.mkdir(parents=True, exist_ok=True)
    (nb / "persona.md").write_text(PERSONA_MD, encoding="utf-8")
    (nb / "knowledge.md").write_text(KNOWLEDGE_MD, encoding="utf-8")
    (nb / "playbook.md").write_text(PLAYBOOK_MD, encoding="utf-8")
    (nb / "settings.yaml").write_text(
        settings_text(funnel_gate=False, allowlist=[OWNER_TG_ID],
                      token_env=NEIGHBOUR_TOKEN_ENV, persona="Ольга"),
        encoding="utf-8")

    build_dir = root / "build" / "onboard" / SLUG
    _client_files(build_dir, build_config)
    if build_config != "absent":
        report = {
            "schema_version": 1,
            "meta": {"slug": SLUG, "source": "brief.xlsx",
                     "files": ["persona.md", "knowledge.md", "playbook.md",
                               "settings.yaml"],
                     "notes": [], "flags_checked": True},
            "sections": {"taken": [], "defaulted": [], "missing": []},
            "flags": [],
            "counters": {},
            "role_wording_question": None,
            "sla_reality_question": None,
        }
        (build_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # Метка вычитки S2 + названные флаги S3 (флагов нет — называть нечего).
        (build_dir / "drill.yaml").write_text(DRILL_SCENARIO, encoding="utf-8")
        (build_dir / "REVIEWED").write_text(
            "Вычитано 2026-08-19, владелец. Флагов в report.json нет.\n",
            encoding="utf-8")

    if session:
        (root / ".secrets" / f"{SLUG}.session").write_bytes(b"SQLite format 3\x00")
    (root / ".secrets" / "demo.session").write_bytes(b"SQLite format 3\x00")
    (root / ".env.enc").write_bytes(b"\x01\x02encrypted")

    if consent:
        # §12.8.3: согласие живёт в state/connect/, а НЕ в каталоге клиента.
        # Внутри каталога оно было тупиком: человек, положивший согласие до
        # логина, создавал бы каталог с одним файлом, и S4 объявлял бы
        # ПРОТИВОРЕЧИЕ тому, кто всё сделал правильно.
        # §12.7.4: первая строка начинается с ISO-даты.
        day = time.strftime("%Y-%m-%d", time.localtime(now - 86400))
        (root / "state" / "connect" / f"{SLUG}.consent.md").write_text(
            f"{day} — согласие на доступ к личке аккаунта получено от "
            f"владельца студии (голосовое в Telegram).\n",
            encoding="utf-8")

    if bundle:
        (root / "state" / "connect" / f"{SLUG}.bundle.txt").write_text(
            f"сессии в бандле: demo.session.enc, {SLUG}.session.enc\n",
            encoding="utf-8")

    log = root / "logs" / f"chatter_{SLUG}.log"
    log.write_text(
        (f"2026-08-20 10:00:00 {ISOLATED_TOKEN_LINE}\n" if token_log else "")
        + "2026-08-20 10:00:01 runner up\n",
        encoding="utf-8")

    if live_state is not None:
        write_live_state(root, SLUG, state=live_state, now=now,
                         heartbeat_age=heartbeat_age)

    if drill_result:
        d = root / "state" / "drills" / SLUG
        d.mkdir(parents=True, exist_ok=True)
        (d / "20260820-090000.md").write_text(
            f"# Дрил: {SLUG}\nclient: {SLUG}\n"
            f"contact: {DRILL_CONTACT_ID}:{SLUG}\nEXPECT_KEYS: закрыт\n",
            encoding="utf-8")

    _ = token  # env собирается отдельно — см. make_env
    return root


def make_env(*, token: bool = True) -> dict[str, str]:
    env = {
        "TELEGRAM_API_ID": "1234567",
        "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
        "ANTHROPIC_API_KEY": "sk-ant-" + "x" * 40,
    }
    if token:
        env[TOKEN_ENV] = "8000000000:" + "A" * 35
    env[NEIGHBOUR_TOKEN_ENV] = "7000000000:" + "B" * 35
    return env


def make_ctx(root: Path, runner: FakeRunner, *, drill_yes: bool = False,
             drill_again: bool = False, token: bool = True,
             now: float | None = None) -> Ctx:
    return Ctx(
        root=Path(root),
        slug=SLUG,
        now=time.time() if now is None else now,
        env=make_env(token=token),
        # §12.8.4: поле называется `runner`, вызов — `ctx.runner.run(argv,
        # cwd=..., timeout=...)`. Прежнее имя `run` допускало две законные
        # трактовки сразу («объект с методом» и «сама функция»), и авторы
        # разошлись ровно на нём; алиаса больше нет, и подстраиваться не под
        # что — имя одно.
        runner=runner,
        drill_yes=drill_yes,
        drill_again=drill_again,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Снимки диска: Д13 и Д14 сторожат ОТСУТСТВИЕ действия, поэтому сравниваем
# БАЙТЫ, а не «был ли вызов с таким именем».
# ─────────────────────────────────────────────────────────────────────────────

def digest_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def active_digest(root: Path) -> str | None:
    p = Path(root) / ACTIVE_REL
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def assert_active_untouched(root: Path, before: str | None, where: str) -> None:
    """Д13. Сравниваются БАЙТЫ: правка комментария, дописанная строка и
    полное удаление файла одинаково красные."""
    now = active_digest(root)
    assert now == before, (
        f"Д13: {ACTIVE_REL} изменился ({where}). Это ложный канал (§0-бис): "
        f"гардиан его не читает, а правка во время подключения — дефект, "
        f"а не недоделка. было={before} стало={now}")


def gate_true_hits(root: Path) -> list[str]:
    """Все места на диске, где `funnel_gate` стоит в истине.

    Ищем ДВУМЯ способами сразу — разбором YAML и сырым текстом. Разбор
    пропустил бы гейт, дописанный в закомментированный блок или в файл, который
    `load_config` не читает; сырой текст пропустил бы `funnel_gate: !!bool yes`.
    """
    hits: list[str] = []
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".yaml", ".yml"):
            continue
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _GATE_TRUE_RE.search(text):
            hits.append(f"{rel} (текст)")
            continue
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if isinstance(doc, dict):
            tg = doc.get("telegram")
            if isinstance(tg, dict) and tg.get("funnel_gate") is True:
                hits.append(f"{rel} (yaml)")
    return hits


def assert_gate_closed(root: Path, where: str, baseline=()) -> None:
    """Д14 на диске.

    Сравнение с БАЗОЙ, а не с пустотой: гейт, открытый человеком ДО прогона
    (случай Д8 — живой клиент в работе), нашей правкой не является, и сторож,
    краснеющий на нём, кричал бы о чужом решении. Красное здесь — НОВОЕ
    открытие, ровно то, что запрещает Д14.
    """
    hits = [h for h in gate_true_hits(root) if h not in set(baseline)]
    assert hits == [], (
        f"Д14: `funnel_gate` переведён в true ({where}): {hits}. "
        f"Открытие трафика — команда владельца `/funnel_gate on confirm` "
        f"и вне команды подключения навсегда (§5.5); за ним стоит catch-up "
        f"по непрочитанному за сутки.")


def assert_gate_not_commanded(runner: FakeRunner, where: str,
                              root: Path | None = None) -> None:
    """Д14 снаружи: гейт можно открыть и чужой командой через пульт.

    Аргументы, которые являются ПУТЯМИ внутрь корня, из проверки исключены:
    каталог pytest называется по имени теста, и имя `..._funnel_gate_...`
    иначе краснит сторожа на безобидном `--registry <путь>`. Это ровно тот
    класс, где сторож ловит СЕБЯ.
    """
    marker = str(root) if root is not None else None
    bad = []
    for call in runner.calls:
        for arg in call:
            a = str(arg)
            if marker and marker in a:
                continue
            if "funnel_gate" in a.lower():
                bad.append(call)
                break
    assert bad == [], (
        f"Д14: команда подключения зовёт `funnel_gate` ({where}): {bad}")


def assert_no_force(runner: FakeRunner, where: str) -> None:
    """Д4: `-Force` пропускает замер радиуса ОСОЗНАННО — осознанность здесь
    человеческая, автомат её подставить не может."""
    for call in runner.calls:
        for arg in call:
            assert not _FORCE_RE.match(str(arg).strip()), (
                f"Д4: `-Force` подставлен автоматически ({where}): {call}. "
                f"У нового клиента радиус нулевой по построению; если он не "
                f"нулевой — это ровно то, что надо прочитать глазами.")
        assert "-force" not in " ".join(call).lower(), (
            f"Д4: `-Force` просочился в аргументы ({where}): {call}")


# ─────────────────────────────────────────────────────────────────────────────
# Конвейер §12.3 — реализован ЗДЕСЬ, по спеке, а не взят у проверяемого кода
# ─────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Outcome:
    code: int
    stopped_at: str | None
    verdicts: list[tuple[str, str]]
    acted: list[str]
    errors: list[str]
    # Последний StepResult каждого шага — сторожа §12.6.2 читают `facts`.
    last: dict = dataclasses.field(default_factory=dict)
    # То, что вернуло ДЕЙСТВИЕ шага. Отдельно от пробы намеренно: причину
    # «замер сказал нет» против «замер не состоялся» знает только действие —
    # с диска после неудачи обе выглядят одинаково.
    last_act: dict = dataclasses.field(default_factory=dict)


def _verdict_of(result, step_id: str, phase: str) -> Verdict:
    assert isinstance(result, StepResult) or hasattr(result, "verdict"), (
        f"{step_id}/{phase}: ожидался StepResult (§12.2), получено {result!r}")
    return Verdict(result.verdict)


TAIL_HUMAN_STEPS = ("S14", "S15")


def loop_steps():
    """Шаги ЦИКЛА §12.3 — S0…S13.

    S14 и S15 хвостовые человеческие и в цикл не входят (§12.8.1): фактом на
    диске они не закрываются, и буквальный цикл упирался бы в код 3 всегда,
    то есть код 0 был бы недостижим.
    """
    return [s for s in STEPS if s.id not in TAIL_HUMAN_STEPS]


def _waits(result) -> bool:
    return bool(getattr(result, "waits_for_human", False))


def run_pipeline(ctx: Ctx, *, watch=None) -> Outcome:
    """Ровно порядок §12.3 с поправками §12.7.1 и §12.8.1.

    `watch(step_id, phase)` зовётся после КАЖДОГО шага — так Д13/Д14
    проверяются пошагово, а не только в конце: правка, сделанная на S9 и
    отменённая на S12, обязана быть красной.
    """
    verdicts: list[tuple[str, str]] = []
    acted: list[str] = []
    errors: list[str] = []
    last: dict = {}
    last_act: dict = {}

    def _tick(step_id: str, phase: str) -> None:
        if watch is not None:
            watch(step_id, phase)

    for step in loop_steps():
        try:
            r = step.probe(ctx)
            v = _verdict_of(r, step.id, "probe")
        except Exception as exc:  # noqa: BLE001 — проба обязана вернуть вердикт
            errors.append(
                f"{step.id}: проба подняла {type(exc).__name__}: {exc}. "
                f"Беду внешней команды ловит вызывающая проба и превращает в "
                f"CONFLICT с внятным why (§12.6.3); трассировка в лицо "
                f"человеку — это отказ, который ничего не объясняет (§12.6.5).")
            _tick(step.id, "probe-raised")
            return Outcome(2, step.id, verdicts, acted, errors, last, last_act)
        verdicts.append((step.id, v.value))
        last[step.id] = r
        _tick(step.id, "probe")

        if v is Verdict.CONFLICT:
            return Outcome(1, step.id, verdicts, acted, errors, last, last_act)
        if v is Verdict.CLOSED:
            continue
        if Owner(step.owner) is Owner.HUMAN:
            return Outcome(3, step.id, verdicts, acted, errors, last, last_act)

        try:
            last_act[step.id] = step.act(ctx)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"{step.id}: действие подняло {type(exc).__name__}: {exc} "
                f"(§12.6.3/§12.6.5: беда внешней команды обязана стать "
                f"CONFLICT, а не трассировкой)")
            acted.append(step.id)
            _tick(step.id, "act-raised")
            return Outcome(1, step.id, verdicts, acted, errors, last, last_act)
        acted.append(step.id)
        _tick(step.id, "act")

        try:
            r2 = step.probe(ctx)
            v2 = _verdict_of(r2, step.id, "reprobe")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{step.id}: повторная проба подняла {type(exc).__name__}: {exc}")
            _tick(step.id, "reprobe-raised")
            return Outcome(2, step.id, verdicts, acted, errors, last, last_act)
        verdicts.append((step.id, v2.value))
        last[step.id] = r2
        _tick(step.id, "reprobe")
        if v2 is not Verdict.CLOSED:
            # §12.7.1: «жду разрешения человека» — это код 3, а не «сломано».
            # Денежные ворота S13 иначе печатали бы ПРОТИВОРЕЧИЕ на штатном
            # пути и приучали не смотреть на красное — ровно то, чего велел
            # избежать владелец решением q2.
            waits = _waits(r2) or _waits(last_act.get(step.id))
            return Outcome(3 if waits else 1, step.id, verdicts, acted,
                           errors, last, last_act)

    return Outcome(0, None, verdicts, acted, errors, last, last_act)


def guarded_run(ctx: Ctx, runner: FakeRunner, *, label: str) -> Outcome:
    """Прогон с Д13/Д14, снимаемыми ПОСЛЕ КАЖДОГО шага."""
    before_active = active_digest(ctx.root)
    gate_baseline = gate_true_hits(ctx.root)

    def watch(step_id: str, phase: str) -> None:
        where = f"{label}: после {step_id}/{phase}"
        assert_active_untouched(ctx.root, before_active, where)
        assert_gate_closed(ctx.root, where, gate_baseline)
        assert_gate_not_commanded(runner, where, ctx.root)
        assert_no_force(runner, where)

    outcome = run_pipeline(ctx, watch=watch)
    watch(outcome.stopped_at or "конец", "итог")
    return outcome


FLAG_COMBOS = [(False, False), (True, False), (False, True), (True, True)]
FLAG_IDS = ["без-флагов", "--drill-yes", "--drill-again", "оба-флага"]


def step_by_id(step_id: str):
    for s in STEPS:
        if s.id == step_id:
            return s
    raise AssertionError(f"в STEPS нет шага {step_id}; есть: {[s.id for s in STEPS]}")


# ─────────────────────────────────────────────────────────────────────────────
# МЕТА: контракт шагов
# ─────────────────────────────────────────────────────────────────────────────

def test_meta_steps_are_the_literal_ordered_map_of_the_spec():
    """§12.1: STEPS — литеральный кортеж в порядке исполнения.

    Список написан ЛИТЕРАЛОМ ([[jarvis-literal-lists-not-introspection]]):
    выведенный из реализации согласился бы с ней по определению и промолчал бы
    ровно там, где она забыла шаг.
    """
    got = [s.id for s in STEPS]
    loop = [f"S{i}" for i in range(14)]
    assert got[:14] == loop, (
        "порядок шагов из §3 менять нельзя: каждая строка опирается на факт "
        f"предыдущей. ожидались первыми {loop}, получено {got}")
    # S14/S15 — хвостовые человеческие (§12.8.1). Держать их в STEPS законно
    # (карта шагов для `--plan`), не держать — тоже; но если они есть, они
    # ПОСЛЕДНИЕ и в своём порядке, иначе цикл S0…S13 вырезал бы середину.
    assert got[14:] in ([], ["S14"], ["S14", "S15"]), (
        f"хвост STEPS = {got[14:]}; ожидались S14/S15 в этом порядке или ничего")
    assert [s.id for s in loop_steps()] == loop


def test_contract_ctx_field_for_the_runner_is_named_runner():
    """§12.8.4: поле контекста называется `runner`, вызов —
    `ctx.runner.run(argv, cwd=..., timeout=...)`.

    Имя `run` допускало ДВЕ законные трактовки — «объект с методом» и «сама
    функция», — и авторы разошлись именно на нём. Это не косметика: сторож,
    подставивший объект, и код, звавший его как функцию, встретились бы только
    на живом подъёме.
    """
    names = [f.name for f in dataclasses.fields(Ctx)]
    assert "runner" in names, (
        f"в Ctx нет поля `runner` (§12.8.4); поля: {names}")
    assert "run" not in names, (
        "старое имя `run` осталось в Ctx рядом с `runner` — два имени на одну "
        "вещь, и меньшее погасит большее молча")


def test_meta_auto_iff_act_and_exactly_six_auto_steps():
    """§3: AUTO ⇔ у шага есть `act`. Автошагов ровно шесть.

    Это предусловие всех переборов ниже: «ни при каком пути» проверяется по
    ВСЕМ шагам, и если автошагов вдруг стало семь, седьмой не проверен никем.
    """
    auto = [s.id for s in STEPS if Owner(s.owner) is Owner.AUTO]
    with_act = [s.id for s in STEPS if s.act is not None]
    assert set(auto) == {"S4", "S9", "S10", "S11", "S12", "S13"}, (
        f"по §3 автоматических шагов ровно шесть (S4, S9, S10, S11, S12, S13); "
        f"в STEPS: {auto}")
    assert set(with_act) == set(auto), (
        f"AUTO ⇔ act (§3/§12.2) нарушен: owner=AUTO у {auto}, act есть у "
        f"{with_act}. Шаг без действия — человеческий по определению; шаг с "
        f"действием, помеченный человеком, обойдёт весь порядок §12.3.")


# ─────────────────────────────────────────────────────────────────────────────
# МЕТА: сторожа на ОТСУТСТВИЕ действия обязаны уметь краснеть
# ─────────────────────────────────────────────────────────────────────────────

def test_meta_active_yaml_guard_reddens_on_a_single_byte(tmp_path):
    """Проверка проверки (Д13): помощник обязан покраснеть от ОДНОГО байта."""
    root = make_root(tmp_path)
    before = active_digest(root)
    assert_active_untouched(root, before, "контроль")  # зелёный на нетронутом

    p = root / ACTIVE_REL
    p.write_text(p.read_text(encoding="utf-8") + "# правка\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_active_untouched(root, before, "мутант: дописана строка")


def test_meta_active_yaml_guard_reddens_on_deletion(tmp_path):
    root = make_root(tmp_path)
    before = active_digest(root)
    (root / ACTIVE_REL).unlink()
    with pytest.raises(AssertionError):
        assert_active_untouched(root, before, "мутант: файл удалён")


@pytest.mark.parametrize("payload", ["true", "True", "yes", "on"])
def test_meta_funnel_gate_guard_reddens_on_open_gate(tmp_path, payload):
    """Проверка проверки (Д14): гейт, открытый РУКАМИ ТЕСТА, обязан краснеть —
    в любом из написаний, которые YAML читает как истину."""
    root = make_root(tmp_path)
    assert_gate_closed(root, "контроль")  # зелёный на закрытом

    p = root / "chatter" / "clients" / SLUG / "settings.yaml"
    text = p.read_text(encoding="utf-8").replace(
        "funnel_gate: false", f"funnel_gate: {payload}")
    assert "funnel_gate: false" not in text
    p.write_text(text, encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_gate_closed(root, f"мутант: funnel_gate: {payload}")


def test_meta_force_guard_reddens_on_a_planted_force(tmp_path):
    """Проверка проверки (Д4)."""
    root = make_root(tmp_path)
    runner = FakeRunner(root)
    assert_no_force(runner, "контроль")
    runner.calls.append(["powershell", "scripts/chatter_client.ps1", "-Slug",
                         SLUG, "-Action", "start", "-Force"])
    with pytest.raises(AssertionError):
        assert_no_force(runner, "мутант: подставлен -Force")


# ─────────────────────────────────────────────────────────────────────────────
# МЕТА: конвейеру должно быть ЧТО делать, иначе Д13/Д14 зелены по построению
# ─────────────────────────────────────────────────────────────────────────────

def test_meta_healthy_root_reaches_the_raise_step(tmp_path):
    """Здоровый корень обязан ДОЙТИ до подъёма (S11) и позвать
    `chatter_client.ps1 -Action start`.

    Если этот тест красный — все утверждения вида «на диске ничего не
    изменилось» ниже ничего не доказывают: конвейер просто не дошёл до места,
    где мог бы изменить.
    """
    root = make_root(tmp_path)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner)
    outcome = run_pipeline(ctx)

    assert outcome.errors == [], outcome.errors
    assert "S11" in outcome.acted, (
        f"конвейер не дошёл до подъёма: остановился на {outcome.stopped_at}, "
        f"вердикты {outcome.verdicts}")
    assert runner.start_calls, (
        f"S11 обязан звать scripts/chatter_client.ps1 -Action start "
        f"(§2.3: замер радиуса вшит в него, своей копии быть не должно). "
        f"вызовы: {runner.calls}")
    assert registry_enabled(root, SLUG) is True, (
        "после успешного подъёма в реестре ожидается enabled: true")


def test_meta_healthy_root_reaches_the_drill_when_paid_run_is_allowed(tmp_path):
    """С `--drill-yes` конвейер обязан дойти до S13 и позвать харнесс.

    Это второй якорь: без него параметризация «ни при каком флаге» у Д14
    проверяла бы одно и то же место четыре раза.
    """
    root = make_root(tmp_path)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, drill_yes=True)
    outcome = run_pipeline(ctx)

    assert outcome.errors == [], outcome.errors
    assert "S13" in outcome.acted, (
        f"конвейер не дошёл до дрила: остановился на {outcome.stopped_at}, "
        f"вердикты {outcome.verdicts}")
    assert runner.drill_calls, f"S13 обязан звать харнесс. вызовы: {runner.calls}"


# ─────────────────────────────────────────────────────────────────────────────
# Д2 — порядок: `enabled: true` при отсутствии конфига или сессии
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flags", FLAG_COMBOS, ids=FLAG_IDS)
@pytest.mark.parametrize(
    "damage,expect_stop",
    [
        pytest.param({"config": "broken", "build_config": "broken"}, "S4",
                     id="конфиг-не-грузится"),
        pytest.param({"config": "absent", "build_config": "absent"}, "S1",
                     id="конфига-нет-вовсе"),
        pytest.param({"session": False}, "S6", id="сессии-нет"),
    ],
)
def test_d2_enabled_true_is_never_set_without_config_or_session(
        tmp_path, damage, expect_stop, flags):
    """Д2. `enabled: true` при битом конфиге = гардиан поднимает и роняет
    раннер по кругу; без сессии — вечный рестарт, процесс умирает на старте.

    Проверяется НЕ «стоп в нужном месте», а инвариант на КАЖДОМ шаге: реестр
    не получил `enabled: true` ни разу, даже на мгновение, и подъём не звался.
    """
    drill_yes, drill_again = flags
    root = make_root(tmp_path, registry_entry=None, **damage)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, drill_yes=drill_yes, drill_again=drill_again)

    seen: list[str] = []

    def watch(step_id: str, phase: str) -> None:
        seen.append(f"{step_id}/{phase}")
        assert registry_enabled(root, SLUG) is not True, (
            f"Д2: enabled: true выставлен при {damage} (после {step_id}/{phase}). "
            f"Гардиан читает реестр каждые ~30 с и уйдёт в цикл подъёма.")
        assert not runner.start_calls, (
            f"Д2: подъём позван при {damage} (после {step_id}/{phase}): "
            f"{runner.start_calls}")

    outcome = run_pipeline(ctx, watch=watch)
    watch(outcome.stopped_at or "конец", "итог")

    assert outcome.errors == [], outcome.errors
    assert registry_enabled(root, SLUG) is not True
    assert not runner.start_calls
    assert not runner.drill_calls, (
        f"Д2: платный дрил при {damage} — деньги за прогон, который ничего "
        f"не доказывает: {runner.drill_calls}")
    assert outcome.stopped_at == expect_stop, (
        f"останов ожидался на {expect_stop}, получен на {outcome.stopped_at}; "
        f"вердикты: {outcome.verdicts}. Останов НЕ ТАМ так же плох, как его "
        f"отсутствие: человеку называют не ту работу.")


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param({"config": "broken", "build_config": "broken"}, id="конфиг-бит"),
        pytest.param({"session": False}, id="сессии-нет"),
    ],
)
def test_d2_registry_already_enabled_without_config_is_a_conflict(tmp_path, damage):
    """Д2, обратная сторона: реестр УЖЕ включён, а конфига/сессии нет.

    §1 называет это ПРОТИВОРЕЧИЕМ дословно («реестр включён, а конфига нет»).
    Команда обязана сказать «разбирайся» (код 1), а не молча пойти дальше и не
    списать это на «жду тебя» (код 3): для автоматики и для человека это
    разные события.
    """
    root = make_root(tmp_path, registry_entry="enabled", **damage)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner)

    outcome = run_pipeline(ctx)

    assert outcome.errors == [], outcome.errors
    assert outcome.code == 1, (
        f"ожидался код 1 (ПРОТИВОРЕЧИЕ), получен {outcome.code} на "
        f"{outcome.stopped_at}; вердикты: {outcome.verdicts}")
    assert not runner.start_calls, "при противоречии подъёма быть не должно"
    assert not runner.drill_calls, "при противоречии платного прогона быть не должно"


@pytest.mark.parametrize("step_id", ["S10", "S11"])
@pytest.mark.parametrize(
    "damage",
    [
        pytest.param({"config": "broken", "build_config": "broken"}, id="конфиг-бит"),
        pytest.param({"config": "absent", "build_config": "absent"}, id="конфига-нет"),
        pytest.param({"session": False}, id="сессии-нет"),
    ],
)
def test_d2_act_refuses_on_its_own_when_called_out_of_order(tmp_path, step_id, damage):
    """Д2, «НИ ПРИ КАКОМ пути»: у действия обязаны быть СВОИ предусловия.

    Порядок §12.3 — не единственный путь к действию: мутация, снявшая проверку
    порядка (S10 до S4, §8), обязана краснеть здесь. Поэтому `act` зовётся
    напрямую, вне очереди, на битом корне — и не имеет права включить клиента.

    Предусловия названы в §2.1 поимённо: «конфиг грузится НАСТОЯЩИМ
    load_config» и «сессия открывается».
    """
    root = make_root(tmp_path, registry_entry="disabled", **damage)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner)
    step = step_by_id(step_id)
    assert step.act is not None, f"{step_id} — автошаг, у него обязан быть act"

    try:
        step.act(ctx)
    except Exception:
        # Громкий отказ — законный способ отказаться (DEV-18). Молчаливый
        # успех — нет; его ловят проверки ниже.
        pass

    assert registry_enabled(root, SLUG) is not True, (
        f"Д2: {step_id}.act включил клиента при {damage}, будучи позван вне "
        f"очереди. Порядок обязан держаться предусловием шага, а не только "
        f"циклом §12.3.")
    assert not runner.start_calls, (
        f"Д2: {step_id}.act позвал подъём при {damage}: {runner.start_calls}")


# ─────────────────────────────────────────────────────────────────────────────
# Д4 — радиус переответа и `-Force`
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("start_rc", [1, 2, 9])
@pytest.mark.parametrize("flags", FLAG_COMBOS, ids=FLAG_IDS)
def test_d4_radius_refusal_stops_the_raise(tmp_path, start_rc, flags):
    """Д4. `scripts/chatter_client.ps1` отказывает в подъёме двумя способами
    (`rc 1` — «нашли, кого переответят», прочий ненулевой — «замер НЕ
    СОСТОЯЛСЯ»), и оба одинаково останавливают подъём: молчание инструмента не
    имеет права читаться как разрешение.

    Цена известна: рестарт Ярины 17.08 заставил catch-up ответить на сообщение
    13 ч 49 мин давности в ЭСКАЛИРОВАННОМ диалоге, поверх человека.
    """
    drill_yes, drill_again = flags
    root = make_root(tmp_path)
    runner = FakeRunner(root, start_rc=start_rc)
    ctx = make_ctx(root, runner, drill_yes=drill_yes, drill_again=drill_again)

    outcome = guarded_run(ctx, runner, label=f"отказ замера rc={start_rc}")

    assert outcome.errors == [], outcome.errors
    assert outcome.stopped_at == "S11", (
        f"отказ замера обязан останавливать подъём (S11), а конвейер "
        f"остановился на {outcome.stopped_at}; вердикты: {outcome.verdicts}")
    assert outcome.code == 1, (
        f"после действия проба обязана доказать факт, а не «act не упал» "
        f"(§12.3): ожидался код 1, получен {outcome.code}")
    assert len(runner.start_calls) == 1, (
        f"Д4: отказ замера = ОСТАНОВКА, а не повтор. Попыток подъёма: "
        f"{len(runner.start_calls)} — {runner.start_calls}")
    assert not runner.drill_calls, (
        f"Д4: после несостоявшегося подъёма платный дрил невозможен: "
        f"{runner.drill_calls}")
    assert_no_force(runner, f"отказ замера rc={start_rc}")


@pytest.mark.parametrize("flags", FLAG_COMBOS, ids=FLAG_IDS)
@pytest.mark.parametrize("start_rc", [0, 1, 2])
def test_d4_force_is_never_substituted_in_any_branch(tmp_path, flags, start_rc):
    """Д4. `-Force` пропускает замер ОСОЗНАННО — и осознанность здесь
    человеческая. Скрипт САМ печатает подсказку «подними осознанно: -Action
    start -Force» (она есть в выводе фейка), поэтому соблазн назван и проверен.

    Перебираются обе ветки подъёма (замер прошёл / замер отказал) и все
    сочетания флагов: «ни в одной ветке» проверяется перебором, а не примером.
    """
    drill_yes, drill_again = flags
    root = make_root(tmp_path)
    runner = FakeRunner(root, start_rc=start_rc)
    ctx = make_ctx(root, runner, drill_yes=drill_yes, drill_again=drill_again)

    run_pipeline(ctx)
    assert_no_force(runner, f"ветка rc={start_rc}, флаги {flags}")


def test_d4_force_is_never_substituted_by_any_single_act(tmp_path):
    """Д4, перебором по ВСЕМ автошагам: ни одно действие в отдельности не
    подставляет `-Force`, даже будучи позванным вне очереди."""
    for step in STEPS:
        if step.act is None:
            continue
        # live_state=alive: у S12 своё действие — ОГРАНИЧЕННОЕ ОЖИДАНИЕ
        # (§12.6.6). На корне, где живости нет, оно честно выждет свой
        # предел, и сторож превратился бы в паузу.
        root = make_root(tmp_path / f"act-{step.id}", live_state="alive",
                         heartbeat_age=2.0)
        runner = FakeRunner(root, start_rc=1)
        ctx = make_ctx(root, runner, drill_yes=True, drill_again=True)
        try:
            step.act(ctx)
        except Exception:
            pass
        assert_no_force(runner, f"{step.id}.act в одиночку")


def test_d4_measurement_that_could_not_run_is_not_a_permission(tmp_path):
    """Д4 + §12.6.3. `CommandRunner` при беде БРОСАЕТ исключение (таймаут,
    ненайденный исполняемый), а не возвращает выдуманный `rc`.

    Проверяется трижды:
      * исключение НЕ вылетает наружу — его ловит проба/действие и превращает
        в ПРОТИВОРЕЧИЕ (§12.6.5: человеку, пришедшему подключать клиента,
        трассировка ничего не объясняет);
      * подъём остановлен, дрил не запущен;
      * `-Force` не подставлен — «не смогли посмотреть» не равно «чисто».
    """
    root = make_root(tmp_path)
    runner = FakeRunner(root, start_raises=True)
    ctx = make_ctx(root, runner, drill_yes=True)

    outcome = run_pipeline(ctx)

    assert outcome.errors == [], (
        "Д4/§12.6.3: беда внешней команды вылетела трассировкой наружу вместо "
        f"ПРОТИВОРЕЧИЯ с внятным why: {outcome.errors}")
    assert outcome.code == 1, (
        f"ожидалось ПРОТИВОРЕЧИЕ (код 1), получен {outcome.code} на "
        f"{outcome.stopped_at}; вердикты: {outcome.verdicts}")
    assert outcome.stopped_at == "S11"
    assert registry_enabled(root, SLUG) is not True, (
        "Д2/Д4: клиент включён, хотя замер не состоялся")
    assert not runner.drill_calls
    assert_no_force(runner, "замер не состоялся")


def _step_results(outcome: Outcome, step_id: str) -> list:
    """Всё, что шаг сказал о себе: результат действия И результат пробы.

    Контракт §12.6.2 не говорит, в чьих `facts` живут ключи шага, — и не обязан:
    у пробы и действия одна форма (`StepResult`). Сторож, выбравший одно из
    двух мест, покраснел бы на законной реализации, поэтому смотрим оба.
    """
    out = []
    for src in (outcome.last_act.get(step_id), outcome.last.get(step_id)):
        if isinstance(src, StepResult) or hasattr(src, "facts"):
            out.append(src)
    return out


def _merged_facts(outcome: Outcome, step_id: str) -> dict:
    facts: dict = {}
    for r in reversed(_step_results(outcome, step_id)):
        facts.update(dict(getattr(r, "facts", None) or {}))
    return facts


def test_d4_failed_measurement_and_refused_measurement_are_told_apart(tmp_path):
    """Д4 + §12.6.3. «Замер сказал нет» и «замер не состоялся» обязаны
    различаться в тексте остановки.

    Слипшись, они дают один и тот же совет на два разных события: в первом
    случае человек должен ответить клиенту сам, во втором — чинить инструмент.
    Сверяется НЕ дословный текст (сторож на буквы превратил бы правку
    формулировки в красное), а лишь то, что тексты разные и оба непусты.
    """
    def _s11_texts(runner_kwargs, sub):
        root = make_root(tmp_path / sub)
        runner = FakeRunner(root, **runner_kwargs)
        ctx = make_ctx(root, runner)
        outcome = run_pipeline(ctx)
        assert outcome.errors == [], outcome.errors
        results = _step_results(outcome, "S11")
        assert results, (
            f"S11 не дал результата; остановка на {outcome.stopped_at}, "
            f"вердикты: {outcome.verdicts}")
        for r in results:
            assert str(r.why).strip(), f"{sub}: пустое ПОЧЕМУ"
            assert str(r.todo).strip(), f"{sub}: пустое ЧТО СДЕЛАТЬ (Д11)"
        return {(str(r.why).strip(), str(r.todo).strip()) for r in results}

    said_no = _s11_texts({"start_rc": 1}, "сказал-нет")
    did_not_run = _s11_texts({"start_raises": True}, "не-состоялся")

    assert said_no != did_not_run, (
        "Д4/§12.6.3: «замер сказал нет» и «замер не состоялся» объяснены "
        f"одними и теми же словами: {sorted(said_no)}. Совет человеку в этих "
        f"двух случаях разный: ответить клиенту самому — против починить "
        f"инструмент.")


@pytest.mark.parametrize(
    "kwargs,radius_ok,enabled,name",
    [
        ({}, True, True, "замер-прошёл"),
        ({"start_rc": 1}, False, False, "замер-сказал-нет"),
        ({"start_raises": True}, False, False, "замер-не-состоялся"),
    ],
)
def test_d4_s11_facts_name_enabled_and_radius_ok(tmp_path, kwargs, radius_ok,
                                                 enabled, name):
    """§12.6.2: у S11 обязательны ключи `enabled` и `radius_ok`.

    Это и есть машиночитаемая улика Д4: без неё «шаг не закрыт» ничего не
    говорит о ПРИЧИНЕ, и подъём, сорвавшийся по любой другой причине,
    выглядел бы точно так же.
    """
    root = make_root(tmp_path / name)
    runner = FakeRunner(root, **kwargs)
    ctx = make_ctx(root, runner)
    outcome = run_pipeline(ctx)

    assert outcome.errors == [], outcome.errors
    assert _step_results(outcome, "S11"), (
        f"S11 не дал результата; вердикты: {outcome.verdicts}")
    facts = _merged_facts(outcome, "S11")
    missing = [k for k in ("enabled", "radius_ok") if k not in facts]
    assert not missing, f"S11.facts без обязательных ключей {missing}: {facts}"
    assert bool(facts["radius_ok"]) is radius_ok, (
        f"S11.facts['radius_ok'] = {facts['radius_ok']!r} при «{name}»")
    assert bool(facts["enabled"]) is enabled, (
        f"S11.facts['enabled'] = {facts['enabled']!r} при «{name}»")


def test_d4_connect_does_not_keep_its_own_copy_of_the_radius_probe(tmp_path):
    """Д4. Замер вшит в `chatter_client.ps1 -Action start` (требование
    владельца 17.08). Своей копии замера в подключении быть не должно: две
    правды об одном разъедутся, и разъедутся молча.
    """
    root = make_root(tmp_path)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner)
    run_pipeline(ctx)

    own = runner.calls_matching("chatter_catchup_radius")
    assert own == [], (
        f"Д4: подключение зовёт замер радиуса СВОЕЙ копией: {own}. "
        f"Замер живёт внутри chatter_client.ps1 (§2.3).")
    assert runner.start_calls, "подъём обязан идти через chatter_client.ps1"


# ─────────────────────────────────────────────────────────────────────────────
# Д8 — живой клиент не трогаем
# ─────────────────────────────────────────────────────────────────────────────

def _alive_open_gate_root(tmp_path, *, alive: bool, gate: bool) -> Path:
    """Корень, где подключение НЕ ЗАВЕРШЕНО (allowlist пуст, записи в реестре
    нет), но клиент, возможно, уже живой и с открытым гейтом.

    Незавершённость намеренная: у полностью закрытого подключения команда и
    так ничего не делает (идемпотентность Д12), и Д8 на нём был бы зелен по
    построению. Опасен именно ЭТОТ случай — «доподключение» живого.
    """
    return make_root(
        tmp_path,
        registry_entry="enabled" if alive else None,
        allowlist=[],
        funnel_gate=gate,
        live_state="alive" if alive else "down",
        heartbeat_age=5.0 if alive else 4000.0,
    )


@pytest.mark.parametrize("flags", FLAG_COMBOS, ids=FLAG_IDS)
def test_d8_alive_client_with_open_gate_is_refused_entirely(tmp_path, flags):
    """Д8. Слаг уже `alive`, гейт открыт — команда отказывается ЦЕЛИКОМ.

    «Целиком» здесь буквально: ни одного действия, ни одного внешнего вызова,
    меняющего живое, и ни одного изменённого байта в конфиге и реестре. Слово
    «доподключить» звучит безобидно ровно до момента, когда allowlist,
    дописанный на живом клиенте, впустит в открытую воронку дрил-контакт.
    """
    drill_yes, drill_again = flags
    root = _alive_open_gate_root(tmp_path, alive=True, gate=True)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, drill_yes=drill_yes, drill_again=drill_again)

    before = digest_tree(root)
    outcome = guarded_run(ctx, runner, label="живой клиент с открытым гейтом")
    after = digest_tree(root)
    # Гейт в этой фикстуре открыт ЧЕЛОВЕКОМ до прогона — база непуста, и
    # guarded_run сравнивает именно с ней.

    assert outcome.errors == [], outcome.errors
    assert outcome.acted == [], (
        f"Д8: на живом клиенте выполнены действия {outcome.acted}. "
        f"Отказ обязан наступить ДО первого действия.")
    assert outcome.code == 1, (
        f"Д8: ожидался отказ (код 1 — ПРОТИВОРЕЧИЕ), получен {outcome.code} "
        f"на {outcome.stopped_at}; вердикты: {outcome.verdicts}. Код 0 здесь "
        f"означал бы «готово» на клиенте, подключение которого не доведено, "
        f"а код 3 — «жду тебя» там, где ждать нечего.")
    assert not runner.start_calls, f"Д8: подъём живого: {runner.start_calls}"
    assert not runner.drill_calls, (
        f"Д8: дрил в БД живого клиента — посторонний трафик и деньги: "
        f"{runner.drill_calls}")

    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    # Журнал (§1) пишется для человека и меняться вправе — всё остальное нет.
    changed -= {p for p in changed if p.startswith("state/connect/")}
    assert changed == set(), (
        f"Д8: на диске живого клиента изменилось: {sorted(changed)}")


def test_meta_d8_same_root_without_the_live_client_does_act(tmp_path):
    """Якорь для Д8: ТОТ ЖЕ корень, но клиент не живой и гейт закрыт —
    конвейер обязан работать.

    Без этой пары Д8 зелен по построению: «ничего не сделал» — это ровно то,
    что делает сломанный конвейер на любом корне.
    """
    root = _alive_open_gate_root(tmp_path, alive=False, gate=False)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner)

    outcome = run_pipeline(ctx)

    assert outcome.errors == [], outcome.errors
    assert outcome.acted, (
        f"на неживом клиенте с пустым allowlist конвейер обязан действовать; "
        f"остановился на {outcome.stopped_at}, вердикты: {outcome.verdicts}")


@pytest.mark.parametrize(
    "alive,gate,name",
    [
        (True, True, "жив-и-гейт-открыт"),
        (True, False, "жив-гейт-закрыт"),
    ],
)
def test_d8_live_client_is_never_raised_again(tmp_path, alive, gate, name):
    """Д8, узкая часть: живого клиента не поднимают повторно ни при каком
    состоянии гейта. Повторный `-Action start` на живом — это рестарт, а
    рестарт гонит catch-up по непрочитанному."""
    root = _alive_open_gate_root(tmp_path, alive=alive, gate=gate)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner)

    run_pipeline(ctx)

    assert not runner.start_calls, (
        f"Д8 ({name}): подъём уже живого клиента: {runner.start_calls}")


# ─────────────────────────────────────────────────────────────────────────────
# Д10 — приёмка живости
# ─────────────────────────────────────────────────────────────────────────────

def _s12_result(tmp_path, *, state, heartbeat_age, token_log=True,
                write_state=True) -> StepResult:
    now = time.time()
    root = make_root(
        tmp_path,
        registry_entry="enabled",
        live_state=state if write_state else None,
        heartbeat_age=heartbeat_age,
        token_log=token_log,
        now=now,
    )
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, now=now)
    return step_by_id("S12").probe(ctx)


def _s12_verdict(tmp_path, **kw) -> Verdict:
    return Verdict(_s12_result(tmp_path, **kw).verdict)


def test_d10_alive_with_fresh_heartbeat_and_token_line_is_closed(tmp_path):
    """Якорь Д10: здоровая живость обязана ЗАКРЫВАТЬ шаг.

    Без него все «не закрыт» ниже проходили бы и на пробе, которая не
    закрывается никогда, — то есть сторож молчал бы о настоящем дефекте.
    """
    v = _s12_verdict(tmp_path, state="alive", heartbeat_age=5.0)
    assert v is Verdict.CLOSED, (
        f"alive + свежий heartbeat + строка `{ISOLATED_TOKEN_LINE}` — это "
        f"закрытая приёмка живости (§2.2), получено: {v}")


def test_d10_starting_is_not_alive(tmp_path):
    """Д10. `starting` за `alive` не считается.

    Гардиан ставит `starting` в момент запуска процесса, ДО того как раннер
    хоть раз отписался. Принять его за живость — значит объявить подключение
    состоявшимся на процессе, который может умереть на первой же строке.
    """
    v = _s12_verdict(tmp_path, state="starting", heartbeat_age=5.0)
    assert v is not Verdict.CLOSED, (
        "Д10: `starting` принят за `alive` — приёмка закрылась на процессе, "
        "который ещё ничего о себе не сказал")


@pytest.mark.parametrize("state", ["down", "stopped", "invalid"])
def test_d10_non_alive_states_are_not_alive(tmp_path, state):
    """Д10, перебором остальных состояний гардиана: закрывает шаг РОВНО
    `alive`, а не «всё, что не ошибка»."""
    v = _s12_verdict(tmp_path, state=state, heartbeat_age=5.0)
    assert v is not Verdict.CLOSED, f"Д10: состояние `{state}` принято за живость"


@pytest.mark.parametrize("age", [3600.0, 86400.0, 49398.0])
def test_d10_stale_heartbeat_is_not_alive(tmp_path, age):
    """Д10. Протухший heartbeat не считается — даже когда гардиан всё ещё
    пишет `alive`.

    Файл отметки переживает смерть процесса и ребут: одна лишь его свежесть
    соврала бы «жив», а одно лишь слово `alive` в чужом файле — тем более.
    Возраст 49398 с взят не с потолка: ровно столько показывала проба
    `ops_watchdog` при ЖИВОМ раннере, когда читала не ту форму отметки.
    """
    v = _s12_verdict(tmp_path, state="alive", heartbeat_age=age)
    assert v is not Verdict.CLOSED, (
        f"Д10: heartbeat возрастом {age:.0f} с принят за свежий")


def test_d10_missing_heartbeat_is_not_alive(tmp_path):
    """Д10. Отметки нет вовсе — это не живость, а неизвестность."""
    v = _s12_verdict(tmp_path, state="alive", heartbeat_age=None)
    assert v is not Verdict.CLOSED, "Д10: отсутствие heartbeat принято за живость"


def test_d10_missing_observed_state_is_not_alive(tmp_path):
    """Д10. Нет `state/chatter_clients.json` — доказательства нет.
    «Скрипт отработал» и «факт появился» — разные утверждения (§2.2)."""
    v = _s12_verdict(tmp_path, state="alive", heartbeat_age=5.0, write_state=False)
    assert v is not Verdict.CLOSED, (
        "Д10: приёмка закрылась без наблюдаемого состояния")


def test_d10_alive_without_isolated_token_line_is_not_alive(tmp_path):
    """Д10/§2.2. Третье условие приёмки — строка `isolated token` в логе
    клиента: именно она доказала перешифровку у Ярины. Без неё раннер жив, но
    пульт ходит на ЧУЖОМ токене, и это то самое «команды через раз без ошибок
    в логе»."""
    v = _s12_verdict(tmp_path, state="alive", heartbeat_age=5.0, token_log=False)
    assert v is not Verdict.CLOSED, (
        "Д10: приёмка закрылась без строки `control-bot poller starting "
        "(isolated token)` в logs/chatter_<slug>.log")


@pytest.mark.parametrize(
    "state,age,token_log,name",
    [
        ("alive", 5.0, True, "живой"),
        ("starting", 5.0, True, "стартующий"),
        ("alive", 86400.0, True, "протухшая-отметка"),
        ("alive", None, True, "отметки-нет"),
        ("alive", 5.0, False, "без-строки-токена"),
    ],
)
def test_d10_s12_facts_name_state_heartbeat_and_token_line(tmp_path, state, age,
                                                           token_log, name):
    """§12.6.2: у S12 обязательны `state`, `heartbeat_age`, `isolated_token_line`.

    Ключи здесь важнее вердикта: без них «не закрыт» одинаково выглядит и у
    стартующего процесса, и у протухшей отметки, и у чужого токена — то есть
    человек читает одно и то же слово про три разные беды. Заодно это ловит
    пробу, которая ВЕРНУЛА правильный вердикт по неправильной причине.
    """
    r = _s12_result(tmp_path / name, state=state, heartbeat_age=age,
                    token_log=token_log)
    facts = dict(r.facts or {})
    missing = [k for k in ("state", "heartbeat_age", "isolated_token_line")
               if k not in facts]
    assert not missing, f"S12.facts без обязательных ключей {missing}: {facts}"

    assert str(facts["state"]) == state, (
        f"S12.facts['state'] = {facts['state']!r}, на диске {state!r}")
    # `None` = «не проверяли» — законно: проба вправе не читать лог, если уже
    # знает, что живости нет. А вот `False` при СУЩЕСТВУЮЩЕЙ строке — это
    # улика, утверждающая неправду, и человек по ней пойдёт перешифровывать
    # исправный токен.
    iso = facts["isolated_token_line"]
    assert iso is None or bool(iso) is token_log, (
        f"S12.facts['isolated_token_line'] = {iso!r} при строке в логе = "
        f"{token_log}. Если факт не проверялся — это None, а не False.")
    if age is None:
        assert facts["heartbeat_age"] is None, (
            "отметки нет — возраст обязан быть None, а не числом: ноль здесь "
            "читался бы как «только что»")
    else:
        got = facts["heartbeat_age"]
        assert got is not None and abs(float(got) - age) <= 120.0, (
            f"S12.facts['heartbeat_age'] = {got!r}, на диске ≈{age}")


def test_d10_probe_is_read_only(tmp_path):
    """§12.5: `probes.py` НИЧЕГО не пишет на диск.

    Проба, которая пишет, — это второе представление состояния, и оно молча
    погасит первое ([[jarvis-two-numbers-for-one-thing]]).
    """
    now = time.time()
    root = make_root(tmp_path, registry_entry="enabled", live_state="alive",
                     heartbeat_age=5.0, now=now)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, now=now)

    before = digest_tree(root)
    for step in STEPS:
        try:
            step.probe(ctx)
        except Exception:
            pass
    after = digest_tree(root)

    assert before == after, (
        "пробы изменили диск: "
        f"{sorted(set(before) ^ set(after)) or [k for k in before if before[k] != after.get(k)]}")


# ─────────────────────────────────────────────────────────────────────────────
# Д13 — active.yaml не трогаем НИ НА БАЙТ
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flags", FLAG_COMBOS, ids=FLAG_IDS)
def test_d13_active_yaml_is_untouched_through_the_whole_run(tmp_path, flags):
    """Д13. Прогон целиком, снимок `active.yaml` — после КАЖДОГО шага.

    Пошагово, а не только в конце: правка, сделанная на S10 и «прибранная» на
    S12, — тот же дефект, и в итоговом снимке её бы не было видно.
    """
    drill_yes, drill_again = flags
    root = make_root(tmp_path)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, drill_yes=drill_yes, drill_again=drill_again)

    outcome = guarded_run(ctx, runner, label=f"полный прогон, флаги {flags}")
    assert outcome.errors == [], outcome.errors


def test_d13_active_yaml_is_untouched_by_every_step_taken_alone(tmp_path):
    """Д13, перебором по ВСЕМ шагам STEPS: ни проба, ни действие каждого шага
    в отдельности не трогают `active.yaml`.

    Перебор, а не выборка: «ни на одном шаге» — это утверждение обо всех
    шестнадцати, и шаг, добавленный завтра, обязан попасть под сторожа сам.
    """
    for step in STEPS:
        root = make_root(tmp_path / f"d13-{step.id}", registry_entry="disabled",
                         live_state="alive", heartbeat_age=2.0)
        runner = FakeRunner(root)
        ctx = make_ctx(root, runner, drill_yes=True, drill_again=True)
        before = active_digest(root)

        try:
            step.probe(ctx)
        except Exception:
            pass
        assert_active_untouched(root, before, f"{step.id}.probe в одиночку")

        if step.act is not None:
            try:
                step.act(ctx)
            except Exception:
                pass
            assert_active_untouched(root, before, f"{step.id}.act в одиночку")


def test_d13_active_yaml_is_untouched_on_damaged_roots(tmp_path):
    """Д13 на битых корнях: обработка ошибки — обычное место, где код
    «чинит что-нибудь». `active.yaml` чинить нечего, он вне подключения."""
    damages = [
        {"config": "broken", "build_config": "broken"},
        {"config": "absent", "build_config": "absent"},
        {"session": False},
        {"consent": False},
        {"bundle": False},
        {"registry_entry": "enabled", "session": False},
    ]
    for i, damage in enumerate(damages):
        root = make_root(tmp_path / f"d13-dmg-{i}", **damage)
        runner = FakeRunner(root, start_rc=1)
        ctx = make_ctx(root, runner, drill_yes=True)
        before = active_digest(root)
        run_pipeline(ctx)
        assert_active_untouched(root, before, f"битый корень {damage}")


# ─────────────────────────────────────────────────────────────────────────────
# Д14 — funnel_gate не открывается НИКОГДА
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flags", FLAG_COMBOS, ids=FLAG_IDS)
def test_d14_funnel_gate_stays_false_through_the_whole_run(tmp_path, flags):
    """Д14. «Ни на одном шаге и ни при каком флаге» — перебором флагов, со
    снимком после каждого шага.

    Единственный сторож, чья цена измеряется в живых лидах: за открытым гейтом
    стоит catch-up по непрочитанному за сутки, и открывает его владелец
    командой `/funnel_gate on confirm`, а не подключение.
    """
    drill_yes, drill_again = flags
    root = make_root(tmp_path)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, drill_yes=drill_yes, drill_again=drill_again)

    outcome = guarded_run(ctx, runner, label=f"полный прогон, флаги {flags}")
    assert outcome.errors == [], outcome.errors
    assert_gate_closed(root, "конец прогона")
    assert_gate_not_commanded(runner, "конец прогона", root)


def test_d14_funnel_gate_stays_false_for_every_step_taken_alone(tmp_path):
    """Д14, перебором по ВСЕМ шагам STEPS, с обоими флагами сразу."""
    for step in STEPS:
        root = make_root(tmp_path / f"d14-{step.id}", registry_entry="disabled",
                         live_state="alive", heartbeat_age=2.0)
        runner = FakeRunner(root)
        ctx = make_ctx(root, runner, drill_yes=True, drill_again=True)

        try:
            step.probe(ctx)
        except Exception:
            pass
        assert_gate_closed(root, f"{step.id}.probe в одиночку")

        if step.act is not None:
            try:
                step.act(ctx)
            except Exception:
                pass
            assert_gate_closed(root, f"{step.id}.act в одиночку")
        assert_gate_not_commanded(runner, f"{step.id} в одиночку", root)


def test_d14_gate_is_not_opened_to_finish_the_connection(tmp_path):
    """Д14, самый правдоподобный соблазн: подключение «почти готово», и
    открыть гейт выглядит как последний шаг.

    S15 — вне команды НАВСЕГДА (§5.5). Здесь конвейеру дают дойти до конца при
    обоих флагах и требуют, чтобы гейт остался закрытым, а `S15` не оказался
    среди выполненных действий.
    """
    root = make_root(tmp_path)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, drill_yes=True, drill_again=True)

    outcome = guarded_run(ctx, runner, label="конвейер до конца")

    assert outcome.errors == [], outcome.errors
    assert "S15" not in outcome.acted, (
        "Д14: открытие трафика выполнено автоматом — это команда владельца")
    assert_gate_closed(root, "после полного прогона")
    assert_gate_not_commanded(runner, "после полного прогона", root)


def test_d14_gate_stays_false_even_when_the_client_config_already_says_true(tmp_path):
    """Д14, вторая сторона: гейт, УЖЕ открытый в конфиге, команда не «чинит»
    в свою сторону и не пользуется им как разрешением.

    Проверяется не значение (оно осталось прежним по воле человека), а то, что
    конвейер не превратил открытый гейт в основание что-либо сделать: это
    прямо запрещённый Д8 случай «доподключить живого».
    """
    root = make_root(tmp_path, funnel_gate=True, live_state="alive",
                     heartbeat_age=5.0, registry_entry="enabled", allowlist=[])
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner, drill_yes=True)

    outcome = run_pipeline(ctx)

    assert outcome.errors == [], outcome.errors
    assert outcome.acted == [], (
        f"Д14/Д8: при открытом гейте выполнены действия {outcome.acted}")
    assert not runner.drill_calls, (
        f"Д14: платный дрил при ОТКРЫТОМ гейте — реплики стенда уходят в "
        f"живую воронку: {runner.drill_calls}")


def test_d14_gate_stays_false_on_damaged_roots(tmp_path):
    """Д14 на битых корнях — по той же причине, что и Д13: ветка обработки
    ошибки проверяется реже всего, а гейт там открывать так же нельзя."""
    damages = [
        {"config": "broken", "build_config": "broken"},
        {"session": False},
        {"consent": False},
        {"bundle": False},
        {"registry_entry": "enabled", "config": "broken", "build_config": "broken"},
    ]
    for i, damage in enumerate(damages):
        root = make_root(tmp_path / f"d14-dmg-{i}", **damage)
        runner = FakeRunner(root, start_rc=1)
        ctx = make_ctx(root, runner, drill_yes=True, drill_again=True)
        run_pipeline(ctx)
        assert_gate_closed(root, f"битый корень {damage}")
        assert_gate_not_commanded(runner, f"битый корень {damage}", root)


# ─────────────────────────────────────────────────────────────────────────────
# §12.8 п.6 — ДВЕ AST-СВЕРКИ: запреты на действие, которых не видит ни один
# поведенческий тест
# ─────────────────────────────────────────────────────────────────────────────
#
# Обе проверяют ИСХОДНИК, а не поведение, и это не педантизм. Поведенческий
# сторож здесь бессилен по построению: модуль, стреляющий подпроцессом мимо
# `CommandRunner`, на подставном раннере остаётся ЗЕЛЁНЫМ — он просто не
# спрашивает разрешения. Сторож, который нельзя обойти, обязан смотреть на
# текст ([[jarvis-mutation-gate-target-drifts-from-code]]: статическая сверка
# стоит секунды).

CONNECT_PKG = Path(__file__).resolve().parents[1] / "chatter" / "connect"

# Прямой запуск процесса мимо шва §12.2 — любым из известных способов.
_SUBPROCESS_MODULES = {"subprocess", "multiprocessing", "pty"}
_OS_SPAWN_CALLS = {
    "system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp",
    "execv", "execve", "execvp", "execvpe", "startfile", "fork", "posix_spawn",
}


def _connect_source(name: str) -> tuple[Path, str]:
    p = CONNECT_PKG / name
    assert p.is_file(), f"§12.1: в chatter/connect/ нет {name} — {p}"
    return p, p.read_text(encoding="utf-8")


def _all_connect_sources() -> list[tuple[Path, str]]:
    files = sorted(CONNECT_PKG.rglob("*.py"))
    assert files, f"в {CONNECT_PKG} нет ни одного .py"
    return [(p, p.read_text(encoding="utf-8")) for p in files]


@pytest.mark.parametrize("module", ["probes.py", "actions.py"])
def test_ast_no_direct_subprocess_in_probes_and_actions(module):
    """§12.8 п.6, первая сверка: прямого `subprocess` в `probes.py` и
    `actions.py` нет.

    Цена обхода названа в §12.2 дословно: единственный путь наружу —
    `CommandRunner`, и это ровно то, что делает сторожей возможными без живых
    процессов. Модуль, вызвавший `subprocess.run` напрямую, на стенде поднимет
    НАСТОЯЩИЙ powershell — а сторож, ждавший вызова в подставном раннере,
    промолчит: он не увидит ни аргументов, ни `-Force`, ни отказа замера.
    Зелёный при этом остаётся зелёным, и в этом весь вред.
    """
    path, src = _connect_source(module)
    tree = ast.parse(src, filename=str(path))
    bad: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in _SUBPROCESS_MODULES:
                    bad.append(f"строка {node.lineno}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _SUBPROCESS_MODULES:
                bad.append(f"строка {node.lineno}: from {node.module} import ...")
            if root == "os":
                for a in node.names:
                    if a.name in _OS_SPAWN_CALLS:
                        bad.append(f"строка {node.lineno}: from os import {a.name}")
        elif isinstance(node, ast.Attribute):
            owner = getattr(node.value, "id", None)
            if owner in _SUBPROCESS_MODULES:
                bad.append(f"строка {node.lineno}: {owner}.{node.attr}")
            if owner == "os" and node.attr in _OS_SPAWN_CALLS:
                bad.append(f"строка {node.lineno}: os.{node.attr}")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name in ("__import__", "import_module"):
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        if a.value.split(".")[0] in _SUBPROCESS_MODULES:
                            bad.append(
                                f"строка {node.lineno}: динамический импорт {a.value!r}")

    assert bad == [], (
        f"§12.2/§12.8 п.6: в {module} есть прямой запуск процесса мимо "
        f"CommandRunner: {bad}. Шов обойдён — сторожа перестают что-либо "
        f"доказывать, оставаясь зелёными.")


def test_ast_connect_does_not_import_onboard_beyond_frozen_constants():
    """§12.8 п.6, вторая сверка: `chatter.connect` не импортирует
    `chatter.onboard` — кроме узкого исключения §12.9 п.7.

    Решение владельца q5: `onboard` собирает файлы, `connect` поднимает живого
    клиента, у них разные права и разный радиус ошибки; слитый модуль однажды
    подключит что-нибудь при сборке. Запрет про ПРАВА, а не про две
    замороженные константы, поэтому разрешено ровно
    `from chatter.onboard.checks import <КОНСТАНТА>` и запрещено всё, что
    может ЗАПУСТИТЬ пайплайн (сборку, рендер, пересборку конфига).

    Правило исполнимо механически: имя, состоящее из ЗАГЛАВНЫХ и подчёркиваний,
    — константа; всё прочее — код, который умеет что-то делать.
    """
    allowed_module = "chatter.onboard.checks"
    bad: list[str] = []

    for path, src in _all_connect_sources():
        rel = path.relative_to(CONNECT_PKG.parent.parent).as_posix()
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "chatter.onboard" or a.name.startswith("chatter.onboard."):
                        bad.append(f"{rel}:{node.lineno}: import {a.name} "
                                   f"(импорт МОДУЛЯ даёт доступ ко всему, что он умеет)")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level:  # относительный импорт внутри chatter.connect
                    continue
                if mod != "chatter.onboard" and not mod.startswith("chatter.onboard."):
                    continue
                if mod != allowed_module:
                    bad.append(f"{rel}:{node.lineno}: from {mod} import ... "
                               f"(разрешён только {allowed_module})")
                    continue
                for a in node.names:
                    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", a.name):
                        bad.append(
                            f"{rel}:{node.lineno}: from {mod} import {a.name} — "
                            f"это не замороженная константа")
            elif isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name in ("__import__", "import_module"):
                    for a in node.args:
                        if (isinstance(a, ast.Constant)
                                and isinstance(a.value, str)
                                and a.value.startswith("chatter.onboard")):
                            bad.append(f"{rel}:{node.lineno}: динамический импорт "
                                       f"{a.value!r} в обход сверки")

    assert bad == [], (
        "§12.8 п.6 / q5: chatter.connect тянет chatter.onboard шире, чем "
        f"замороженные константы: {bad}")


def test_ast_checks_look_at_files_that_exist():
    """Проверка проверки: обе сверки выше зелены и тогда, когда смотреть не на
    что. Пустой (или переименованный) `chatter/connect/` сделал бы их вечно
    зелёными — тот самый «сторож, зелёный по построению».
    """
    for name in ("model.py", "probes.py", "actions.py", "steps.py", "__main__.py"):
        p, src = _connect_source(name)
        assert src.strip(), f"{name} пуст — сверкам не на что смотреть"
        ast.parse(src, filename=str(p))


# ─────────────────────────────────────────────────────────────────────────────
# Общее: секретов в уликах не бывает (§12.5) — попутный сторож к Д2/Д10
# ─────────────────────────────────────────────────────────────────────────────

def test_facts_never_carry_the_token_value(tmp_path):
    """§12.5: доказываем ПРИЗНАКОМ, а не значением.

    Проверяется на всех шагах сразу: значение токена из `Ctx.env` не имеет
    права оказаться ни в `facts`, ни в `why`, ни в `todo`.
    """
    root = make_root(tmp_path, registry_entry="enabled", live_state="alive",
                     heartbeat_age=5.0)
    runner = FakeRunner(root)
    ctx = make_ctx(root, runner)
    secret = ctx.env[TOKEN_ENV]

    for step in STEPS:
        try:
            r = step.probe(ctx)
        except Exception:
            continue
        blob = json.dumps(getattr(r, "facts", {}), ensure_ascii=False, default=str)
        blob += f"\n{getattr(r, 'why', '')}\n{getattr(r, 'todo', '')}"
        assert secret not in blob, (
            f"{step.id}: значение секрета попало в улики шага")
