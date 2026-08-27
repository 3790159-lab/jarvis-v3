# -*- coding: utf-8 -*-
"""§6.2 и §6.6 спеки `2026-08-27-volska-panel-instance`.

Сторожа писаны ОТ ТЕКСТА СПЕКИ ([[jarvis-guards-not-by-the-plan-author]]).

ДВА ПРАВИЛА, И ОНИ ПРО РАЗНОЕ:

§6.2 ВКЛЮЧЁННЫЙ слаг БЕЗ секции `panel` даёт `no_instance` — **КРАСНОЕ**, а не
     зелёное и не «пробы нет вовсе». Это ровно то состояние, в котором сейчас
     живёт volska: 7895 и 4573 цикла `no_instance`. Соблазн погасить лампу
     переписыванием пробы «по инстансам» дал бы ЗЕЛЁНОЕ ПО ПОСТРОЕНИЮ ровно
     там, где лежит проблема: из четырёх непрочитанных карточек эскалации ТРИ
     принадлежат volska, включая самую старую (26.6 суток). Довод записан в
     самом коде watchdog, и арка его не отменяет, а СОХРАНЯЕТ.

§6.6 ВЫКЛЮЧЕННЫЙ в ростере клиент пробы не имеет ВООБЩЕ — ни красной, ни
     зелёной. Правило уже есть (§5.1 прежней арки); здесь пин на то, что
     новая арка его не сломала. Красная проба о клиенте, которого никто не
     поднимает, — это шум, который съедает настоящее красное рядом.

🔴 ТРЕТЬЕ СОСТОЯНИЕ, КОТОРОЕ НЕЛЬЗЯ СЛИТЬ С ПЕРВЫМИ ДВУМЯ: «секции нет» — это
не «выключен» и не «инстанс молчит». Все три чинятся в разных местах, и
поэтому каждое проверяется отдельно.

СЧИТАЕМ, А НЕ ПЕРЕЧИСЛЯЕМ: состав ключей сверяется РАВЕНСТВОМ МНОЖЕСТВ и
СЧЁТОМ. Перебор известных имён слеп к имени, которого автор сторожа не угадал
— а «завтрашний слаг» и есть тот случай, ради которого сверка нужна.
"""
from __future__ import annotations

import importlib.util as _ilu
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OPS_PATH = REPO_ROOT / "scripts" / "ops_watchdog.py"

_spec = _ilu.spec_from_file_location("ops_watchdog_volska_noinstance", OPS_PATH)
ow = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ow)

ATT_PREFIX = getattr(ow, "ATTENTION_PROBE_PREFIX", "escalation:")
OUT_PREFIX = getattr(ow, "OUTGOING_PROBE_PREFIX", "outgoing:")
RUN_PREFIX = getattr(ow, "CLIENT_PROBE_PREFIX", "chatter_runner:")

HOST = "100.77.77.77"
DECOY_PORT = 7777
YARINA_PORT = 8123
DISABLED_PORT = 8099
REGISTRY_REL = "chatter/clients/registry.yaml"


# 🔴 ПОПРАВКА 1 (§3b): у volska база объявлена и слагу НЕ равна. Ростеру она
# безразлична — но синтетическая ферма, в которой расхождения не существует,
# приучает писать сторожей на реестре, которого нет.
DIVERGING_DBS = {"volska": ".secrets/demo.db"}


def _registry_yaml(clients) -> str:
    lines = ["clients:"]
    for slug, enabled, port in clients:
        lines.append("  %s:" % slug)
        lines.append("    enabled: %s" % ("true" if enabled else "false"))
        lines.append("    personas: [%s]" % slug)
        lines.append("    session: .secrets/%s.session" % slug)
        lines.append("    db: %s"
                     % DIVERGING_DBS.get(slug, ".secrets/%s.db" % slug))
        if port is not None:
            lines.append("    panel:")
            lines.append("      port: %d" % port)
    return "\n".join(lines) + "\n"


def _root_with(tmp_path: Path, clients) -> Path:
    root = tmp_path / "repo"
    (root / "chatter" / "clients").mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_REL).write_text(_registry_yaml(clients), encoding="utf-8")
    return root


