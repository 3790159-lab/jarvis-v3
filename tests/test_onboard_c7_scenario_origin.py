# -*- coding: utf-8 -*-
"""DEV-36: C7 обязана отличать СВОЙ сценарий от ЧУЖОГО.

Сторожа написаны ОТ ТИКЕТА (`state/dev_backlog_pipeline.md`, DEV-36, вариант 1),
до правки `chatter/onboard/checks.py`.

## Класс ошибки

`_drill_scenarios` сперва ищет сценарий в каталоге сборки, а не найдя — уходит
в `docs/chatter/drills/{slug}*.yaml`. Фолбэк заведён законно: ручной эталон
держит сценарии там, и без него калибровка §6 краснела бы на файле, который
существует и отработал живой дрил.

Но отбор идёт по ПРЕФИКСУ ИМЕНИ, а не по происхождению файла. Значит C7
зеленеет на любом файле, чьё имя начинается со слага, — включая написанный
руками месяц назад, под другой прайс. Ночью 17.08 это и случилось дважды:
сперва чужим `drill.yaml`, который оставил в каталоге сборки параллельно
работавший агент, потом фолбэком по совпадению слага.

Цена ложного зелёного здесь — не «неточный отчёт»: C7 существует ради ПЛАТНОГО
живого прогона с человеком. Зелёное по ошибке = дрил по неизвестно чьему
сценарию, и узнают об этом уже потратив деньги и время человека.

## Что требует тикет (вариант 1, «фолбэк называет себя»)

Нашли не свой файл — не зелёное, а ФЛАГ с именем файла. Флаг не роняет
вердикт (как C12/C13), потому что для ручного эталона это законное состояние,
но и зелёным он не притворяется.

🔴 Главный сторож здесь — не сам флаг, а `test_a_real_defect_in_a_foreign_file_
stays_red`: «мягкое» состояние обязано покрывать ТОЛЬКО происхождение файла.
Если флаг заодно проглотит нераспознанный сценарий или чужой контакт, DEV-36
починит одно ложное зелёное и заведёт другое, потише.

$0: только чистые функции, tmp_path и настоящие файлы репозитория. В сеть и в
БД тесты не ходят.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from chatter.onboard import checks

REPO = Path(__file__).resolve().parents[1]

# Слаг и контакт РЕАЛЬНЫЕ: `DRILL_CONTACTS` — закрытый список, и пары
# «выдуманный slug + законный контакт» в природе не существует.
SLUG = "yarina"
MANUAL = REPO / "docs" / "chatter" / "drills" / "yarina-onboarding-1-6.yaml"


def _fake_repo(tmp_path: Path) -> Path:
    """Корень репозитория для прогона: свой, чтобы тест не писал в настоящий.

    `scripts/drill_reset.py` копируется НАСТОЯЩИЙ — C7 читает из него вторую
    копию `DRILL_CONTACTS`, и подмена файла заглушкой означала бы, что сверка
    двух списков проверяется на моей выдумке, а не на каноне.
    """
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "drill_reset.py", root / "scripts" / "drill_reset.py")
    (root / "docs" / "chatter" / "drills").mkdir(parents=True)
    return root


def _client(tmp_path: Path) -> Path:
    """Каталог клиента. C7 не читает пять файлов клиента — ей нужны только
    каталог, слаг и корень репозитория, — поэтому каталог здесь пустой:
    сторож обязан говорить о происхождении сценария, а не о полноте клиента."""
    d = tmp_path / "clients" / SLUG
    d.mkdir(parents=True)
    return d


def _run_c7(client_dir: Path, repo_root: Path, monkeypatch):
    monkeypatch.setattr(checks, "_REPO_ROOT", repo_root)
    res = checks.run_checks(client_dir, {"sections": {}}, slug=SLUG)
    return next(r for r in res if r.id == "C7")


def _scenario_text(contact: str | None = None) -> str:
    text = MANUAL.read_text(encoding="utf-8")
    if contact is not None:
        text = text.replace('contact: "8849893367:yarina"', f'contact: "{contact}"')
    return text


# ── происхождение ────────────────────────────────────────────────────────────

def test_own_scenario_in_the_build_dir_is_plain_green(tmp_path, monkeypatch):
    """Выход пайплайна — единственное, что имеет право быть зелёным без оговорок."""
    root = _fake_repo(tmp_path)
    client = _client(tmp_path)
    (client / "drill.yaml").write_text(_scenario_text(), encoding="utf-8")

    c7 = _run_c7(client, root, monkeypatch)

    assert c7.ok, f"свой сценарий, а C7 не зелёная: {c7.message}"
    assert not c7.is_flag, "свой сценарий помечен флагом — оговорка без причины"
    assert "drill.yaml" in c7.message, c7.message


def test_a_foreign_manual_scenario_is_a_flag_and_never_a_plain_green(tmp_path, monkeypatch):
    """Фолбэк обязан НАЗВАТЬ СЕБЯ: файл найден, но он не выход пайплайна."""
    root = _fake_repo(tmp_path)
    (root / "docs" / "chatter" / "drills" / "yarina-onboarding-1-6.yaml").write_text(
        _scenario_text(), encoding="utf-8")
    client = _client(tmp_path)

    c7 = _run_c7(client, root, monkeypatch)

    assert not c7.ok, "чужой файл принят за свой — ровно то ложное зелёное, ради которого DEV-36"
    assert c7.is_flag, "фолбэк уронил вердикт: ручной эталон — законное состояние, а не дефект"
    assert "yarina-onboarding-1-6.yaml" in c7.message, (
        f"флаг не назвал файл — человеку нечего открыть: {c7.message}")


def test_the_flag_names_where_the_file_came_from(tmp_path, monkeypatch):
    """Имени файла мало: `drill.yaml` в двух местах читается одинаково.
    Сообщение обязано сказать, что это РУЧНОЙ сценарий, а не выход пайплайна."""
    root = _fake_repo(tmp_path)
    (root / "docs" / "chatter" / "drills" / "yarina-manual.yaml").write_text(
        _scenario_text(), encoding="utf-8")
    client = _client(tmp_path)

    c7 = _run_c7(client, root, monkeypatch)

    low = c7.message.casefold()
    assert "docs/chatter/drills" in low.replace("\\", "/"), c7.message
    assert "ручн" in low, f"сообщение не говорит, что сценарий ручной: {c7.message}"


# ── флаг закрывает ТОЛЬКО происхождение ──────────────────────────────────────

def test_a_real_defect_in_a_foreign_file_stays_red(tmp_path, monkeypatch):
    """🔴 ГЛАВНЫЙ сторож. Чужой суффикс контакта уводит прогон к ДРУГОМУ
    клиенту — это дефект, а не оговорка о происхождении. Если флаг накроет и
    его, DEV-36 заменит одно ложное зелёное на тихое красное, которое никто не
    заметит: флаг вердикт не роняет."""
    root = _fake_repo(tmp_path)
    (root / "docs" / "chatter" / "drills" / "yarina-onboarding-1-6.yaml").write_text(
        _scenario_text(contact="8849893367:volska"), encoding="utf-8")
    client = _client(tmp_path)

    c7 = _run_c7(client, root, monkeypatch)

    assert not c7.ok
    assert not c7.is_flag, (
        "чужой контакт превращён во флаг — красное перестало ронять вердикт")


def test_an_unparsable_foreign_file_stays_red(tmp_path, monkeypatch):
    """Второй способ той же ошибки: файл ЕСТЬ, но стенд его не разберёт."""
    root = _fake_repo(tmp_path)
    (root / "docs" / "chatter" / "drills" / "yarina-onboarding-1-6.yaml").write_text(
        "steps: [ не список шагов\n", encoding="utf-8")
    client = _client(tmp_path)

    c7 = _run_c7(client, root, monkeypatch)

    assert not c7.ok
    assert not c7.is_flag, "неразбираемый сценарий превращён во флаг"


def test_no_scenario_at_all_is_red_and_not_a_flag(tmp_path, monkeypatch):
    """Регресс-сторож: «файла нет» и «файл чужой» — РАЗНЫЕ состояния.
    Прежнее поведение (красное) обязано уцелеть, иначе DEV-36 заодно выключит
    проверку у клиента, которому сценарий вообще не собрали."""
    root = _fake_repo(tmp_path)
    client = _client(tmp_path)

    c7 = _run_c7(client, root, monkeypatch)

    assert not c7.ok
    assert not c7.is_flag, "отсутствующий сценарий стал флагом — проверка выключена"


# ── вердикт ──────────────────────────────────────────────────────────────────

def test_the_flag_does_not_drop_the_verdict():
    """Флаг не красное: ручной эталон обязан проходить приёмку, иначе
    калибровка §6 краснеет на файле, который отработал живой дрил."""
    rows = [checks.CheckResult(id=cid, ok=True, is_flag=cid in checks.FLAG_IDS,
                               message="", file=None, line=None)
            for cid in checks.CHECK_IDS]
    rows = [checks.CheckResult(id="C7", ok=False, is_flag=True,
                               message="ручной сценарий", file=None, line=None)
            if r.id == "C7" else r for r in rows]

    assert checks.verdict(rows, reviewed=True) == 0, (
        "флаг C7 уронил вердикт — ручной эталон не пройдёт приёмку никогда")


def test_c7_red_still_drops_the_verdict():
    """Обратная сторона: обычное красное C7 обязано ронять вердикт."""
    rows = [checks.CheckResult(id=cid, ok=True, is_flag=cid in checks.FLAG_IDS,
                               message="", file=None, line=None)
            for cid in checks.CHECK_IDS]
    rows = [checks.CheckResult(id="C7", ok=False, is_flag=False,
                               message="контакт чужой", file=None, line=None)
            if r.id == "C7" else r for r in rows]

    assert checks.verdict(rows, reviewed=True) == 1


# ── мета: фикстура кусается ──────────────────────────────────────────────────

def test_the_manual_scenario_fixture_is_a_real_one(tmp_path):
    """Ловит бедную фикстуру: если бы сценарий не разбирался или его ожидания
    были вакуумны, «фолбэк = флаг» зеленело бы по чужой причине."""
    assert MANUAL.is_file(), f"живого сценария нет на месте: {MANUAL}"
    from chatter.core.drill import parse_scenario, vacuous_expectations

    scenario = parse_scenario(_scenario_text())
    assert scenario.contact == "8849893367:yarina"
    assert not vacuous_expectations(scenario, {}), (
        "ожидания живого сценария вакуумны — фикстура не кусается")
