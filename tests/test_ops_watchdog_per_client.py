# -*- coding: utf-8 -*-
"""ПЕР-КЛИЕНТНЫЕ пробы раннеров chatter — половина «наблюдение».

Спека `docs/superpowers/specs/2026-08-22-per-client-watchdog-probe.md`,
одобрена владельцем 22.08 вместе с тремя ответами (§4.2, §7 п. 4, §10).

Дыра, которую закрываем: `chatter_beat_age` берёт САМУЮ СВЕЖУЮ отметку, а
процессы проверяются как «есть ли хоть один», — живой сосед перекрывает
мёртвого ПО ОПРЕДЕЛЕНИЮ. На двух клиентах это стоит разбора, на шести —
гарантированного тихого простоя.

Здесь пиннится только НАБЛЮДЕНИЕ: ростер, пер-слаг пробы, легаси-проба,
чистка ключей. Доставка (подавление под красным гардианом и склейка алертов)
режет `evaluate`/`_transitions` и по решению владельца идёт ОТДЕЛЬНЫМ заходом
с мутационным гейтом на ядро — её сторожа сюда не входят намеренно.

Сторожа писались ОТ СПЕКИ и ДО реализации. Разделения авторов, которое
принято в этой репе, здесь не было (одна сессия, агентов не звали) — это
названо в коммите, а не спрятано.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_pc",
    Path(__file__).resolve().parents[1] / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ow)

ROOT = "C:/jarvis"


def _cmd(slug, root=r"C:\jarvis"):
    return (root + r"\.venv\Scripts\python.exe -u -m chatter.telethon_run "
            "--llm real --personas %s --session .secrets/%s.session "
            "--db .secrets/%s.db --client %s" % (slug, slug, slug, slug))


def _proc(pid, name, cmdline):
    return {"pid": pid, "name": name, "cmdline": cmdline}


REGISTRY_TWO = """
clients:
  volska:
    enabled: true
    personas: [volska]
  yarina:
    enabled: true
    personas: [yarina]
  demo:
    enabled: false
    personas: [demo]
