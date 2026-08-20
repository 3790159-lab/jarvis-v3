"""Сторожа Д1, Д7, Д9, Д12 из §7 спеки `2026-08-20-t7-turnkey-connect.md`.

Писано ОТ СПЕКИ автором, планового кода не видевшим
([[jarvis-guards-not-by-the-plan-author]]): тест, написанный автором кода,
наследует его же неверное допущение и соглашается с реализацией по
определению.

Что здесь держится:

* **Д1** — обрыв: процесс убит после КАЖДОГО шага (структурно, по всем шагам
  `STEPS`, а не на трёх примерах), повторный запуск доводит подключение до
  того же конца и не повторяет ни одного уже сделанного действия;
* **Д7** — состояние ТОЛЬКО из фактов: удаление журнала
  `state/connect/<slug>.md` не меняет ни одного вердикта; подделка журнала —
  тоже; пробы вообще ничего не пишут на диск (§12.5, §12.6 п.4);
* **Д9** — запись реестра атомарна: подменяется только ЦЕЛЫЙ файл, сбой в
  момент подмены не оставляет реестра, которого не читает `parse_registry`;
* **Д12** — идемпотентность: два запуска подряд на завершённом подключении не
  меняют на диске ничего.

Как проверяется (обязательные условия задачи):

* всё живёт в `tmp_path`: свой корень, свой реестр, свои `.secrets`. Живого
  дерева, живых процессов, Telegram, сети и денег здесь нет вовсе;
* подпроцессы идут ТОЛЬКО через `Ctx.run` (контракт §12.2), и сюда
  подставляется `FakeRunner`, который ничего не запускает, а записывает, что
  его звали, и имитирует побочные эффекты настоящих скриптов ровно в том
  объёме, в каком их читают пробы §2.2. Список его вызовов — и есть
  доказательство «действие не повторилось». Беду он изображает ИСКЛЮЧЕНИЕМ,
  а не выдуманным `rc` (§12.6 п.3);
* порядок исполнения берётся из §12.3 ДОСЛОВНО и живёт в `_drive` ниже.
  Это осознанная копия нормативного текста: точка входа `__main__`, в которую
  можно передать готовый `Ctx`, контрактом §12 не задана, а без такой точки
  в процесс нельзя внести обрыв. Из-за этого собственный цикл CLI, его коды
  выхода и запись журнала (§12.6 п.4 — журнал пишет только `__main__.py`)
  сторожами ЭТОГО файла не покрыты. Сказано вслух, чтобы не считалось
  покрытым.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from chatter.config.loader import load_config
from chatter.core.client_registry import parse_registry
from chatter.registry_cli import build_plan, session_available

from chatter.connect import model as connect_model
from chatter.connect import steps as connect_steps

Verdict = connect_model.Verdict
Owner = connect_model.Owner
Ctx = connect_model.Ctx
STEPS = connect_steps.STEPS

try:                                             # форма результата — из модуля,
    CommandResult = connect_model.CommandResult  # чтобы не заводить второе имя
except AttributeError:                           # на одну вещь
    @dataclasses.dataclass(frozen=True)          # ([[jarvis-two-numbers-for-one-thing]])
    class CommandResult:                         # noqa: D101
        rc: int
        stdout: str = ""
        stderr: str = ""


# Слаг песочницы — НЕ выдуманный. S9 закрывается только тогда, когда у
# клиента есть дрил-контакт в каноне `payments/drill_gate.DRILL_CONTACTS`
# (Д6), а канон закрыт: суффикс контакта — это ПЕРСОНА. С выдуманным слагом
# S9 навсегда «ждёт человека», и всё, что за ним, стало бы непроверяемым.
# Живого ничего не задето: корень целиком в tmp_path, реестр свой, сессия
# своя, наружу ходит только подставной раннер.
SLUG = "yarina"
TOKEN_ENV = "CHATTER_CONTROL_BOT_TOKEN_YARINA"
DRILL_CONTACT_ID = 8849893367
OWNER_CHAT_ID = 237616472
ISOLATED_TOKEN_LINE = "control-bot poller starting (isolated token)"
# Вердикт прогона — дословно из `chatter/core/drill.run_verdict`. Строка
# берётся у источника, а не переписывается: сочинённый фейком вердикт
# проверял бы фантазию фейка.
GREEN_VERDICT = "✅ ВЕРДИКТ: ПРОГОН ЗЕЛЁНЫЙ"

# Шесть автоматических шагов — инвариант §3: AUTO <=> у шага есть `act`.
AUTO_IDS = ("S4", "S9", "S10", "S11", "S12", "S13")
ALL_IDS = tuple(f"S{i}" for i in range(16))
# §12.8 п.1: хвостовые человеческие S14/S15 в цикл НЕ входят — иначе код 0
# недостижим. Цикл идёт по S0..S13.
CYCLE_IDS = tuple(f"S{i}" for i in range(14))
TAIL_HUMAN_IDS = ("S14", "S15")


def cycle_steps():
    """Шаги, по которым идёт цикл §12.3 — без хвостовой человеческой пары."""
    return tuple(st for st in STEPS if st.id not in TAIL_HUMAN_IDS)


# ─────────────────────────────────────────────────────────────────────────────
# Окружение теста: ключей в os.environ быть не должно
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_env_leak(monkeypatch):
    """Среда S0/S7 обязана приходить через `Ctx.env` (§12.2), а не из процесса.

    Корневой conftest подтягивает боевой `.env`; без этой зачистки реализация,
    читающая `os.environ`, зеленела бы на ЧУЖОМ ключе — то есть сторож молчал
    бы ровно там, где должен кричать."""
    for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "ANTHROPIC_API_KEY",
                 TOKEN_ENV, "CHATTER_CONTROL_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# Фейковый CommandRunner — единственная дверь наружу (§12.2)
# ─────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Call:
    argv: tuple[str, ...]
    cwd: str
    timeout: float

    @property
    def line(self) -> str:
        return " ".join(self.argv).lower()


def _is_paid_drill(call: Call) -> bool:
    return "drill" in call.line


def _is_start(call: Call) -> bool:
    return "chatter_client" in call.line and "start" in call.line


def _is_mutating(call: Call) -> bool:
    """Звонок, ПОСЛЕ которого мир другой. Чтение (`status`, `-Check`,
    `registry_cli`) сюда не попадает: повторный запуск имеет право смотреть."""
    line = call.line
    if _is_paid_drill(call):
        return True
    if "chatter_client" in line and any(
            w in line for w in ("start", "stop", "restart")):
        return True
    if "reencrypt" in line and "-check" not in line:
        return True
    if "add_secret" in line:
        return True
    return False


class RunnerTrouble(RuntimeError):
    """«Выполнить не удалось»: таймаут, ненайденный исполняемый, упавший хост.

    Именно исключение, а не `CommandResult` с выдуманным `rc` — §12.6 п.3:
    «замер не состоялся» и «замер сказал нет» обязаны различаться."""


class FakeRunner:
    """Ничего не запускает. Записывает вызовы и имитирует ровно те побочные
    эффекты настоящих скриптов, которые читают пробы §2.2.

    `chatter_client.ps1 -Action start` здесь ставит `enabled: true` в реестре
    потому, что это делает НАСТОЯЩИЙ скрипт (`scripts/chatter_client.ps1`,
    строки 72 и 132), а не потому, что так удобнее тесту.

    `trouble_on` — подстрока; на первом совпавшем вызове раннер БРОСАЕТ и
    ничего не делает: так изображается беда по §12.6 п.3."""

    def __init__(self, root: Path, slug: str, now: float, *,
                 trouble_on: str | None = None):
        self.root = Path(root)
        self.slug = slug
        self.now = now
        self.trouble_on = trouble_on
        # Перечень закрыт в §12.7 п.6 значениями scripts/reencrypt_env.py.
        self.env_enc_state = "in_sync"
        self.calls: list[Call] = []
        self.troubled: list[Call] = []
        self.drills_written: list[Path] = []
        self.called_as_method = 0
        self.called_as_callable = 0

    # --- протокол -----------------------------------------------------------
    def __call__(self, argv, *, cwd=None, timeout=None) -> CommandResult:
        """`ctx.runner(...)` вместо `ctx.runner.run(...)`.

        §12.8 п.4 закрыл разночтение: форма одна — `ctx.runner.run(argv,
        cwd=..., timeout=...)`. Раннер всё же принимает и зов-как-функцию, но
        не прощая: он его СЧИТАЕТ, и отдельный тест ниже краснеет. Так
        расхождение формы называется прямо, а не растекается краснотой по
        сторожам содержания."""
        self.called_as_callable += 1
        return self._exec(argv, cwd=cwd, timeout=timeout)

    def run(self, argv, *, cwd=None, timeout=None) -> CommandResult:
        self.called_as_method += 1
        return self._exec(argv, cwd=cwd, timeout=timeout)

    def _exec(self, argv, *, cwd=None, timeout=None) -> CommandResult:
        argv = [str(a) for a in argv]
        call = Call(tuple(argv), str(cwd), float(timeout or 0.0))
        self.calls.append(call)
        line = call.line
        if self.trouble_on and self.trouble_on in line and not self.troubled:
            self.troubled.append(call)
            raise RunnerTrouble(f"выполнить не удалось: {' '.join(argv)}")
        if "registry_cli" in line:
            return self._plan()
        if "chatter_client" in line:
            if "start" in line:
                return self._start()
            return self._status()
        if "drill" in line:
            return self._drill(argv)
        if "reencrypt" in line:
            # Форма строки и код возврата взяты у НАСТОЯЩЕГО скрипта
            # (scripts/reencrypt_env.py: `[reencrypt] статус до: <status>`,
            # `-Check` даёт rc 0 только на `in_sync`). Свою формулировку
            # выдумывать нельзя: фейк, печатающий не то, что печатает жизнь,
            # проверяет собственную фантазию.
            return CommandResult(
                0 if self.env_enc_state == "in_sync" else 1,
                f"[reencrypt] статус до: {self.env_enc_state}", "")
        if "onboard" in line or "--check" in line:
            return CommandResult(
                0, "приёмка: 17 проверок, красных 0, флагов 0\nВЕРДИКТ: 0", "")
        return CommandResult(0, "", "")

    # --- имитации -----------------------------------------------------------
    def _registry_path(self) -> Path:
        return self.root / "chatter" / "clients" / "registry.yaml"

    def _plan(self) -> CommandResult:
        reg = self._registry_path()
        if not reg.exists():
            return CommandResult(0, json.dumps(
                {"fatal": f"registry not found: {reg}", "clients": []}), "")
        plan = build_plan(
            reg.read_text(encoding="utf-8"), root=str(self.root),
            session_available=lambda s: session_available(s, root=str(self.root)),
            client_dir_exists=lambda slug: (
                self.root / "chatter" / "clients" / slug).is_dir(),
        )
        return CommandResult(0, json.dumps(plan, ensure_ascii=False), "")

    def _state_path(self) -> Path:
        return self.root / "state" / "chatter_clients.json"

    def _start(self) -> CommandResult:
        reg = self._registry_path()
        data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
        clients = data.setdefault("clients", {}) or {}
        entry = clients.setdefault(self.slug, {})
        entry["enabled"] = True
        data["clients"] = clients
        reg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                       encoding="utf-8")

        self._state_path().parent.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(json.dumps({
            "updated_ts": self.now, "fatal": None,
            "clients": {self.slug: {
                "desired": "enabled", "state": "alive", "pid": 4242,
                "heartbeat_ts": self.now, "last_transition_ts": self.now,
                "consecutive_fail": 0, "last_error": None}}}), encoding="utf-8")

        # Раннер штампует СВОЙ файл живости: `state/chatter_heartbeat_<slug>.txt`
        # с целыми unix-секундами в ascii (`chatter/runtime_paths.py` +
        # `telethon_run.write_heartbeat`). Поле `heartbeat_ts` в
        # `chatter_clients.json` — это наблюдение супервизора, и подменять им
        # штамп раннера нельзя: получилось бы два числа на одну вещь
        # ([[jarvis-two-numbers-for-one-thing]]).
        beat = self.root / "state" / f"chatter_heartbeat_{self.slug}.txt"
        beat.write_text(str(int(self.now)), encoding="ascii")

        log = self.root / "logs" / f"chatter_{self.slug}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{self.slug}] {ISOLATED_TOKEN_LINE}\n")
        return CommandResult(
            0, f"[chatter_client] {self.slug} -> enabled: true\n"
               f"catch-up радиус: 0 сообщений", "")

    def _scenario_name(self) -> str:
        """`name:` из заготовки дрила — из живого конфига, иначе из сборки."""
        for base in (self.root / "chatter" / "clients" / self.slug,
                     self.root / "build" / "onboard" / self.slug):
            path = base / "drill.yaml"
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("name:"):
                        return line.split(":", 1)[1].strip().strip('"')
        return f"онбординг {self.slug}"

    def _status(self) -> CommandResult:
        path = self._state_path()
        return CommandResult(0, path.read_text(encoding="utf-8") if path.exists()
                             else "{}", "")

    def _drill(self, argv: list[str]) -> CommandResult:
        """Отчёт прогона в форме НАСТОЯЩЕГО харнесса.

        Форма не выдумана: `scripts/drill_runner.py` пишет
        `<out>/<unix_ts>.md`, первой строкой `# Дрил: <имя сценария>`, второй —
        `**Клиент:** \u0060<slug>\u0060 · **дрил-контакт:** \u0060<contact>\u0060`.
        Имя сценария берётся из `drill.yaml` живого конфига, а не собирается
        по памяти: разъехавшись с ним, фейк проверял бы сам себя.

        `--out state/drills/<slug>` (§12.7 п.2): каталог отделяет чужой
        прогон, тело доказывает, что дрил про ЭТОГО клиента.
        """
        out = self.root / "state" / "drills"
        if "--out" in argv:
            out = self.root / argv[argv.index("--out") + 1]
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{int(self.now) + len(self.drills_written)}.md"
        say = "доброго дня, цікавить чистка"
        path.write_text(
            f"# Дрил: {self._scenario_name()}\n"
            f"**Клиент:** `{self.slug}` · "
            f"**дрил-контакт:** `{DRILL_CONTACT_ID}:{self.slug}`\n"
            f"\n"
            f"{GREEN_VERDICT} — 1 из 1 шаг(ов), все проверки зелёные\n"
            f"\n"
            f"## План (все реплики)\n"
            f"1. «{say}»\n"
            f"\n"
            f"## ✅ Шаг 1: «{say}»\n"
            f"- ✅ `card_delivered` — карточка ушла владельцу\n"
            f"\n"
            f"**Деньги:** смета $0.2951 · факт по ходам дрила $0.2287 · "
            f"отклонение ×0.77\n",
            encoding="utf-8")
        self.drills_written.append(path)
        return CommandResult(0, "дрил: 5/5 зелёных, $0.2287", "")


# ─────────────────────────────────────────────────────────────────────────────
# Корень-песочница
# ─────────────────────────────────────────────────────────────────────────────

_SETTINGS = {
    "model": "claude-sonnet-5",
    "language": "ru",
    "owner_id": "Дмитрий",
    "persona_name": "Аня",
    "currency": "грн",
    "work_hours": {"start": 9, "end": 22},
    "limits": {"max_reply_tokens": 20000, "per_contact_hourly": 20,
               "daily_cap": 500},
    "control": {"control_bot_token_env": TOKEN_ENV,
                "owner_chat_id": OWNER_CHAT_ID},
    # Блок `telegram` присутствует и ПУСТ — ровно так его печатает T1-T6
    # (`chatter/onboard/render.py`: `allowlist: []`, `funnel_gate: false`).
    # Пустой список, а не отсутствующий ключ: сборка даёт именно эту форму, и
    # фикстура, кормящая S9 чем-то другим, проверяла бы вход, которого не
    # бывает. Шаг при этом всё равно НЕ закрыт — владельца и дрил-контакта в
    # списке нет, — поэтому `act_s9` исполняется и остаётся под сторожем.
    "telegram": {"allowlist": [], "funnel_gate": False},
}


def _write_client_files(target: Path) -> None:
    (target / "persona.md").write_text(
        "# Аня\n\nживой администратор студии, пишет коротко.\n", encoding="utf-8")
    (target / "knowledge.md").write_text(
        "## Услуги\n- чистка — 500 грн\n- полировка — 900 грн\n\n"
        "## Сроки\n- запись на завтра\n", encoding="utf-8")
    (target / "playbook.md").write_text(
        "## Правила\n- отвечать коротко\n- цену называть из базы\n",
        encoding="utf-8")
    (target / "examples.yaml").write_text(
        yaml.safe_dump([{"client": "скільки коштує?", "olga": "500 грн"}],
                       allow_unicode=True, sort_keys=False), encoding="utf-8")
    (target / "settings.yaml").write_text(
        yaml.safe_dump(_SETTINGS, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    # Заготовка дрила, которую печатает T5. Имя не свободное: приёмка ищет
    # сценарий РОВНО по четырём именам (`chatter/onboard/checks.py`), а пишет
    # его T5 под `drill.yaml` (`drill_scenario.SCENARIO_FILE_NAME`). Форма —
    # как у `drill_scenario.render_yaml`. Контакт — дрил-контакт из канона:
    # именно он отличает прогон про НАШЕГО клиента от чужого (§12.7 п.2).
    (target / "drill.yaml").write_text(
        f"# заготовка дрила песочницы\n"
        f"name: онбординг {SLUG}\n"
        f"client: {SLUG}\n"
        f"contact: \"{DRILL_CONTACT_ID}:{SLUG}\"\n"
        f"steps:\n"
        f"\n"
        f"  - say: доброго дня, цікавить чистка\n"
        f"    expect:\n"
        f"      card_delivered: true\n",
        encoding="utf-8")


def build_root(base: Path, *, slug: str = SLUG) -> Path:
    """Корень, в котором ЧЕЛОВЕЧЕСКИЕ шаги §3 уже закрыты, а автоматические —
    ещё нет. Всё, что здесь лежит, названо колонкой «факт на диске» §3.

    Автоматических действий фикстура не делает: перенос конфига (S4),
    allowlist (S9), запись в реестр (S10), подъём (S11), приёмка (S12) и дрил
    (S13) остаются работой кода — иначе Д1 проверял бы пустоту."""
    root = base / "root"
    (root / "state" / "connect").mkdir(parents=True, exist_ok=True)
    (root / "state" / "drills").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / ".secrets").mkdir(parents=True, exist_ok=True)
    clients = root / "chatter" / "clients"
    clients.mkdir(parents=True, exist_ok=True)

    # Скрипты, которые команда зовёт наружу (§12.2). Заглушки: исполнять их
    # некому — вместо запуска стоит `FakeRunner`, — но СУЩЕСТВОВАТЬ они
    # обязаны: проверить наличие исполняемого до зова это правильно, и
    # песочница без них проверяла бы отказ по несуществующей причине.
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("chatter_client.ps1", "reencrypt_env.ps1", "add_secret.ps1"):
        (scripts / name).write_text(
            "# заглушка песочницы: запускается только подставным раннером\n",
            encoding="utf-8")
    for name in ("drill_runner.py", "reencrypt_env.py"):
        (scripts / name).write_text(
            "# заглушка песочницы: запускается только подставным раннером\n",
            encoding="utf-8")

    # S0: материал среды. Секретов в файлах нет — только следы.
    (root / ".env").write_text("# plaintext .env, живого секрета тут нет\n",
                               encoding="utf-8")
    (root / ".env.enc").write_bytes(b"\x00fake-dpapi-blob")

    # S1..S3: сборка T1-T6, вычитана, флаги названы (их нет).
    build = root / "build" / "onboard" / slug
    build.mkdir(parents=True, exist_ok=True)
    _write_client_files(build)
    (build / "REPORT.md").write_text(
        "# Звіт\n\nрозділ 1\nрозділ 2\nрозділ 3\n", encoding="utf-8")
    (build / "report.json").write_text(json.dumps({
        "schema_version": 1,
        "meta": {"slug": slug, "source": "brief.xlsx", "brief_schema_version": 1,
                 "files": ["examples.yaml", "knowledge.md", "persona.md",
                           "playbook.md", "settings.yaml"],
                 "notes": [], "flags_checked": True},
        "sections": {"taken": [], "defaulted": [], "missing": []},
        "flags": [],
        "counters": {"services": 2, "prices": 4, "deadlines": 4},
        "role_wording_question": None, "sla_reality_question": None,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (build / "REVIEWED").write_text(
        "вычитано владельцем 20.08; красных нет, флагов в report.json нет "
        "(список пуст) — называть нечего\n", encoding="utf-8")

    # S5: согласие живёт РЯДОМ С МАРКЕРОМ БАНДЛА, а не в каталоге клиента
    # (§12.8 п.3): человек кладёт его ДО логина, то есть до того, как каталог
    # клиента вообще появится. Первая строка — ISO-дата (§12.7 п.4).
    # Каталог клиента фикстура НЕ создаёт: его целиком делает `act_s4`.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (root / "state" / "connect" / f"{slug}.consent.md").write_text(
        f"{yesterday} — согласие на доступ к личной переписке аккаунта "
        f"получено от владельца бизнеса (голосовое в телеграме).\n",
        encoding="utf-8")

    # S6: сессия открывается (правило `.enc` из secret_loader — см.
    # registry_cli.session_available; plaintext тоже годится).
    (root / ".secrets" / f"{slug}.session").write_bytes(b"fake-telethon-session")

    # S8: маркер бандла со строкой экспорта, в ней назван слаг.
    (root / "state" / "connect" / f"{slug}.bundle.txt").write_text(
        f"сессии в бандле: demo.session.enc, {slug}.session.enc\n",
        encoding="utf-8")

    # Реестр: наш слаг ещё НЕ вписан (это работа S10), чужой выключен.
    (clients / "registry.yaml").write_text(
        "clients:\n"
        "  neighbour:\n"
        "    enabled: false\n"
        "    personas: [neighbour]\n"
        "    session: .secrets/neighbour.session\n"
        "    db: .secrets/neighbour.db\n", encoding="utf-8")

    # Ложный канал §0-бис: лежит, чтобы любое его изменение было видно.
    (clients / "active.yaml").write_text(
        "# ГАРДИАНОМ ЭТОТ ФАЙЛ БОЛЬШЕ НЕ ИСПОЛЬЗУЕТСЯ\nclients: [demo]\n",
        encoding="utf-8")
    return root


def strip_human_facts(root: Path, *, slug: str = SLUG) -> None:
    """Тот же корень, но человеческие шаги НЕ закрыты — состояние «ещё ничего
    не начиналось». Нужен Д7: подделка журнала проверяется именно здесь."""
    for path in ((root / "state" / "connect" / f"{slug}.consent.md"),
                 (root / ".secrets" / f"{slug}.session"),
                 (root / "state" / "connect" / f"{slug}.bundle.txt"),
                 (root / "build" / "onboard" / slug / "REVIEWED")):
        if path.exists():
            path.unlink()


def _env() -> dict:
    return {
        "TELEGRAM_API_ID": "1234567",
        "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
        "ANTHROPIC_API_KEY": "sk-ant-" + "x" * 40,
        TOKEN_ENV: "8000000000:AA" + "y" * 33,
    }


def make_ctx(root: Path, runner: FakeRunner, *, slug: str = SLUG,
             drill_yes: bool = True, drill_again: bool = False) -> Ctx:
    return Ctx(
        root=Path(root),
        slug=slug,
        now=runner.now,          # unix-секунды, §12.6 п.6
        env=dict(_env()),
        runner=runner,           # §12.8 п.4: поле `runner`, метод `.run(...)`
        drill_yes=drill_yes,
        drill_again=drill_again,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Порядок исполнения §12.3 — дословно, с точкой обрыва
# ─────────────────────────────────────────────────────────────────────────────

class Killed(Exception):
    """Процесс умер посреди подключения. Ничего не доделывает и не убирает —
    ровно как обрыв сессии, ради которого арка и затевалась."""

    def __init__(self, visited, acted):
        super().__init__("процесс убит")
        self.visited = tuple(visited)
        self.acted = tuple(acted)


@dataclasses.dataclass(frozen=True)
class Outcome:
    code: int
    stop_id: str | None
    visited: tuple
    acted: tuple


def _drive(ctx, *, kill_after=None) -> Outcome:
    """§12.3 без единой отсебятины. `kill_after` — пара `(id, фаза)`, где фаза
    `act` = смерть сразу после действия (факт есть, подтверждения нет), а
    `step` = смерть после того, как шаг полностью пройден."""
    visited: list[str] = []
    acted: list[str] = []
    for step in cycle_steps():
        visited.append(step.id)
        r = step.probe(ctx)
        if r.verdict == Verdict.CONFLICT:
            return Outcome(1, step.id, tuple(visited), tuple(acted))
        if r.verdict != Verdict.CLOSED and step.owner == Owner.HUMAN:
            return Outcome(3, step.id, tuple(visited), tuple(acted))
        if r.verdict != Verdict.CLOSED and step.owner == Owner.AUTO:
            done = step.act(ctx)
            acted.append(step.id)
            if kill_after == (step.id, "act"):
                raise Killed(visited, acted)
            r2 = step.probe(ctx)
            if r2.verdict != Verdict.CLOSED:
                # §12.7 п.1: «жду разрешения человека» — это код 3, а не
                # «сломано». Старшинство названо там же: булево значимо
                # ТОЛЬКО при вердикте, отличном от CLOSED.
                waits = (getattr(r2, "waits_for_human", False)
                         or getattr(done, "waits_for_human", False))
                return Outcome(3 if waits else 1, step.id,
                               tuple(visited), tuple(acted))
        if kill_after == (step.id, "step"):
            raise Killed(visited, acted)
    return Outcome(0, None, tuple(visited), tuple(acted))


def run_once(root: Path, *, kill_after=None, drill_yes: bool = True,
             now: float | None = None, slug: str = SLUG,
             trouble_on: str | None = None):
    """Один ЗАПУСК ПРОЦЕССА: свой раннер, своя память, ничего не переносится
    из прошлого запуска, кроме того, что лежит на диске."""
    runner = FakeRunner(root, slug, time.time() if now is None else now,
                        trouble_on=trouble_on)
    ctx = make_ctx(root, runner, slug=slug, drill_yes=drill_yes)
    try:
        outcome = _drive(ctx, kill_after=kill_after)
    except Killed as killed:
        return None, runner, killed
    return outcome, runner, None


# ─────────────────────────────────────────────────────────────────────────────
# Снимки диска и вердиктов
# ─────────────────────────────────────────────────────────────────────────────

def run_expecting_trouble(root: Path, *, now: float | None = None):
    """Прогон, в котором мы СОРВАЛИ запись на диск.

    Как срыв выйдет наружу — исключением (процесс умер) или вердиктом
    `ПРОТИВОРЕЧИЕ` с внятным `why` (§12.6 п.3) — этим сторожам безразлично:
    они про то, ЧТО ОСТАЛОСЬ НА ДИСКЕ. Требовать здесь трассировку значило бы
    красить правильное поведение: превратить невозможность выполнить в
    названную причину — ровно то, чего требует контракт."""
    try:
        return run_once(root, now=now)
    except BaseException:                                       # noqa: BLE001
        return None, None, None


def journal_path(root: Path, slug: str = SLUG) -> Path:
    return Path(root) / "state" / "connect" / f"{slug}.md"


def fingerprint(root: Path, *, slug: str = SLUG, with_journal: bool = False,
                with_mtime: bool = False) -> dict:
    """Что лежит на диске. Имена дрил-файлов несут ВРЕМЯ ПРОГОНА, поэтому от
    них берётся количество, а не имя: сравнивать два разных прогона по `<ts>`
    значило бы краснеть на календаре."""
    root = Path(root)
    out: dict[str, str] = {}
    drills = 0
    journal = journal_path(root, slug)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path == journal and not with_journal:
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("state/drills/"):
            drills += 1
            continue
        value = hashlib.sha256(_root_neutral(path, root)).hexdigest()
        if with_mtime:
            value += f":{path.stat().st_mtime_ns}"
        out[rel] = value
    out["state/drills/ (файлов)"] = str(drills)
    return out


def _root_neutral(path: Path, root: Path) -> bytes:
    """Содержимое без абсолютного пути корня.

    Два прогона сравниваются в РАЗНЫХ песочницах, и записанный в файл
    абсолютный путь развёл бы их байты, ничего не сказав о предмете."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    for form in (str(root), str(root).replace("\\", "/"),
                 str(root).replace("\\", "\\\\")):
        text = text.replace(form, "<ROOT>")
    return text.encode("utf-8")


