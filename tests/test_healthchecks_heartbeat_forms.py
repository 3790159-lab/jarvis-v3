# -*- coding: utf-8 -*-
"""Dead-man-пінг обязан знать ОБЕ формы отметки живости chatter-раннера.

ТРЕТЬЕ появление одного дефекта. Первое чинили 16.08 в `ops_watchdog.py`,
второе 18.08 в клиентской панели (экран Ольги двое суток врал «Немає зв'язку»
при живом боте). Здесь он лежал ТИХО: `HEALTHCHECKS_URL` не задан, скрипт
выходит нулём на 31-й строке, ничего не проверив, — то есть внешний сторож
стоит в расписании (задача `JarvisHealthchecksPing`, раз в 5 минут) и не делает
ничего. Дефект выстрелил бы ровно в тот день, когда сторожа зарядят: он
немедленно и навсегда объявил бы раннер мёртвым.

Проверка ПОВЕДЕНЧЕСКАЯ, как и соседний `test_healthchecks_ping.py`: там урок
P16-а — тест, читавший текст скрипта, был зелёным, пока Планировщик молча
отвергал триггер. Поэтому здесь гоняется живой powershell на подставном дереве.

Кодировка вывода пинится в дочернем процессе (`[Console]::OutputEncoding`), иначе
кириллица приезжает в cp866 и сторож превращается в лотерею кодировки — та
самая грабля, что дала 13 ложных падений гейта из Bash. Пин проверяется
внутри каждого прогона: если `healthy=` не разобралось, тест падает на этом.

ЗАМЕР ПРОТИВ СЛЕПОТЫ (сделан ДО того, как сторож принят): на СЛОМАННОМ
скрипте проверки обязаны краснеть. Первая редакция сверяла ИМЯ ФАЙЛА в
скобках — и была ЗЕЛЁНОЙ на сломанном (2 из 3), потому что тот имени НЕ
ПЕЧАТАЛ ВОВСЕ, а «имени нет» читалось как «жалобы нет». Поэтому сверка
идёт по НАЗВАНИЮ ПРОВЕРКИ (`RUNNER_CHECK`): оно есть в жалобе всегда,
в обеих редакциях скрипта.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="dead-man-пінг только под Windows")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthchecks_ping.ps1"
FRESH = 180          # дефолт -FreshSeconds в скрипте
# Имя проверки, как его печатает скрипт. Сверка идёт по НЕМУ, а не по имени
# файла: СЛОМАННЫЙ скрипт имени НЕ ПЕЧАТАЛ ВОВСЕ, и «имени нет»
# читалось как «жалобы нет» — первая редакция сторожа была СЛЕПОЙ
# (замер: 2 из 3 зеленели на сломанном скрипте).
RUNNER_CHECK = "chatter раннер"
DUMMY_URL = "http://127.0.0.1:9/never-used"   # -WhatIfOnly до сети не доходит


def _beat(root: Path, name: str, age_s: float) -> None:
    p = root / "state" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("beat", encoding="utf-8")
    t = time.time() - age_s
    os.utime(p, (t, t))


def _run(root: Path) -> str:
    """Прогнать сторожа на подставном корне и вернуть его вердикт.

    `-WhatIfOnly` печатает решение и выходит, не трогая сеть. Кодировку вывода
    пиним внутри дочернего процесса: иначе кириллица приезжает в cp866 и
    ломает декод под PYTHONUTF8=1.
    """
    command = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "& '{0}' -Root '{1}' -Url '{2}' -WhatIfOnly".format(SCRIPT, root, DUMMY_URL)
    )
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # Пин кодировки — предусловие, а не удобство: без него всё, что ниже,
    # сравнивало бы мусор с осмысленной строкой и зеленело бы случайно.
    assert "healthy=" in out, "вывод не разобрался (кодировка?): %r" % (out,)
    return out


def _all_fresh_but_runner(root: Path) -> None:
    """Остальные три отметки — свежие. Нас интересует ровно раннер."""
    for name in ("bot_heartbeat.txt", "chatter_guardian_heartbeat.txt",
                 "ops_watchdog_heartbeat.txt"):
        _beat(root, name, 5.0)


def test_fresh_per_slug_beat_clears_the_runner(tmp_path):
    """ВОСПРОИЗВЕДЕНИЕ: раннер жив (per-slug свежий), легаси-файл заброшен.

    До починки сторож видел только легаси и жаловался на живой раннер.
    """
    _all_fresh_but_runner(tmp_path)
    _beat(tmp_path, "chatter_heartbeat.txt", 200_000.0)
    _beat(tmp_path, "chatter_heartbeat_volska.txt", 10.0)

    out = _run(tmp_path)

    assert RUNNER_CHECK not in out, (
        "сторож пожаловался на раннер, который ЖИВ: прочитан только "
        "заброшенный легаси-файл\n" + out)


def test_names_the_freshest_form_when_everything_is_stale(tmp_path):
    """Если протухли ОБЕ формы — жалоба обязана назвать ту, что свежее.

    Держит сразу две вещи: берётся свежайшая (иначе в тексте оказалось бы
    легаси-имя с возрастом 200000 с) и источник НАЗВАН (иначе «heartbeat
    застарів» снова нельзя было бы отследить до файла).
    """
    _all_fresh_but_runner(tmp_path)
    _beat(tmp_path, "chatter_heartbeat.txt", 200_000.0)
    _beat(tmp_path, "chatter_heartbeat_volska.txt", FRESH * 2)

    out = _run(tmp_path)

    assert "(chatter_heartbeat_volska.txt)" in out, (
        "сторож не назвал свежайшую из протухших форм:\n" + out)
    assert "(chatter_heartbeat.txt)" not in out, (
        "обвинён самый старый файл — правило «берём свежайшую» не работает:\n" + out)


def test_legacy_only_tree_still_clears(tmp_path):
    """Одноарендное дерево (per-slug файла нет вовсе) обязано работать как было."""
    _all_fresh_but_runner(tmp_path)
    _beat(tmp_path, "chatter_heartbeat.txt", 10.0)

    out = _run(tmp_path)

    assert RUNNER_CHECK not in out, (
        "свежая легаси-отметка объявлена протухшей\n" + out)


def test_missing_beats_still_complain(tmp_path):
    """Починка не имеет права превратить «файлов нет» в тишину.

    Сторож на скоуп всегда рискует выродиться в «никогда никого»: если бы
    отбор перестал находить что-либо, три проверки выше зеленели бы, а
    ферма молча осталась бы без присмотра.
    """
    _all_fresh_but_runner(tmp_path)

    out = _run(tmp_path)

    assert RUNNER_CHECK in out, (
        "ни одной отметки раннера нет, а сторож молчит:\n" + out)