# Ферма сторожа: включённый БЕЗ секции, включённый С секцией, выключенный С
# секцией, выключенный БЕЗ секции. Четыре сочетания — потому что склеены
# бывают любые два.
FARM = [("volska", True, None),
        ("yarina", True, YARINA_PORT),
        ("demo", False, DISABLED_PORT),
        ("ghost", False, None)]

ENABLED = {"volska", "yarina"}
DISABLED = {"demo", "ghost"}
NO_SECTION_ENABLED = "volska"
WITH_SECTION_ENABLED = "yarina"


def _payload_for(path: str) -> dict:
    if path == getattr(ow, "ATTENTION_PATH", "/ops/attention"):
        return {"open": 0, "stale_open": 0,
                "oldest_age_s": None, "oldest_wait_s": None}
    return {"pending": 0, "refused": 0, "oldest_age_s": None, "stuck": False}


class _Fetch:
    def __init__(self):
        self.calls = []

    def __call__(self, host, port, path):
        self.calls.append({"host": host, "port": port, "path": path})
        return 200, _payload_for(path)


def _panel():
    return {"host": HOST, "port": DECOY_PORT, "status": 200, "problem": None}


def _roster(root: Path) -> dict:
    snap = ow._roster_snapshot(root=root)
    assert not snap.get("error"), (
        "реестр не прочитан (%s) — сверять нечего" % snap.get("error"))
    return snap


def _chatter(roster):
    """Снимок chatter той же формы, что собирает `_chatter_snapshot`."""
    return {"processes": [], "beats": {}, "legacy_beat_age": 90 * 86400.0,
            "guardian_beat_age": 5.0, "guardian_lock_pid": None,
            "root": "C:/jarvis", "roster": roster}


def _cycle(root: Path):
    """Полный цикл: файл реестра -> сборщики -> `probe_all`.

    Ростер читается ОДИН раз и делится между половинами — второго чтения
    реестра в цикле появиться не должно.
    """
    roster = _roster(root)
    panel = _panel()
    fetch = _Fetch()
    att = ow._attention_snapshot(roster, panel, fetch=fetch)
    out = ow._outgoing_snapshot(roster, panel, fetch=fetch)
    probes = ow.probe_all(lambda p: 200,
                          lambda p: (100 * 2 ** 30, 0, 50 * 2 ** 30),
                          chatter_snapshot=_chatter(roster),
                          attention_snapshot=att,
                          outgoing_snapshot=out)
    return probes, fetch


def _slugs_of(probes, prefix):
    return {k[len(prefix):] for k in probes if k.startswith(prefix)}


FAMILIES = [("escalation", ATT_PREFIX), ("outgoing", OUT_PREFIX)]


# ── §6.2: секции нет -> КРАСНОЕ `no_instance` ──────────────────────────────
@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_an_enabled_slug_without_a_panel_section_gets_a_probe_at_all(tmp_path, name, prefix):
    """Первое из трёх: ключ ЕСТЬ.

    «Пробы нет» — это тишина о клиенте, у которого три непрочитанных карточки.
    Спека называет такой исход недопустимым отдельной строкой.
    """
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    assert prefix + NO_SECTION_ENABLED in probes, (
        "у включённого слага %s без секции `panel` семейство %s пробы НЕ "
        "ЗАВЕЛО вовсе. Ключи семейства: %s"
        % (NO_SECTION_ENABLED, name, sorted(_slugs_of(probes, prefix))))


@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_an_enabled_slug_without_a_panel_section_is_RED(tmp_path, name, prefix):
    """Второе из трёх: КРАСНОЕ, а не зелёное."""
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    verdict = probes[prefix + NO_SECTION_ENABLED]
    assert verdict.get("ok") is False, (
        "включённый клиент БЕЗ инстанса панели получил ЗЕЛЁНОЕ в семействе "
        "%s: %s. Зелёное здесь — это зелёное ПО ПОСТРОЕНИЮ ровно там, где "
        "лежит проблема" % (name, verdict))