def _facts_text(result) -> str:
    return json.dumps(getattr(result, "facts", None), ensure_ascii=False,
                      sort_keys=True, default=str)


def verdicts(root: Path, *, slug: str = SLUG, now: float) -> tuple:
    """Вердикт и улики КАЖДОГО шага §3 — то, что Д7 обязан удержать
    неизменным. `now` передаётся снаружи: `heartbeat_age` (§12.6 п.2) считается
    от него, и два снимка с разным `now` разошлись бы по календарю, а не по
    предмету."""
    runner = FakeRunner(root, slug, now)
    ctx = make_ctx(root, runner, slug=slug)
    out = []
    for step in cycle_steps():
        r = step.probe(ctx)
        out.append((step.id, r.verdict, _facts_text(r)))
    return tuple(out)


HONEST_JOURNAL = (
    "# журнал подключения acme\n\n"
    "- S0 закрыт\n- S1 закрыт\n- S2 закрыт\n- S3 закрыт\n"
    "- S4 конфиг перенесён\n"
)

FORGED_JOURNAL = "\n".join(
    [f"- {sid}: ЗАКРЫТ (подделка: этой строке верить нельзя)" for sid in ALL_IDS]
    + ["", "ПОДКЛЮЧЕНИЕ ЗАВЕРШЕНО: всё готово, дрил прогнан, трафик открыт", ""]
)