"""


# ── §3. Ростер: читаем реестр, а не гадаем ──────────────────────────────────


def test_roster_names_enabled_and_disabled_clients():
    roster = ow.read_roster(REGISTRY_TWO)
    by_slug = {e["slug"]: e["enabled"] for e in roster}
    assert by_slug == {"volska": True, "yarina": True, "demo": False}


def test_a_client_without_enabled_is_not_assumed_running():
    """Умолчание `enabled` — False, как у боевого парсера реестра.

    Обратное умолчание дало бы алерт о клиенте, которого никто не поднимает.
    """
    roster = ow.read_roster("clients:\n  ghost:\n    personas: [ghost]\n")
    # `panel_port` добавлен 27.08: порт клиентской панели живёт в реестре, и
    # его отсутствие — законное состояние (панели у клиента нет). Пин
    # РАВЕНСТВОМ оставлен намеренно: он ловит появление нового поля, и сегодня
    # он это и поймал — ровно то, чего от него ждут.
    assert roster == [{"slug": "ghost", "enabled": False, "panel_port": None}]


def test_a_broken_registry_raises_instead_of_reading_as_empty():
    """«Реестр не прочитан» и «клиентов нет» — РАЗНОЕ (§6)."""
    # ValueError, а не голый Exception: `pytest.raises(Exception)` прошёл бы и
    # на ОТСУТСТВУЮЩЕЙ функции (AttributeError), то есть был бы зелёным по
    # построению до всякой реализации.
    with pytest.raises(ValueError):
        ow.read_roster("clients: [это не словарь]")


def test_roster_probe_is_red_when_the_registry_is_unreadable():
    p = ow.probe_roster({"error": "yaml: боль"})
    assert p["ok"] is False
    assert p["reason"] == "roster_unreadable"


def test_roster_probe_is_green_when_nobody_is_enabled():
    """Выключенная ферма — законное состояние, а не авария."""
    p = ow.probe_roster({"clients": [{"slug": "demo", "enabled": False}]})
    assert p["ok"] is True


# ── §5. Живость КЛИЕНТА: процесс И отметка, оба по слагу ────────────────────


def test_a_live_client_with_fresh_beat_is_ok():
    p = ow.probe_client_runner([_proc(111, "python.exe", _cmd("volska"))],
                               slug="volska", beat_age=12.0, root=ROOT)
    assert p["ok"] is True


def test_the_death_of_one_client_is_seen_while_the_neighbour_lives():
    """ГЛАВНЫЙ сторож этой работы: сегодня этот случай не ловится вовсе."""
    procs = [_proc(111, "python.exe", _cmd("volska"))]
    live = ow.probe_client_runner(procs, slug="volska", beat_age=5.0, root=ROOT)
    dead = ow.probe_client_runner(procs, slug="yarina", beat_age=5.0, root=ROOT)

    assert live["ok"] is True, "живой клиент покраснел"
    assert dead["ok"] is False, "смерть соседа не видна — ровно дефект спеки"
    assert dead["reason"] == "no_process"


def test_a_frozen_client_is_not_health():
    """Процесс жив, отметка протухла — зависший раннер."""
    p = ow.probe_client_runner([_proc(111, "python.exe", _cmd("yarina"))],
                               slug="yarina",
                               beat_age=ow.CHATTER_BEAT_MAX_AGE_S + 1,
                               root=ROOT)
    assert p["ok"] is False and p["reason"] == "stale_heartbeat"


def test_a_missing_beat_is_its_own_reason():
    p = ow.probe_client_runner([_proc(111, "python.exe", _cmd("yarina"))],
                               slug="yarina", beat_age=None, root=ROOT)
    assert p["ok"] is False and p["reason"] == "no_heartbeat"


def test_reasons_carry_no_seconds():
    """Секунды в причине означали бы алерт раз в 30 секунд (дедуп по причине)."""
    p = ow.probe_client_runner([_proc(111, "python.exe", _cmd("yarina"))],
                               slug="yarina", beat_age=9999.0, root=ROOT)
    assert "9999" not in p["reason"]


# ── §7. Ловушки ────────────────────────────────────────────────────────────


def test_a_slug_is_matched_as_a_token_not_a_substring():
    """`volska2` не делает `volska` живым (и наоборот)."""
    procs = [_proc(111, "python.exe", _cmd("volska2"))]
    p = ow.probe_client_runner(procs, slug="volska", beat_age=5.0, root=ROOT)
    assert p["ok"] is False, "клиент ожил за счёт соседа с длинным именем"


def test_the_equals_form_of_the_flag_counts():
    procs = [_proc(111, "python.exe",
                   _cmd("volska").replace("--client volska", "--client=volska"))]
    p = ow.probe_client_runner(procs, slug="volska", beat_age=5.0, root=ROOT)
    assert p["ok"] is True


def test_a_runner_from_a_worktree_is_not_production():
    """`c:/jarvis` как подстрока сидит внутри `c:/jarvis_worktrees/...`."""
    procs = [_proc(111, "python.exe", _cmd("volska", root=r"C:\jarvis_worktrees\x"))]
    p = ow.probe_client_runner(procs, slug="volska", beat_age=5.0, root=ROOT)
    assert p["ok"] is False


def test_a_non_python_process_does_not_count():
    """Строка-маркер попадает в командную строку того, кто ищет (29.07)."""
    procs = [_proc(111, "powershell.exe", _cmd("volska"))]
    p = ow.probe_client_runner(procs, slug="volska", beat_age=5.0, root=ROOT)
    assert p["ok"] is False


# ── §7 п. 4. Легаси-отметка: три исхода ────────────────────────────────────


def test_a_missing_legacy_beat_is_green():
    assert ow.probe_beat_legacy(None)["ok"] is True


def test_a_stale_legacy_beat_is_green_because_that_is_todays_norm():
    """Вечная красная лампа на сироте стала бы фоном."""
    p = ow.probe_beat_legacy(90 * 86400.0)
    assert p["ok"] is True


def test_a_FRESH_legacy_beat_is_the_news():
    """Кто-то ПИШЕТ легаси-отметку — значит есть раннер вне разметки."""
    p = ow.probe_beat_legacy(5.0)
    assert p["ok"] is False
    assert p["reason"] == "legacy_beat_alive"


# ── §4/§6. Состав цикла ────────────────────────────────────────────────────


def _snapshot(**over):
    snap = {
        "processes": [_proc(111, "python.exe", _cmd("volska"))],
        "beats": {"volska": 5.0, "yarina": 5.0},
        "legacy_beat_age": 90 * 86400.0,
        "guardian_beat_age": 5.0,
        "guardian_lock_pid": None,
        "root": ROOT,
        "roster": {"clients": [{"slug": "volska", "enabled": True},
                               {"slug": "yarina", "enabled": True},
                               {"slug": "demo", "enabled": False}]},
    }
    snap.update(over)
    return snap


def _probes(snap):
    return ow.probe_all(lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30),
                        chatter_snapshot=snap)


def test_probe_all_gives_one_probe_per_enabled_client():
    probes = _probes(_snapshot())
    assert "chatter_runner:volska" in probes
    assert "chatter_runner:yarina" in probes
    assert "chatter_runner:demo" not in probes, "выключенный клиент не пробится"


def test_probe_all_drops_the_aggregate_key_once_the_roster_is_known():
    """Общий ключ = один `alerted` на всю ферму (§4). Его быть не должно."""
    assert "chatter_runner" not in _probes(_snapshot())


def test_the_neighbour_stays_green_in_the_full_cycle():
    probes = _probes(_snapshot())
    assert probes["chatter_runner:volska"]["ok"] is True
    assert probes["chatter_runner:yarina"]["ok"] is False


def test_an_unreadable_roster_gives_no_client_probes_at_all():
    probes = _probes(_snapshot(roster={"error": "битый yaml"}))
    assert probes["chatter_roster"]["ok"] is False
    assert not [k for k in probes if k.startswith("chatter_runner:")]


def test_a_snapshot_without_a_roster_keeps_the_old_behaviour():
    """Старое окружение не имеет права начать слать DOWN о том, чего не мерил."""
    snap = _snapshot()
    snap.pop("roster")
    snap["runner_beat_age"] = 5.0
    probes = _probes(snap)
    assert "chatter_runner" in probes
    assert not [k for k in probes if k.startswith("chatter_runner:")]


def test_the_legacy_probe_joins_the_cycle():
    assert _probes(_snapshot())["chatter_beat_legacy"]["ok"] is True


# ── §7 п. 6. Чистка ключей ─────────────────────────────────────────────────


def test_keys_of_slugs_gone_from_the_roster_are_pruned():
    state = {"chatter_runner:volska": {"fail": 0, "alerted": False},
             "chatter_runner:olga": {"fail": 3, "alerted": True},
             "backend": {"fail": 0, "alerted": False}}
    probes = _probes(_snapshot())

    pruned = ow.prune_client_state(state, probes)

    assert "chatter_runner:olga" not in pruned, "мёртвая запись однажды прочтётся как чья-то"
    assert "chatter_runner:volska" in pruned
    assert "backend" in pruned, "вычищено лишнее"


def test_the_legacy_aggregate_key_is_migrated_away():
    state = {"chatter_runner": {"fail": 0, "alerted": True}}
    pruned = ow.prune_client_state(state, _probes(_snapshot()))
    assert "chatter_runner" not in pruned


def test_nothing_is_pruned_while_the_roster_is_unknown():
    """Иначе секундный сбой чтения реестра вымывает `alerted` у всех.

    Красный `chatter_roster` — это «мы не знаем состава», и состояние в этот
    момент трогать нельзя.
    """
    state = {"chatter_runner:volska": {"fail": 2, "alerted": True}}
    probes = _probes(_snapshot(roster={"error": "битый yaml"}))

    assert ow.prune_client_state(state, probes) == state


# ── ДОБАВЛЕНО ПРИ ПЕРЕСБОРКЕ ПОД КНОПКУ (24.08) ────────────────────────────
# Два изъяна, найденных владельцем на разборе ветки. Оба про одно: громкий
# отказ не должен стоять рядом с успокаивающей лампой, а сам отказ обязан
# читаться словами. Сторожа писал автор кода — разделения не было, названо
# в коммите.


def test_an_unreadable_roster_leaves_no_soothing_aggregate_lamp():
    """Нечитаемый ростер: КРАСНОЕ и НИ ОДНОЙ зелёной лампы про раннеров.

    Сторож на СОСЕДА красной пробы, а не на неё саму. Существующий
    `test_an_unreadable_roster_gives_no_client_probes_at_all` пинит отсутствие
    пер-слаговых ключей — но НЕ отсутствие агрегатного `chatter_runner`.
    Разница не теоретическая: агрегат зелен ровно тогда, когда жив ХОТЬ ОДИН
    раннер, то есть при нечитаемом реестре он сказал бы «раннеры в порядке»
    рядом с «состава не знаем». Это и есть худший вид тишины: владелец видит
    зелёное и не идёт смотреть.

    Сегодня ветка `else` агрегат не выставляет по построению. Сторож стоит
    затем, чтобы возврат агрегата «как запасного варианта при сбое реестра»
    не прошёл молча — остальные сторожа при такой правке остались бы зелёными.
    """
    probes = _probes(_snapshot(roster={"error": "битый yaml"}))
    assert probes["chatter_roster"]["ok"] is False
    assert "chatter_runner" not in probes, "агрегат = успокаивающая лампа"
    assert not [k for k in probes if k.startswith("chatter_runner:")]


def test_the_roster_snapshot_never_answers_none(tmp_path):
    """`None` от снимка = тихий откат в ЛЕГАСИ-ветку, где агрегат живёт.

    `probe_all` различает «ростера нет в снимке» (старое окружение, агрегат
    остаётся) и «ростер не прочитан» (красная проба). Первое определяется
    ключом `roster is None`. Значит `_roster_snapshot`, вернув `None` хоть
    однажды, МОЛЧА вернула бы успокаивающую лампу вместо отказа — и все
    сторожа выше остались бы зелёными, потому что каждый из них подаёт снимок
    руками.

    Три способа не прочитать реестр, все обязаны дать `error`, а не `None`.
    """
    # 1. файла нет
    snap = ow._roster_snapshot(root=tmp_path)
    assert snap is not None and "error" in snap, "нет файла -> error"

    # 2. YAML битый
    reg = tmp_path / "chatter" / "clients"
    reg.mkdir(parents=True)
    (reg / "registry.yaml").write_text("clients: [не словарь]", encoding="utf-8")
    snap = ow._roster_snapshot(root=tmp_path)
    assert snap is not None and "error" in snap, "битый yaml -> error"

    # 3. корректный реестр по-прежнему даёт clients, а не error
    (reg / "registry.yaml").write_text(
        "clients:\n  volska:\n    enabled: true\n", encoding="utf-8")
    snap = ow._roster_snapshot(root=tmp_path)
    assert snap is not None and "clients" in snap and "error" not in snap