@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_the_reason_is_exactly_no_instance(tmp_path, name, prefix):
    """Третье из трёх: причина названа СВОИМ словом.

    `reason` — ключ дедупа алертов и ответ на вопрос «что чинить». Слипание
    «секции нет» с `no_response` отправило бы владельца поднимать процесс,
    которого не существует.
    """
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    verdict = probes[prefix + NO_SECTION_ENABLED]
    assert verdict.get("reason") == "no_instance", (
        "семейство %s назвало причину %r вместо `no_instance`: %s"
        % (name, verdict.get("reason"), verdict))


@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_nobody_walks_to_the_network_for_a_slug_without_a_section(tmp_path, name, prefix):
    """Без секции идти НЕКУДА.

    Поход «куда-нибудь» — это поход на порт соседа: 404 или, хуже, чужой
    здоровый ответ, засчитанный как свой.
    """
    _probes, fetch = _cycle(_root_with(tmp_path, FARM))
    assert DECOY_PORT not in [c["port"] for c in fetch.calls], (
        "проба сходила на общий порт фермы (%d) за слаг без секции: %s"
        % (DECOY_PORT, fetch.calls))


@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_no_instance_does_not_swallow_the_slug_that_HAS_one(tmp_path, name, prefix):
    """Встречная половина: «всем no_instance» — тоже неверная реализация.

    Без этого сторожа `return no_instance` прошёл бы все три предыдущих.
    """
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    verdict = probes[prefix + WITH_SECTION_ENABLED]
    assert verdict.get("reason") != "no_instance", (
        "слаг %s с секцией `panel` тоже объявлен `no_instance` в семействе "
        "%s — значит секция не читается вовсе: %s"
        % (WITH_SECTION_ENABLED, name, verdict))
    assert verdict.get("ok") is True, (
        "здоровый инстанс %s не зелёный в семействе %s: %s"
        % (WITH_SECTION_ENABLED, name, verdict))


@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_the_detail_says_there_is_no_instance_in_words(tmp_path, name, prefix):
    """Владелец читает ФРАЗУ, а не имя переменной."""
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    detail = probes[prefix + NO_SECTION_ENABLED].get("detail") or ""
    assert detail.strip(), (
        "вердикт `no_instance` семейства %s пришёл без человеческого "
        "объяснения" % name)
    assert "инстанс" in detail.lower(), (
        "в `detail` семейства %s не сказано словами, что инстанса нет: %r"
        % (name, detail))


def test_the_empty_registry_section_is_not_the_same_as_a_dead_instance(tmp_path):
    """«Секции нет» и «инстанс молчит» — РАЗНЫЕ причины.

    Слить их — значит послать владельца поднимать гардиан там, где надо
    дописать две строки в реестр (и наоборот).
    """
    root = _root_with(tmp_path, FARM)
    roster = _roster(root)
    silent = _Fetch()

    def silent_fetch(host, port, path):
        silent.calls.append({"host": host, "port": port, "path": path})
        return None, None

    att = ow._attention_snapshot(roster, _panel(), fetch=silent_fetch)
    dead = ow.probe_attention(att["clients"][WITH_SECTION_ENABLED])
    absent = ow.probe_attention(att["clients"][NO_SECTION_ENABLED])
    assert dead.get("reason") == "no_response", (
        "молчащий инстанс со секцией должен быть `no_response`: %s" % (dead,))
    assert absent.get("reason") == "no_instance", (
        "слаг без секции должен быть `no_instance`: %s" % (absent,))
    assert dead.get("reason") != absent.get("reason"), (
        "«инстанс молчит» и «секции нет» слиты в одну причину — чинятся они "
        "в разных местах")


# ── §6.6: выключенный клиент пробы не имеет ВООБЩЕ ─────────────────────────
@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_a_disabled_client_has_no_probe_at_all(tmp_path, name, prefix):
    """Ни красной, ни зелёной. Считаем РАВЕНСТВОМ МНОЖЕСТВ.

    Равенство, а не «нет вот этих двух»: перебор известных имён слеп к имени,
    которого автор сторожа не угадал.
    """
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    got = _slugs_of(probes, prefix)
    assert got == ENABLED, (
        "состав семейства %s = %s, ожидались РОВНО включённые %s. Лишнее — "
        "шум о клиенте, которого никто не поднимает; недостающее — тишина о "
        "живом" % (name, sorted(got), sorted(ENABLED)))