def _write_journal(root: Path, text: str, *, slug: str = SLUG) -> None:
    path = journal_path(root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# Предусловия сторожей: без них Д1 молчал бы на урезанном списке шагов
# ═════════════════════════════════════════════════════════════════════════════

def test_steps_are_the_cycle_in_spec_order():
    """`STEPS` — литеральный кортеж в порядке §3, и цикл идёт по S0..S13.

    Хвостовая пара S14/S15 в цикл не входит (§12.8 п.1), но если она в
    кортеже присутствует — то только последней и только в этом порядке.
    Стоит здесь, а не «где-то ещё»: Д1 параметризуется ПО `STEPS`, и урезанный
    кортеж превратил бы сторожа на все шаги в сторожа на выборку — молча
    ([[jarvis-literal-lists-not-introspection]])."""
    ids = tuple(st.id for st in STEPS)
    assert ids[:len(CYCLE_IDS)] == CYCLE_IDS
    assert ids[len(CYCLE_IDS):] in ((), TAIL_HUMAN_IDS)
    assert tuple(st.id for st in cycle_steps()) == CYCLE_IDS


def test_auto_is_exactly_the_six_steps_that_have_an_act():
    """Инвариант §3: AUTO ⇔ у шага есть `act`, автоматических ровно шесть."""
    auto = tuple(s.id for s in STEPS if s.owner == Owner.AUTO)
    with_act = tuple(s.id for s in STEPS if s.act is not None)
    assert auto == AUTO_IDS
    assert with_act == AUTO_IDS


def test_command_runner_is_called_as_a_method(tmp_path):
    """§12.8 п.4: форма зова ОДНА — `ctx.runner.run(argv, cwd=..., timeout=...)`.

    Форма зова здесь важна не из вкуса: подставить вместо раннера объект —
    единственный способ проверить подключение без живых процессов, и любой
    следующий фейк (в мутационном гейте, в репетиции §9) обязан знать, какую
    форму изображать. Две формы = два имени на одну вещь
    ([[jarvis-two-numbers-for-one-thing]])."""
    root = build_root(tmp_path)
    _, runner, _ = run_once(root)
    assert runner.called_as_method or runner.called_as_callable, \
        "наружу не позвали ни разу"
    assert runner.called_as_callable == 0, (
        f"раннер зовут как функцию ({runner.called_as_callable} раз) — "
        f"контракт §12.8 п.4 задаёт ctx.runner.run(argv, cwd=..., timeout=...)")


def test_prepared_root_runs_to_done(tmp_path):
    """Здоровье фикстуры отдельным тестом: если корень не доводится до конца,
    краснеет ОДНА строка с понятной причиной, а не все сторожа разом."""
    root = build_root(tmp_path)
    outcome, runner, killed = run_once(root)
    assert killed is None
    assert outcome.code == 0, f"остановка на {outcome.stop_id}"
    assert outcome.visited == CYCLE_IDS, "не все шаги цикла пройдены"

    # Действия исполняются только автоматические и только в порядке §3.
    assert set(outcome.acted) <= set(AUTO_IDS)
    assert outcome.acted == tuple(i for i in AUTO_IDS if i in set(outcome.acted))
    # S12 — единственный, кто вправе не понадобиться: его действие это
    # «ограниченное по времени ожидание» (§12.6 п.6), а раннер после S11 уже
    # жив, и факт появляется до того, как ждать стало нужно.
    assert not (set(AUTO_IDS) - set(outcome.acted) - {"S12"})
    assert sum(_is_paid_drill(c) for c in runner.calls) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Д1 — обрыв на КАЖДОМ шаге
# ═════════════════════════════════════════════════════════════════════════════

def _kill_points():
    """Все точки обрыва: после каждого шага, а у автоматических — ещё и между
    действием и его подтверждением. Это и есть самый опасный момент: факт уже
    есть, а никто ещё не проверил, что он есть."""
    points = []
    for step in cycle_steps():
        if step.act is not None:
            points.append((step.id, "act"))
        points.append((step.id, "step"))
    return points


@pytest.mark.parametrize("kill_after", _kill_points(),
                         ids=lambda p: f"{p[0]}-после-{p[1]}")
def test_d1_kill_after_every_step_resumes_and_repeats_nothing(tmp_path, kill_after):
    """Д1. Процесс убит после шага N — повторный запуск доводит подключение до
    того же конца и не повторяет ни одного уже сделанного действия.

    Сравнение РАЗНОСТНОЕ: прерванная пара запусков обязана прийти туда же,
    куда пришёл непрерывный прогон на таком же корне. «Туда же» проверяется по
    диску, а не по словам процесса."""
    clean = build_root(tmp_path / "clean")
    now = time.time()
    whole, _, killed = run_once(clean, now=now)
    assert killed is None
    assert whole.code == 0, f"непрерывный прогон встал на {whole.stop_id}"

    step_id, phase = kill_after
    assert step_id in whole.visited, (
        f"шаг {step_id} не пройден вовсе — обрыв на нём непроверяем, а сам шаг "
        f"недостижим")
    if phase == "act" and step_id not in whole.acted:
        pytest.skip(f"{step_id}: действие не понадобилось (факт появился "
                    f"раньше) — прерывать нечего")

    broken = build_root(tmp_path / "broken")
    _, first_runner, killed = run_once(broken, kill_after=kill_after, now=now)
    if killed is None:
        pytest.fail(f"точка обрыва {kill_after} не достигнута: прогон "
                    f"закончился раньше, чем дошёл до неё")

    second, second_runner, killed2 = run_once(broken, now=now)
    assert killed2 is None

    # 1. Ни одно завершённое действие не повторено.
    repeated = set(killed.acted) & set(second.acted)
    assert not repeated, (
        f"после обрыва на {kill_after} повторно исполнены шаги {sorted(repeated)}")

    # 2. Платное — тем более (§2.3): дрил и подъём звались не больше раза.
    calls = first_runner.calls + second_runner.calls
    assert sum(_is_paid_drill(c) for c in calls) <= 1, "платный дрил повторён"
    assert sum(_is_start(c) for c in calls) <= 1, "подъём клиента повторён"

    # 3. Довели до конца — до того же, что и непрерывный прогон.
    assert second.code == whole.code
    assert set(killed.acted) | set(second.acted) == set(whole.acted)
    assert fingerprint(broken) == fingerprint(clean)


def test_d1_a_dead_subprocess_is_an_interruption_too(tmp_path):
    """Д1 в форме, которая случается чаще выключенного питания: подпроцесс не
    выполнился вовсе (таймаут, скрипта нет). По §12.6 п.3 это ИСКЛЮЧЕНИЕ, и
    оно обязано стать `ПРОТИВОРЕЧИЕ`/код 1 с внятной причиной, а не
    трассировкой и не тихим «шаг закрыт».

    Следующий запуск на здоровом раннере доводит подключение до конца, и
    подъём при этом состоялся РОВНО ОДИН раз: сорванный вызов ничего не
    сделал, значит и повтором не считается."""
    root = build_root(tmp_path)
    now = time.time()
    first, first_runner, killed = run_once(root, now=now,
                                           trouble_on="chatter_client")
    assert killed is None, "обрыв здесь не имитировался"
    assert first_runner.troubled, "беда не наступила — проверять нечего"
    assert first.code == 1, (
        f"невозможность выполнить подпроцесс дала код {first.code}: "
        f"«не состоялось» и «сказал нет» слиплись")

    second, second_runner, killed2 = run_once(root, now=now)
    assert killed2 is None
    assert second.code == 0, f"после сорванного вызова встали на {second.stop_id}"
    started = [c for c in first_runner.calls + second_runner.calls if _is_start(c)]
    assert len(started) - len(first_runner.troubled) == 1
    assert sum(_is_paid_drill(c)
               for c in first_runner.calls + second_runner.calls) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Д7 — состояние ТОЛЬКО из фактов
# ═════════════════════════════════════════════════════════════════════════════

def _root_ready(base):
    return build_root(base)


def _root_bare(base):
    root = build_root(base)
    strip_human_facts(root)
    return root


def _root_done(base):
    root = build_root(base)
    outcome, _, killed = run_once(root)
    assert killed is None and outcome.code == 0, "фикстура «завершено» не собралась"
    return root


_ROOT_STATES = {"готов-к-старту": _root_ready, "ничего-не-начато": _root_bare,
                "завершено": _root_done}


@pytest.mark.parametrize("state", sorted(_ROOT_STATES))
def test_d7_deleting_the_journal_changes_no_verdict(tmp_path, state):
    """Д7, направление первое: журнала не стало — не изменился ни один вердикт.

    Журнал кладётся сюда РУКАМИ теста, а не прогоном: по §12.6 п.4 его пишет
    только `__main__.py`, и сторож, ждущий журнала от собственного цикла, был
    бы зелёным по построению ([[jarvis-escape-eaten-at-file-write]])."""
    root = _ROOT_STATES[state](tmp_path)
    now = time.time()
    _write_journal(root, HONEST_JOURNAL)
    with_journal = verdicts(root, now=now)

    journal_path(root).unlink()
    without_journal = verdicts(root, now=now)

    assert [(sid, v) for sid, v, _ in without_journal] == \
           [(sid, v) for sid, v, _ in with_journal], "журнал двигает вердикты"
    assert without_journal == with_journal, "журнал двигает улики facts"


@pytest.mark.parametrize("state", sorted(_ROOT_STATES))
def test_d7_forged_journal_changes_no_verdict(tmp_path, state):
    """Д7, направление второе: в журнал дописали «всё готово» — вердикты те же.

    Оба направления нужны: сторож в одну сторону зелен и тогда, когда журнал
    читают, а он случайно совпал с фактами."""
    root = _ROOT_STATES[state](tmp_path)
    now = time.time()
    without = verdicts(root, now=now)

    _write_journal(root, FORGED_JOURNAL)
    forged = verdicts(root, now=now)

    assert [(sid, v) for sid, v, _ in forged] == \
           [(sid, v) for sid, v, _ in without], "подделка журнала двигает вердикты"
    assert forged == without, "подделка журнала двигает улики facts"


def test_d7_forged_journal_does_not_move_the_run_forward(tmp_path):
    """Подделка не двигает и сам прогон: остановка там же, что и без журнала,
    и ни одного лишнего действия — тем более платного."""
    honest = _root_bare(tmp_path / "honest")
    forged = _root_bare(tmp_path / "forged")
    _write_journal(forged, FORGED_JOURNAL)

    now = time.time()
    a, _, ka = run_once(honest, now=now)
    b, b_runner, kb = run_once(forged, now=now)
    assert ka is None and kb is None
    assert (b.code, b.stop_id) == (a.code, a.stop_id)
    assert b.acted == a.acted
    assert sum(_is_paid_drill(c) for c in b_runner.calls) == 0


@pytest.mark.parametrize("state", sorted(_ROOT_STATES))
def test_d7_probes_write_nothing_to_disk(tmp_path, state):
    """§12.5 и §12.6 п.4: `probes.py` не пишет НИЧЕГО — ни файла, ни строки
    журнала.

    Проба, оставляющая след, перестаёт быть чистой функцией факта, и тогда
    «пробы прогнали» само становится состоянием — вторым представлением,
    которое молча погасит первое."""
    root = _ROOT_STATES[state](tmp_path)
    now = time.time()
    before = fingerprint(root, with_journal=True, with_mtime=True)
    verdicts(root, now=now)
    verdicts(root, now=now)
    assert fingerprint(root, with_journal=True, with_mtime=True) == before


# ═════════════════════════════════════════════════════════════════════════════
# Д9 — запись реестра атомарна
# ═════════════════════════════════════════════════════════════════════════════

def _registry(root: Path) -> Path:
    return Path(root) / "chatter" / "clients" / "registry.yaml"


def _readable(path: Path) -> str:
    """`ok` / `нет файла` / причина, по которой `parse_registry` его не берёт."""
    if not path.exists():
        return "нет файла"
    try:
        parse_registry(path.read_text(encoding="utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        return f"НЕ ЧИТАЕТСЯ: {type(exc).__name__}: {exc}"
    return "ok"


class ReplaceSpy:
    """Подменяет ВСЕ четыре способа переименовать файл или каталог. Реализация
    вольна звать `os.replace`, `os.rename`, `Path.replace` или `Path.rename` —
    сторож обязан видеть любой, иначе он ловит стиль, а не механику.

    `watch` — путь, въезд в который наблюдается: `registry.yaml` у Д9 и
    каталог клиента у его пары. `check` — как выглядит целое: для реестра это
    «читается `parse_registry`», для каталога — «полный комплект файлов»."""

    def __init__(self, watch: Path, *, fail: bool = False, check=None):
        self.watch = Path(watch)
        self.fail = fail
        self.check = check or _readable
        self.pairs: list[tuple[Path, Path]] = []
        self.observed: list[tuple[str, str]] = []
        self._real = os.replace

    def install(self, monkeypatch):
        monkeypatch.setattr(os, "replace", self)
        monkeypatch.setattr(os, "rename", self)
        monkeypatch.setattr(Path, "replace", lambda s, t: self(s, t))
        monkeypatch.setattr(Path, "rename", lambda s, t: self(s, t))
        actions = __import__("chatter.connect.actions", fromlist=["*"])
        for name in ("replace", "rename"):
            if hasattr(actions, name):
                monkeypatch.setattr(actions, name, self)

    def __call__(self, src, dst, *args, **kwargs):
        src_p, dst_p = Path(src), Path(dst)
        if dst_p == self.watch:
            self.pairs.append((src_p, dst_p))
            # Улика для «частичного состояния не наблюдается»: и то, что
            # въезжает, и то, что лежит сейчас, обязаны быть ЦЕЛЫМИ.
            self.observed.append((self.check(src_p), self.check(dst_p)))
            if self.fail:
                raise OSError(28, "прерывание ровно в момент подмены")
        return self._real(src, dst, *args, **kwargs)


def test_d9_registry_is_written_through_a_rename_of_a_sibling_temp_file(
        tmp_path, monkeypatch):
    """Д9, механика. `registry.yaml` подменяется целиком: временный файл РЯДОМ
    и переименование поверх (§12.5).

    Прямая запись поверх живого файла оставила бы полуфайл, а его читает
    гардиан каждые ~30 с — `fatal` у всех клиентов сразу, включая живых (§6).
    Механики нет — сторож обязан краснеть, поэтому проверяется сам факт
    переименования, а не только благополучный результат."""
    root = build_root(tmp_path)
    spy = ReplaceSpy(_registry(root))
    spy.install(monkeypatch)

    outcome, _, killed = run_once(root)
    assert killed is None and outcome.code == 0

    assert spy.pairs, ("реестр записан МИМО os.replace — значит существует "
                       "момент, когда гардиан читает половину файла")
    for src, dst in spy.pairs:
        assert src.parent == dst.parent, (
            f"временный файл {src} лежит не рядом с реестром: переименование "
            f"между томами не атомарно")


def test_d9_only_whole_registries_are_ever_swapped_in(tmp_path, monkeypatch):
    """Д9, суть: промежуточного состояния не наблюдается.

    В момент подмены и НОВЫЙ файл, и тот, что лежит на месте, обязаны читаться
    `parse_registry`. Половины реестра не существует ни в один момент (§6)."""
    root = build_root(tmp_path)
    spy = ReplaceSpy(_registry(root))
    spy.install(monkeypatch)

    outcome, _, killed = run_once(root)
    assert killed is None and outcome.code == 0
    assert spy.observed, "подмены реестра не было — проверять нечего"
    for src_state, dst_state in spy.observed:
        assert src_state == "ok", f"подменяют НЕполный реестр: {src_state}"
        assert dst_state in ("ok", "нет файла"), (
            f"на месте реестра лежит нечитаемое ещё ДО подмены: {dst_state}")
    assert _readable(_registry(root)) == "ok"


def test_d9_crash_at_the_moment_of_replace_leaves_the_registry_readable(
        tmp_path, monkeypatch):
    """Д9, обрыв: процесс убит ровно в момент подмены.

    На диске остаётся ПРЕЖНИЙ реестр, байт в байт, и он читается."""
    root = build_root(tmp_path)
    registry = _registry(root)
    before = registry.read_bytes()

    spy = ReplaceSpy(registry, fail=True)
    spy.install(monkeypatch)
    run_expecting_trouble(root)

    assert spy.pairs, "подмены не было — сбой пришёлся мимо записи реестра"
    assert _readable(registry) == "ok"
    assert registry.read_bytes() == before, (
        "реестр изменён, хотя подмена не состоялась — значит писали поверх")


def test_d9_after_a_crashed_registry_write_the_next_run_finishes(
        tmp_path, monkeypatch):
    """Д9 + Д1: сорванная запись реестра не мешает следующему запуску дойти
    до конца — обрыв обязан быть невидим для результата (§6)."""
    root = build_root(tmp_path)
    now = time.time()
    spy = ReplaceSpy(_registry(root), fail=True)
    spy.install(monkeypatch)
    run_expecting_trouble(root, now=now)
    monkeypatch.undo()

    outcome, runner, killed = run_once(root, now=now)
    assert killed is None
    assert outcome.code == 0, f"после сорванной записи встали на {outcome.stop_id}"
    assert _readable(_registry(root)) == "ok"
    assert sum(_is_paid_drill(c) for c in runner.calls) == 1


# ── Пара к Д9: перенос каталога клиента (S4) так же атомарен (§12.8 п.2) ────

CONFIG_FILES = ("persona.md", "knowledge.md", "playbook.md", "settings.yaml")


def _client_dir(root: Path) -> Path:
    return Path(root) / "chatter" / "clients" / SLUG


def _client_dir_state(root: Path) -> str:
    """`нет каталога` / `ok` / причина, по которой `load_config` его не берёт.

    Читается НАСТОЯЩИМ `load_config` — тем самым, которым спрашивает проба S4
    (§3): свой упрощённый разбор согласился бы сам с собой."""
    d = _client_dir(root)
    if not d.exists():
        return "нет каталога"
    try:
        load_config(d.parent, SLUG)
    except Exception as exc:                                    # noqa: BLE001
        return f"НЕ ГРУЗИТСЯ: {type(exc).__name__}: {exc}"
    return "ok"


def _dir_is_a_whole_config(path: Path) -> str:
    """Целый ли это комплект конфига. `load_config` тут не годится: у времянки
    имя не равно слагу, а он открывает клиента ПО ИМЕНИ КАТАЛОГА."""
    if not path.exists():
        return "нет каталога"
    if not path.is_dir():
        return f"НЕ КАТАЛОГ: {path}"
    missing = [f for f in CONFIG_FILES if not (path / f).is_file()]
    return f"НЕПОЛНЫЙ: не хватает {missing}" if missing else "ok"


def test_s4_client_dir_arrives_by_a_single_rename_of_a_sibling_temp_dir(
        tmp_path, monkeypatch):
    """§12.8 п.2, механика: копия собирается во ВРЕМЕННОМ каталоге рядом и
    въезжает ОДНИМ переименованием.

    Копирование файл за файлом оставляло бы после обрыва каталог, который не
    грузится, — а по §12.8 п.2 такой каталог теперь означает «правка человека,
    разбирать человеку», и подключение вставало бы намертво у того, кто ничего
    не делал руками. Это тот же класс, что атомарная запись реестра, и здесь
    он не покрыт ничем, кроме этой пары."""
    root = build_root(tmp_path)
    spy = ReplaceSpy(_client_dir(root), check=_dir_is_a_whole_config)
    spy.install(monkeypatch)

    outcome, _, killed = run_once(root)
    assert killed is None and outcome.code == 0, (
        f"остановка на {getattr(outcome, 'stop_id', None)}")

    assert spy.pairs, ("каталог клиента собран НЕ переименованием — значит "
                       "существует момент, когда на диске лежит полукопия")
    assert len(spy.pairs) == 1, (
        f"въездов в каталог клиента {len(spy.pairs)}, а обещано одно "
        f"переименование: несколько подмен — это снова наблюдаемая середина")
    src, dst = spy.pairs[0]
    assert src.parent == dst.parent, (
        f"времянка {src} лежит не рядом с каталогом клиента: переименование "
        f"между томами не атомарно")
    assert _client_dir_state(root) == "ok"


def test_s4_crash_at_the_moment_of_the_swap_leaves_no_half_copy(
        tmp_path, monkeypatch):
    """§12.8 п.2, суть: полукопии ОТ НАШЕЙ РУКИ не существует.

    Процесс убит ровно в момент въезда. После этого каталога клиента либо нет
    вовсе, либо он целый: состояния «есть, но не грузится» наша рука оставить
    не может — иначе следующий запуск объявит ПРОТИВОРЕЧИЕ и потребует
    человека там, где никто ничего не правил."""
    root = build_root(tmp_path)
    spy = ReplaceSpy(_client_dir(root), fail=True, check=_dir_is_a_whole_config)
    spy.install(monkeypatch)
    run_expecting_trouble(root)

    assert spy.pairs, "подмены не было — сбой пришёлся мимо переноса каталога"
    for src_state, dst_state in spy.observed:
        assert src_state == "ok", f"въезжает НЕполный комплект: {src_state}"

    state = _client_dir_state(root)
    assert state in ("нет каталога", "ok"), (
        f"после обрыва на диске лежит полукопия ({state}) — по §12.8 п.2 её "
        f"прочтут как правку человека, и подключение встанет навсегда")

    # То же самое глазами пробы: тупика нет.
    runner = FakeRunner(root, SLUG, time.time())
    ctx = make_ctx(root, runner)
    s4 = next(st for st in STEPS if st.id == "S4")
    assert s4.probe(ctx).verdict != Verdict.CONFLICT, (
        "обрыв посреди переноса объявлен противоречием: команда не чинит то, "
        "что оставила сама")


def test_s4_after_an_interrupted_transfer_the_next_run_finishes(
        tmp_path, monkeypatch):
    """Пара к Д9 + Д1: сорванный перенос каталога не мешает следующему запуску
    дойти до конца — обрыв обязан быть невидим для результата (§6)."""
    root = build_root(tmp_path)
    now = time.time()
    spy = ReplaceSpy(_client_dir(root), fail=True, check=_dir_is_a_whole_config)
    spy.install(monkeypatch)
    run_expecting_trouble(root, now=now)
    monkeypatch.undo()

    outcome, runner, killed = run_once(root, now=now)
    assert killed is None
    assert outcome.code == 0, f"после сорванного переноса встали на {outcome.stop_id}"
    assert _client_dir_state(root) == "ok"
    assert sum(_is_paid_drill(c) for c in runner.calls) == 1


# ── Пара к Д9: правка settings.yaml ЖИВОГО конфига (S9) так же атомарна ────

# Ключи, без которых `load_config` отказывается открывать клиента
# (`chatter/config/loader.py`, `_require`). Полуфайл — это файл, в котором их
# уже/ещё нет.
REQUIRED_SETTINGS_KEYS = ("model", "language", "owner_id", "persona_name")


def _settings_path(root: Path) -> Path:
    return _client_dir(root) / "settings.yaml"


def _settings_is_whole(path: Path) -> str:
    """`нет файла` / `ok` / чем именно этот файл не целый конфиг.

    Годится и для времянки, и для того, что лежит на месте: `load_config`
    здесь звать нельзя — он открывает клиента ПО ИМЕНИ КАТАЛОГА, а у времянки
    имя другое. Зато обязательные ключи читаются у кого угодно."""
    if not path.exists():
        return "нет файла"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        return f"НЕ РАЗБИРАЕТСЯ: {type(exc).__name__}: {exc}"
    if not isinstance(raw, dict):
        return f"НЕ ОТОБРАЖЕНИЕ: {type(raw).__name__}"
    missing = [k for k in REQUIRED_SETTINGS_KEYS if k not in raw]
    return f"НЕПОЛНЫЙ: не хватает {missing}" if missing else "ok"


def _allowlist(root: Path) -> list:
    raw = yaml.safe_load(_settings_path(root).read_text(encoding="utf-8")) or {}
    return list((raw.get("telegram") or {}).get("allowlist") or [])


def test_s9_settings_are_written_through_a_rename_of_a_sibling_temp_file(
        tmp_path, monkeypatch):
    """§12.5 в третьем месте: `act_s9` правит `settings.yaml` ЖИВОГО клиента.

    Цена полуфайла здесь выше, чем у реестра: этот файл читает сам раннер, а
    гардиан пересматривает состав каждые ~30 с. Запись поверх живого конфига
    создаёт окно, в котором клиент не грузится, — и попасть в него можно без
    всякого обрыва, просто по таймеру. Значит: времянка РЯДОМ (между томами
    переименование не атомарно, `%TEMP%` не годится) и ОДНА подмена."""
    root = build_root(tmp_path)
    spy = ReplaceSpy(_settings_path(root), check=_settings_is_whole)
    spy.install(monkeypatch)

    outcome, _, killed = run_once(root)
    assert killed is None and outcome.code == 0, (
        f"остановка на {getattr(outcome, 'stop_id', None)}")

    assert spy.pairs, ("settings.yaml записан МИМО переименования — значит "
                       "существует момент, когда раннер и гардиан читают "
                       "половину конфига живого клиента")
    assert len(spy.pairs) == 1, (
        f"подмен settings.yaml {len(spy.pairs)}, а нужна одна: каждая лишняя — "
        f"ещё одно окно, в котором клиент не грузится")
    src, dst = spy.pairs[0]
    assert src.parent == dst.parent, (
        f"времянка {src} лежит не рядом с конфигом ({dst.parent}): "
        f"переименование между томами не атомарно")
    for src_state, dst_state in spy.observed:
        assert src_state == "ok", f"въезжает НЕполный конфиг: {src_state}"
        assert dst_state in ("ok", "нет файла"), (
            f"на месте конфига лежит неполное ещё ДО подмены: {dst_state}")
    assert _settings_is_whole(_settings_path(root)) == "ok"


def test_s9_crash_at_the_moment_of_the_swap_leaves_the_old_config_loadable(
        tmp_path, monkeypatch):
    """Сбой ровно в момент подмены: на диске остаётся ПРЕЖНИЙ конфиг, и он
    грузится настоящим `load_config` — тем самым, которым спрашивает проба S4.

    Проверяется и то, что правка не применилась наполовину: allowlist остался
    прежним, а не «уже с владельцем, но ещё без дрил-контакта»."""
    root = build_root(tmp_path)
    spy = ReplaceSpy(_settings_path(root), fail=True, check=_settings_is_whole)
    spy.install(monkeypatch)
    run_expecting_trouble(root)

    assert spy.pairs, "подмены не было — сбой пришёлся мимо правки конфига"
    assert _settings_is_whole(_settings_path(root)) == "ok"
    assert _client_dir_state(root) == "ok", (
        "после сорванной правки живой конфиг не грузится — раннер не "
        "поднимется, а гардиан будет ронять его по кругу")
    assert _allowlist(root) == [], (
        "allowlist изменён, хотя подмена не состоялась — значит писали поверх")


def test_s9_after_an_interrupted_settings_write_the_next_run_finishes(
        tmp_path, monkeypatch):
    """Пара к Д9 + Д1: сорванная правка конфига не мешает следующему запуску
    дойти до конца и дописать allowlist (§6: обрыв невидим для результата)."""
    root = build_root(tmp_path)
    now = time.time()
    spy = ReplaceSpy(_settings_path(root), fail=True, check=_settings_is_whole)
    spy.install(monkeypatch)
    run_expecting_trouble(root, now=now)
    monkeypatch.undo()

    outcome, runner, killed = run_once(root, now=now)
    assert killed is None
    assert outcome.code == 0, f"после сорванной правки встали на {outcome.stop_id}"
    assert _client_dir_state(root) == "ok"
    allow = _allowlist(root)
    assert OWNER_CHAT_ID in allow and DRILL_CONTACT_ID in allow, (
        f"allowlist после доработки: {allow}")
    assert sum(_is_paid_drill(c) for c in runner.calls) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Д12 — идемпотентность
# ═════════════════════════════════════════════════════════════════════════════

def test_d12_second_run_on_a_finished_connection_does_nothing(tmp_path):
    """Д12. Подключение завершено — повторный запуск не исполняет ни одного
    действия и ни во что не звонит мутирующим вызовом.

    Второй прогон идёт БЕЗ `--drill-yes`: флаг не запоминается (§4), и
    завершённость обязана держаться фактом на диске, а не разрешением
    потратить."""
    root = build_root(tmp_path)
    now = time.time()
    first, _, killed = run_once(root, now=now, drill_yes=True)
    assert killed is None and first.code == 0

    second, runner, killed = run_once(root, now=now, drill_yes=False)
    assert killed is None
    assert second.code == 0
    assert second.acted == (), f"повторный запуск исполнил {second.acted}"
    mutating = [c.argv for c in runner.calls if _is_mutating(c)]
    assert not mutating, f"повторный запуск позвал наружу: {mutating}"


def test_d12_two_runs_in_a_row_change_nothing_on_disk(tmp_path):
    """Д12, строгое прочтение: «не меняют на диске НИЧЕГО» — включая журнал и
    включая время правки.

    Перезапись тем же содержимым — это тоже запись: она двигает mtime, а на
    mtime смотрят и человек, и сторожа. Прочтение выбрано самым строгим
    сознательно: послабление (дописать в журнал строку «всё уже закрыто»)
    обязано быть решением владельца, а не побочным эффектом реализации."""
    root = build_root(tmp_path)
    now = time.time()
    first, _, killed = run_once(root, now=now, drill_yes=True)
    assert killed is None and first.code == 0

    after_first = fingerprint(root, with_journal=True, with_mtime=True)
    run_once(root, now=now, drill_yes=False)
    after_second = fingerprint(root, with_journal=True, with_mtime=True)
    run_once(root, now=now, drill_yes=False)
    after_third = fingerprint(root, with_journal=True, with_mtime=True)

    assert after_second == after_first
    assert after_third == after_first


def test_d12_repeat_run_touches_neither_active_yaml_nor_the_registry(tmp_path):
    """Д12 в самом дорогом месте: повторный запуск не трогает ни ложный канал
    §0-бис, ни источник истины подключения.

    Реестр читает гардиан каждые ~30 с; лишняя перезапись — это лишний шанс
    показать ему половину файла, и делается она ни за чем."""
    root = build_root(tmp_path)
    now = time.time()
    first, _, killed = run_once(root, now=now, drill_yes=True)
    assert killed is None and first.code == 0

    watched = [_registry(root), root / "chatter" / "clients" / "active.yaml"]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in watched]
    run_once(root, now=now, drill_yes=False)
    after = [(p.read_bytes(), p.stat().st_mtime_ns) for p in watched]
    assert after == before
