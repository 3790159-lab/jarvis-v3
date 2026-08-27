# -*- coding: utf-8 -*-
"""§5.2 спеки `2026-08-27-alarm-channel-outside-the-broken-transport.md`:
dead-man пінг обязан считать систему здоровой ТОЛЬКО при свежем зелёном
вердикте связи — и FAIL-CLOSED, когда вердикта нет.

ЧТО СЕГОДНЯ. Критерий здоровья в `healthchecks_ping.ps1` — ровно два вида:
свежесть heartbeat-файлов и наличие процессов. Ни одной проверки того, что
машина способна достучаться наружу. Во время перехвата 26–27.08 все
heartbeat'ы были свежие, все процессы живы: скрипт считал систему здоровой и
слал «ok». Пронесло случайно — «ok» не доехал, и тишина сработала как надо.

ЧЕМ ЭТО ОПАСНО ЗАВТРА. При ЧАСТИЧНОМ перехвате (`hc-ping.com` доступна,
`api.telegram.org` — нет) «ok» доедет и АКТИВНО ПОГАСИТ внешнюю тревогу, пока
бот нем. Сторож, который сам гасит тревогу о собственной немоте, — не сторож.

ТРЕБОВАНИЕ, дословно: «считает систему здоровой только если свежий
`reachability` из §5.1 зелёный по всем термам. Нет свежего вердикта — это
ПРОБЛЕМА, а не „нет данных“ (fail-closed)». Три формы отсутствия проверяются
порознь: файла нет, файл протух, файл не читается. Все три обязаны краснеть —
«факта нет» не имеет права выглядеть как «фактов нет, значит всё хорошо»
([[jarvis-absence-is-not-contradiction]]).

КАК ПРОВЕРЯЕТСЯ. Поведенчески, живым powershell на подставном дереве, как и
соседние `test_healthchecks_ping.py` / `test_healthchecks_heartbeat_forms.py`:
урок P16-а — тест, читавший ТЕКСТ скрипта, был зелёным, пока Планировщик молча
отвергал триггер. `-WhatIfOnly` печатает решение и до сети не доходит.

⚠️ ПРОТИВ ПУСТОГО ЗЕЛЁНОГО. Проверки процессов в скрипте смотрят на ЖИВУЮ
машину, а не на подставной корень, поэтому `healthy=False` может оказаться
истинным и без всякой связи — и сторож на одном только `healthy=False` был бы
зелёным ПО ПОСТРОЕНИЮ. Поэтому каждый случай сверяется с БАЗОЙ (тот же корень
со свежим зелёным вердиктом): жалоба обязана БЫТЬ НОВОЙ по сравнению с базой.

Файл вердикта — `state/reachability_verdict.json` (контракт). Внутреннюю
форму спека не фиксирует, поэтому сторож кладёт одну и ту же правду в двух
видах (плоско и под `terms`): красное обязано означать ЛОГИКУ, а не выбор
имени поля. Реализации автор не видел — на момент написания связи в критерии
здоровья НЕТ, файл ОБЯЗАН быть красным.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="dead-man-пінг только под Windows")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthchecks_ping.ps1"
FRESH = 180                                   # дефолт -FreshSeconds в скрипте
DUMMY_URL = "http://127.0.0.1:9/never-used"   # -WhatIfOnly до сети не доходит

# Литерал из таблицы §5.1 — тот же, что и в `test_ops_watchdog_reachability`.
# Повторён намеренно: два потребителя одного контракта, и совпадение состава
# между ними — предмет сверки, а не общая переменная.
WIRES = ("tg_api", "llm_api", "r2")

# Файл вердикта по контракту. Лежит в `state/` подставного корня — того
# самого, который скрипту передаётся параметром `-Root`.
VERDICT_NAMES = ("reachability_verdict.json",)

# По чему узнаётся, что жалоба именно про связь. Набор, а не одно слово: текст
# скрипта украиноязычный, и требовать в нём английского термина было бы
# требованием к словарю, а не к смыслу. Имена проводов — из спеки.
MARKERS = ("reachability", "tg_api", "llm_api", "r2",
           "зв'язок", "зв`язок", "зв’язок", "звязок", "связь", "наверх", "назовні")


# ── подставное дерево ──────────────────────────────────────────────────────
def _beat(root: Path, name: str, age_s: float) -> None:
    p = root / "state" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("beat", encoding="utf-8")
    t = time.time() - age_s
    os.utime(p, (t, t))


def _fresh_beats(root: Path) -> None:
    """Все отметки живости свежие: сегодня этого достаточно для «ok»."""
    for name in ("chatter_heartbeat_volska.txt", "bot_heartbeat.txt",
                 "chatter_guardian_heartbeat.txt", "ops_watchdog_heartbeat.txt"):
        _beat(root, name, 5.0)


def _write_verdict(root: Path, age_s: float = 5.0, **wires) -> None:
    """Вердикт §5.1 на диск — под всеми правдоподобными именами.

    Тексты внутри намеренно ASCII: PowerShell 5.1 читает файл без BOM в
    системной кодовой странице, и кириллица в вердикте превратила бы сторожа в
    лотерею кодировки ([[jarvis-ps1-needs-utf8-bom]]).
    """
    terms = {w: {"ok": bool(wires[w]),
                 "detail": ("%s ok" % w) if wires[w] else ("%s unreachable" % w),
                 "error": None if wires[w] else "TLS handshake failed"}
             for w in wires}
    ts = time.time() - age_s
    payload = {"ts": int(ts),
               "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
               "terms": dict(terms)}
    payload.update(terms)                     # та же правда плоско
    text = json.dumps(payload, ensure_ascii=True, indent=2)
    (root / "state").mkdir(parents=True, exist_ok=True)
    for name in VERDICT_NAMES:
        p = root / "state" / name
        p.write_text(text, encoding="utf-8")
        os.utime(p, (ts, ts))                 # свежесть и по mtime тоже


# ── прогон ─────────────────────────────────────────────────────────────────
def _run(root: Path) -> tuple[bool, set[str], str]:
    """Вердикт скрипта: (healthy, множество жалоб, сырой вывод).

    Кодировка вывода пинится ВНУТРИ дочернего процесса: под Планировщиком
    консоль стартует в cp866, и без пина сторож сравнивал бы мусор с
    осмысленной строкой ([[jarvis-console-encoding-never-reaches-the-child]]).
    """
    command = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "& '{0}' -Root '{1}' -Url '{2}' -WhatIfOnly".format(SCRIPT, root, DUMMY_URL)
    )
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # Пин кодировки — предусловие, а не удобство: без него всё ниже зеленело бы
    # случайно.
    assert "healthy=" in out, "вывод не разобрался (кодировка?): %r" % (out,)

    healthy = None
    body_lines = []
    for line in out.splitlines():
        if healthy is None and line.startswith("healthy="):
            healthy = line.split("=", 1)[1].strip().lower() == "true"
            continue
        if healthy is not None:
            body_lines.append(line)
    body = "\n".join(body_lines).strip()
    problems = set()
    if body and body != "ok":
        for chunk in body.replace("\n", "; ").split(";"):
            if chunk.strip():
                problems.add(chunk.strip())
    return healthy, problems, out


def _baseline(root: Path) -> tuple[bool, set[str]]:
    """База: тот же корень, свежий вердикт, все три провода зелёные.

    Всё, что появится СВЕРХ этого набора жалоб, — заслуга проверки связи, а не
    состояния машины, на которой гоняют сторожа.
    """
    _write_verdict(root, age_s=5.0, **{w: True for w in WIRES})
    healthy, problems, _out = _run(root)
    return healthy, problems


def _mentions_link(problems: set[str]) -> bool:
    low = " ".join(problems).lower()
    return any(m.lower() in low for m in MARKERS)


# ── §5.2 fail-closed: отсутствие вердикта — ПРОБЛЕМА ───────────────────────
def test_missing_verdict_is_a_problem_not_a_shrug(tmp_path):
    """Вердикта нет вовсе → «ok» слать НЕЛЬЗЯ.

    Это главный случай: проба §5.1 не отработала (умер watchdog, умерла
    задача, не дошли руки включить), а пинг продолжает гасить внешнюю тревогу
    от имени системы, о связи которой он ничего не знает.
    """
    _fresh_beats(tmp_path)
    base_healthy, base_problems = _baseline(tmp_path)
    for name in VERDICT_NAMES:
        (tmp_path / "state" / name).unlink(missing_ok=True)

    healthy, problems, out = _run(tmp_path)

    new = problems - base_problems
    assert new, (
        "вердикта связи НЕТ, а список жалоб не изменился: критерий здоровья "
        "по-прежнему не знает про связь.\nбаза: %s\nстало: %s" % (
            sorted(base_problems), out))
    assert _mentions_link(new), (
        "жалоба появилась, но она не про связь: %s" % sorted(new))
    assert healthy is False, (
        "нет вердикта связи, а система объявлена здоровой — «нет данных» "
        "прочитано как «всё хорошо»:\n%s" % out)
    assert base_healthy is not False or base_problems != problems, (
        "база и случай неразличимы — сторож пуст")


def test_stale_verdict_is_a_problem_even_when_it_was_green(tmp_path):
    """Протухший ЗЕЛЁНЫЙ вердикт опаснее отсутствующего: он выглядит как факт.

    Спека требует именно СВЕЖИЙ вердикт. Вчерашнее «связь была» — это память,
    а не измерение, и гасить ею тревогу значит гасить её вчерашним днём.
    """
    _fresh_beats(tmp_path)
    _base_healthy, base_problems = _baseline(tmp_path)
    _write_verdict(tmp_path, age_s=10 * 24 * 3600, **{w: True for w in WIRES})

    healthy, problems, out = _run(tmp_path)

    new = problems - base_problems
    assert new, ("вердикт связи протух на десять суток, а жалоб не прибавилось:"
                 "\nбаза: %s\nстало: %s" % (sorted(base_problems), out))
    assert _mentions_link(new), "жалоба не про связь: %s" % sorted(new)
    assert healthy is False, "система здорова по вчерашнему вердикту:\n%s" % out


def test_unreadable_verdict_is_a_problem(tmp_path):
    """Файл есть, но не разбирается → тоже проблема.

    Битый вердикт — третья форма отсутствия, и она самая тихая: `try/catch`
    вокруг чтения естественно скатывается в «ну нет так нет».
    """
    _fresh_beats(tmp_path)
    _base_healthy, base_problems = _baseline(tmp_path)
    for name in VERDICT_NAMES:
        (tmp_path / "state" / name).write_text("{ это не json", encoding="utf-8")

    healthy, problems, out = _run(tmp_path)

    new = problems - base_problems
    assert new, ("вердикт связи не читается, а жалоб не прибавилось:"
                 "\nбаза: %s\nстало: %s" % (sorted(base_problems), out))
    assert healthy is False, "битый вердикт прочитан как здоровье:\n%s" % out


# ── §5.2: частичный перехват — тот самый случай, ради которого правка ──────
@pytest.mark.parametrize("broken", WIRES)
def test_a_single_dead_wire_fails_closed_and_names_it(tmp_path, broken):
    """Каждый провод порознь: красный обязан ПРОБИТЬСЯ до `/fail` с именем.

    §6.2 дословно: «dead-man шлёт `/fail` с именем провода». Без имени
    владелец получает «связь плохая» и идёт перезагружать роутер, тогда как
    молчит одна конкретная ручка.
    """
    _fresh_beats(tmp_path)
    _base_healthy, base_problems = _baseline(tmp_path)
    _write_verdict(tmp_path, age_s=5.0, **{w: (w != broken) for w in WIRES})

    healthy, problems, out = _run(tmp_path)

    new = problems - base_problems
    assert new, ("провод %s мёртв, а жалоб не прибавилось — частичный перехват "
                 "гасит тревогу «бодрым ok»:\nбаза: %s\nстало: %s"
                 % (broken, sorted(base_problems), out))
    assert healthy is False, (
        "провод %s мёртв, а система объявлена здоровой:\n%s" % (broken, out))
    joined = " ".join(new)
    assert broken in joined, (
        "жалоба не называет провод %s: %s" % (broken, sorted(new)))
    for w in WIRES:
        if w != broken:
            assert w not in joined, (
                "обвинён живой провод %s заодно с мёртвым %s — на стороне пинга "
                "вердикты снова свернулись в один" % (w, broken))


def test_all_green_verdict_adds_no_complaint(tmp_path):
    """Обратный ход §6.4: зелёное возвращается САМО.

    Проверка, которая красна ВСЕГДА, — не сторож, а фон
    ([[jarvis-gate-mutates-the-deploy-tree]]).
    """
    _fresh_beats(tmp_path)
    _write_verdict(tmp_path, age_s=5.0, **{w: True for w in WIRES})

    _healthy, problems, out = _run(tmp_path)

    assert not _mentions_link(problems), (
        "все три провода живы, а жалоба на связь всё равно есть:\n%s" % out)


# ── защита старого критерия: связь ДОБАВЛЕНА, а не подменила прежнее ───────
def test_green_link_does_not_rescue_a_stale_heartbeat(tmp_path):
    """Связь наружу не имеет права ОТМЕНЯТЬ прежние проверки.

    Правка добавляет третий вид критерия, а не заменяет два прежних. Зелёный
    провод при мёртвом раннере — это по-прежнему авария, и молчание о ней было
    бы новым способом соврать «ok».
    """
    for name in ("bot_heartbeat.txt", "chatter_guardian_heartbeat.txt",
                 "ops_watchdog_heartbeat.txt"):
        _beat(tmp_path, name, 5.0)
    _beat(tmp_path, "chatter_heartbeat_volska.txt", FRESH * 20)
    _write_verdict(tmp_path, age_s=5.0, **{w: True for w in WIRES})

    healthy, problems, out = _run(tmp_path)

    assert healthy is False, "протухшая отметка раннера объявлена здоровьем:\n%s" % out
    assert any("chatter" in p for p in problems), (
        "жалоба на раннер исчезла — новый критерий съел старый:\n%s" % out)