@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_a_panel_section_does_not_resurrect_a_disabled_client(tmp_path, name, prefix):
    """🔴 Ловушка, которую вносит ИМЕННО эта арка.

    Порт переехал в реестр, и состав проб легко «съехать» на «у кого есть
    секция `panel`». Тогда выключенный `demo` — а у него секция есть — получил
    бы пробу, вечно красную по построению. Состав задаёт `enabled`, секция
    задаёт ТОЛЬКО адрес.
    """
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    got = _slugs_of(probes, prefix)
    for slug in DISABLED:
        assert slug not in got, (
            "выключенный клиент %s получил пробу семейства %s: состав проб "
            "поехал за секцией `panel` вместо `enabled`" % (slug, name))


@pytest.mark.parametrize("name,prefix", FAMILIES)
def test_the_count_of_probes_equals_the_count_of_enabled_clients(tmp_path, name, prefix):
    """СЧЁТ, а не перечисление: сколько включённых — столько ключей."""
    root = _root_with(tmp_path, FARM)
    probes, _f = _cycle(root)
    enabled_n = sum(1 for c in _roster(root)["clients"] if c.get("enabled"))
    got = _slugs_of(probes, prefix)
    assert len(got) == enabled_n, (
        "в семействе %s %d ключей на %d включённых клиентов: %s"
        % (name, len(got), enabled_n, sorted(got)))


def test_the_disabled_client_is_never_walked_to(tmp_path):
    """Выключенный клиент не только без ключа — к нему и не ходят."""
    _probes, fetch = _cycle(_root_with(tmp_path, FARM))
    assert DISABLED_PORT not in [c["port"] for c in fetch.calls], (
        "проба сходила на порт выключенного клиента (%d): %s"
        % (DISABLED_PORT, fetch.calls))


def test_the_runner_family_still_matches_the_same_enabled_set(tmp_path):
    """Границы соседа: арка не тронула состав проб РАННЕРОВ.

    Три семейства пер-клиентных проб обязаны говорить об одном и том же
    составе фермы. Разъехавшийся состав означал бы два ответа на вопрос «кто у
    нас есть».
    """
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    assert _slugs_of(probes, RUN_PREFIX) == ENABLED, (
        "состав проб раннеров разъехался с включёнными: %s против %s"
        % (sorted(_slugs_of(probes, RUN_PREFIX)), sorted(ENABLED)))


def test_all_three_client_families_agree_on_the_farm(tmp_path):
    probes, _f = _cycle(_root_with(tmp_path, FARM))
    runners = _slugs_of(probes, RUN_PREFIX)
    att = _slugs_of(probes, ATT_PREFIX)
    out = _slugs_of(probes, OUT_PREFIX)
    assert runners == att == out, (
        "семейства пер-клиентных проб знают РАЗНЫЕ фермы: раннеры %s, "
        "эскалации %s, отправка %s"
        % (sorted(runners), sorted(att), sorted(out)))


def test_a_registry_with_no_panel_sections_at_all_still_gives_red_probes(tmp_path):
    """Обратный ход §7.5: секцию убрали — лампы вернулись в `no_instance`.

    Проверяется на ВСЕЙ ферме сразу: реализация, зеленеющая при пустом наборе
    инстансов, — это и есть «погасили пробу вместо того, чтобы поднять
    панель».
    """
    root = _root_with(tmp_path, [("volska", True, None), ("yarina", True, None)])
    probes, fetch = _cycle(root)
    for prefix in (ATT_PREFIX, OUT_PREFIX):
        for slug in ("volska", "yarina"):
            v = probes[prefix + slug]
            assert v.get("reason") == "no_instance", (
                "после снятия секции `panel` у %s проба %s сказала %r вместо "
                "`no_instance`" % (slug, prefix, v.get("reason")))
    assert not fetch.calls, (
        "секций нет ни у кого, а проба всё равно куда-то сходила: %s"
        % fetch.calls)
