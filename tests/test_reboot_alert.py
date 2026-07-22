"""DEV-24: алерт «машина перезагрузилась».

Инцидент 2026-07-15: Windows Update ребутнул прод ДВАЖДЫ за три минуты
(03:14:09 + 03:16:48), погибла аудит-задача, и никто не узнал до утра.
`ops_watchdog` ловит «сервис не отвечает», но сценарий «ребутнулось и всё
поднялось» для него неотличим от нормы — простой проходит бесследно.

Ключевое требование: сигнал по СМЕНЕ ЗАГРУЗКИ, а не по порогу аптайма.
Порог пропустил бы ребут, если watchdog стартовал с задержкой; смена boot_id
не пропустит — и корректно даст ДВА алерта на двойной ребут.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ops_watchdog import (  # noqa: E402
    BOOT_KEY,
    detect_reboot,
    evaluate,
    reboot_alert_text,
)

BOOT_A = 1784478849.0          # 2026-07-15 03:14:09
BOOT_B = 1784479008.0          # 2026-07-15 03:16:48 — второй ребут, 2.5 мин спустя


def test_first_run_records_baseline_without_alerting():
    """Первый запуск не знает предыдущей загрузки — алертить не о чем."""
    fired, state = detect_reboot({}, BOOT_A, BOOT_A + 30)
    assert fired is False
    assert state[BOOT_KEY]["boot_id"] == int(BOOT_A)


def test_same_boot_is_silent():
    """Обычный тик каждые 30с не должен слать ничего."""
    _, state = detect_reboot({}, BOOT_A, BOOT_A + 30)
    for i in range(5):
        fired, state = detect_reboot(state, BOOT_A, BOOT_A + 60 + i * 30)
        assert fired is False


def test_changed_boot_fires():
    _, state = detect_reboot({}, BOOT_A, BOOT_A + 30)
    fired, state = detect_reboot(state, BOOT_B, BOOT_B + 30)
    assert fired is True
    assert state[BOOT_KEY]["boot_id"] == int(BOOT_B)


def test_double_reboot_fires_twice():
    """Регрессия на реальный инцидент: два ребута за три минуты (зазор 159с) =
    ДВА алерта. Один алерт скрыл бы, что машина ушла в перезагрузку дважды —
    а это меняет вывод (чекпойнт не спас бы, задача умерла бы на втором)."""
    _, state = detect_reboot({}, 1784478000.0, 1784478000.0 + 30)
    fired_a, state = detect_reboot(state, BOOT_A, BOOT_A + 30)
    fired_b, state = detect_reboot(state, BOOT_B, BOOT_B + 30)
    assert fired_a is True and fired_b is True


def test_fires_if_watchdog_slept_through_the_reboot():
    """Смена boot_id ловится сравнением с тем, что видели В ПРОШЛЫЙ РАЗ —
    даже если watchdog сам не работал в момент ребута. Граница: детект обязан
    случиться, пока аптайм НОВОЙ загрузки < 30 мин (sanity-guard, решение
    владельца 2026-07-22); проспал дольше — алерта не будет (см.
    test_clock_step_with_old_uptime_is_not_claimed_as_reboot)."""
    _, state = detect_reboot({}, BOOT_A, BOOT_A + 30)
    # watchdog не работал; машина перезагрузилась час спустя, тик — через
    # 10 минут после НОВОЙ загрузки
    fired, _ = detect_reboot(state, BOOT_A + 3600, BOOT_A + 3600 + 600)
    assert fired is True


def test_does_not_mutate_input_state():
    state = {BOOT_KEY: {"boot_id": int(BOOT_A)}}
    snapshot = {BOOT_KEY: {"boot_id": int(BOOT_A)}}
    detect_reboot(state, BOOT_B, BOOT_B + 30)
    assert state == snapshot


def test_survives_corrupt_boot_entry():
    """Стейт читается с диска — он может быть кривым. Падать нельзя:
    watchdog обязан продолжать следить (DEV-18 — не молча, но и не умирая)."""
    for bad in ({BOOT_KEY: None}, {BOOT_KEY: "мусор"}, {BOOT_KEY: {}}, {BOOT_KEY: []}):
        fired, state = detect_reboot(bad, BOOT_A, BOOT_A + 30)
        assert fired is False                       # база, а не ложный алерт
        assert state[BOOT_KEY]["boot_id"] == int(BOOT_A)


def test_string_boot_id_from_json_still_compares():
    """JSON мог сохранить число строкой — сравнение обязано выжить."""
    fired, _ = detect_reboot({BOOT_KEY: {"boot_id": str(int(BOOT_A))}},
                             BOOT_A, BOOT_A + 30)
    assert fired is False


# --- текст алерта ----------------------------------------------------------

def _fake_localtime(ts):
    return time.struct_time((2026, 7, 15, 3, 14, 9, 2, 196, 0))


def test_alert_text_names_time_and_uptime():
    text = reboot_alert_text(BOOT_A, BOOT_A + 42, localtime=_fake_localtime)
    assert "03:14" in text
    assert "42" in text
    assert "перезагруз" in text.lower()


def test_alert_text_never_negative_uptime():
    """Проверяем ИМЕННО поле аптайма, а не весь хвост строки: в тексте есть
    легитимные дефисы («что-то»), и грубый поиск '-' ловил их."""
    text = reboot_alert_text(BOOT_A, BOOT_A - 10, localtime=_fake_localtime)
    uptime_field = text.split("аптайм")[1].split(")")[0]
    assert "-" not in uptime_field
    assert "0 с" in uptime_field


# --- совместимость с существующим evaluate ---------------------------------

def test_evaluate_preserves_boot_key():
    """_boot живёт в том же файле состояния, что и проверки. evaluate обязан
    протаскивать его нетронутым, иначе следующий тик решит, что это первый
    запуск, и ребут пройдёт молча."""
    prev = {BOOT_KEY: {"boot_id": 123}, "health": {"fail": 0, "alerted": False}}
    alerts, new_state = evaluate(prev, {"health": {"ok": True, "detail": ""}})
    assert new_state[BOOT_KEY] == {"boot_id": 123}
    assert alerts == []


def test_evaluate_does_not_alert_on_boot_key():
    """_boot — не проверка. Он не должен попасть в probes и породить 🚨."""
    prev = {BOOT_KEY: {"boot_id": 123}}
    alerts, _ = evaluate(prev, {})
    assert alerts == []


def test_uptime_is_human_readable():
    """«аптайм 48323 с» в 3 часа ночи разбирать некогда."""
    from ops_watchdog import humanize_uptime
    assert humanize_uptime(42) == "42 с"
    assert humanize_uptime(600) == "10 мин"
    assert humanize_uptime(48323) == "13 ч"
    assert humanize_uptime(-5) == "0 с"


def test_alert_text_uses_human_uptime_for_long_gaps():
    text = reboot_alert_text(BOOT_A, BOOT_A + 48323, localtime=_fake_localtime)
    assert "13 ч" in text
    assert "48323" not in text


# ---------------------------------------------------------------------------
# DEV-24 (уточнённый): ДЕДУПЛИКАЦИЯ, а не новый мониторинг.
#
# Поправка владельца 2026-07-19: watchdog на утренний ребут СРАБОТАЛ — пришли
# 🚨 по backend/раннеру/cloudflared в 07:01-07:03, потом ✅ на каждый. То есть
# одно событие давало до ШЕСТИ сообщений, и ни одно не называло причину.
# Проблема не в отсутствии сигнала, а в том, что сигнал не собирался в смысл.
#
# Поведение: в загрузочном окне сервисы ещё поднимаются — их падения ОЖИДАЕМЫ
# и подавляются, вместо них одно понятное «машина перезагрузилась». Но если
# сервис не встал К КОНЦУ окна — это уже настоящая авария, и она обязана
# прозвучать.
# ---------------------------------------------------------------------------
from ops_watchdog import BOOT_GRACE_S, within_boot_grace  # noqa: E402

_DOWN = {"ok": False, "detail": "нет ответа"}
_UP = {"ok": True, "detail": ""}


def test_boot_grace_window_boundaries():
    assert within_boot_grace(BOOT_A, BOOT_A + 1) is True
    assert within_boot_grace(BOOT_A, BOOT_A + BOOT_GRACE_S - 1) is True
    assert within_boot_grace(BOOT_A, BOOT_A + BOOT_GRACE_S + 1) is False


def test_suppressed_down_emits_nothing_but_keeps_counting():
    """Падения в загрузочном окне не шумят, но СЧИТАЮТСЯ — иначе после окна
    пришлось бы заново набирать debounce и настоящая авария опоздала бы."""
    alerts, state = evaluate({}, {"backend": _DOWN}, suppress_down=True)
    assert alerts == []
    alerts, state = evaluate(state, {"backend": _DOWN}, suppress_down=True)
    assert alerts == []
    assert state["backend"]["fail"] == 2
    assert state["backend"]["alerted"] is False       # не «уже сообщили»


def test_service_still_down_after_grace_does_alert():
    """Главная страховка: дедупликация не смеет проглотить сервис, который
    после ребута ТАК И НЕ ПОДНЯЛСЯ."""
    _, state = evaluate({}, {"backend": _DOWN}, suppress_down=True)
    _, state = evaluate(state, {"backend": _DOWN}, suppress_down=True)
    alerts, state = evaluate(state, {"backend": _DOWN}, suppress_down=False)
    assert len(alerts) == 1 and "backend" in alerts[0].lower() or "бэкенд" in alerts[0].lower()
    assert state["backend"]["alerted"] is True


def test_no_spurious_recovery_after_suppressed_down():
    """Подавили 🚨 — значит и ✅ слать не о чем. Иначе владелец получит
    «Восстановлено» о том, о чём ему не сообщали."""
    _, state = evaluate({}, {"backend": _DOWN}, suppress_down=True)
    _, state = evaluate(state, {"backend": _DOWN}, suppress_down=True)
    alerts, _ = evaluate(state, {"backend": _UP}, suppress_down=False)
    assert alerts == []


def test_recovery_from_pre_reboot_outage_still_reported():
    """Если сервис лежал и об этом УЖЕ сообщили до ребута — ✅ обязан прийти."""
    prev = {"backend": {"fail": 3, "alerted": True}}
    alerts, _ = evaluate(prev, {"backend": _UP}, suppress_down=True)
    assert len(alerts) == 1 and "✅" in alerts[0]


def test_suppression_does_not_touch_healthy_checks():
    alerts, state = evaluate({}, {"disk": _UP}, suppress_down=True)
    assert alerts == [] and state["disk"]["fail"] == 0


def test_reboot_alert_explains_the_silence():
    """Владелец должен понимать, ПОЧЕМУ вместо привычных 🚨 тишина."""
    text = reboot_alert_text(BOOT_A, BOOT_A + 5, localtime=_fake_localtime)
    low = text.lower()
    assert "поднима" in low or "подним" in low
    assert "мин" in low                      # назван размер окна тишины


# ---------------------------------------------------------------------------
# Инцидент 2026-07-21/22: ЛОЖНЫЙ «🔄 перезагрузилась в 07:00 (аптайм 74 ч)» —
# два дня подряд, 7-11 повторов за заход, реального ребута НЕ БЫЛО
# (LastBootUpTime = 2026-07-19 07:00:56, аптайм сходится).
#
# Корень: boot_id = int(psutil.boot_time()), а psutil.boot_time() на Windows =
# time.time() - GetTickCount64()/1000. Тик-каунтер не получает NTP-коррекций,
# оценка момента загрузки ДРЕЙФУЕТ на ~секунду в сутки. Раз в сутки дробная
# часть переползает целочисленную границу, int() флипается → «смена загрузки».
# Пока дрейф в зоне шума замера (±мс) вокруг границы, каждый 30с-тик флипает
# значение туда-сюда → 7-11 алертов об одном несуществующем событии.
#
# Фикс (два независимых предохранителя + дедуп):
#   1) смена boot_id засчитывается только БОЛЬШЕ допуска на дрейф
#      (BOOT_JITTER_TOLERANCE_S); реальный ребут двигает boot_time минимум на
#      прежний аптайм — на порядки больше;
#   2) sanity-guard: не заявлять ребут при аптайме > 30 мин (реальный ребут
#      watchdog видит на первом же 30с-тике). Подавление НЕ молчит (DEV-18);
#   3) once-per-boot-id: об одной загрузке алертим один раз, ключ в стейте.
# ---------------------------------------------------------------------------
from ops_watchdog import (  # noqa: E402
    BOOT_JITTER_TOLERANCE_S,
    REBOOT_CLAIM_MAX_UPTIME_S,
)

_74H = 74 * 3600.0


def test_boot_time_jitter_is_not_a_reboot():
    """Регрессия на инцидент: int(boot_time) уполз на 1с при аптайме 74ч."""
    _, state = detect_reboot({}, BOOT_A, BOOT_A + _74H)
    fired, _ = detect_reboot(state, BOOT_A + 1.2, BOOT_A + _74H + 30)
    assert fired is False


def test_jitter_oscillation_never_fires():
    """7-11 повторов давал флип int() туда-сюда на каждом 30с-тике —
    ни один из них не смеет породить алерт."""
    now = BOOT_A + _74H
    _, state = detect_reboot({}, BOOT_A, now)
    for i in range(11):
        now += 30
        wobble = BOOT_A + (1.0 if i % 2 else 0.0)   # ...52 <-> ...53
        fired, state = detect_reboot(state, wobble, now)
        assert fired is False, f"тик {i}: дрожание boot_time дало ложный ребут"


def test_real_reboot_still_fires_through_both_guards():
    """Предохранители не смеют съесть НАСТОЯЩИЙ ребут: boot_time прыгает
    на десятки часов вперёд, аптайм на первом тике — секунды."""
    _, state = detect_reboot({}, BOOT_A, BOOT_A + _74H)
    new_boot = BOOT_A + _74H + 120                  # машина встала 2 мин как
    fired, state = detect_reboot(state, new_boot, new_boot + 40)
    assert fired is True
    assert state[BOOT_KEY]["boot_id"] == int(new_boot)


def test_clock_step_with_old_uptime_is_not_claimed_as_reboot():
    """NTP-степ на минуты двигает boot_id за допуск дрейфа, но аптайм всё ещё
    74ч — заявлять «перезагрузилась в 07:00 (аптайм 74 ч)» абсурдно и было
    ровно симптомом инцидента. Sanity-guard: аптайм > 30 мин = не ребут."""
    _, state = detect_reboot({}, BOOT_A, BOOT_A + _74H)
    shifted = BOOT_A + BOOT_JITTER_TOLERANCE_S + 300   # сдвиг часов, не ребут
    fired, state = detect_reboot(state, shifted, shifted + _74H)
    assert fired is False
    assert state[BOOT_KEY]["boot_id"] == int(shifted)  # базу приняли, шум не копим


def test_guard_suppression_is_not_silent():
    """DEV-18: подавили сигнал — скажи об этом. warn-канал обязан получить
    объяснение, иначе следующий разбор опять начнётся с гадания."""
    calls = []
    _, state = detect_reboot({}, BOOT_A, BOOT_A + _74H)
    shifted = BOOT_A + BOOT_JITTER_TOLERANCE_S + 300
    detect_reboot(state, shifted, shifted + _74H, warn=calls.append)
    assert len(calls) == 1
    assert "аптайм" in calls[0].lower()


def test_once_per_boot_id_no_repeat_alert():
    """Дедуп: об одной загрузке — ОДИН пуш. Если о boot_id уже алертили
    (ключ в стейте), повторная «смена» на него же молчит."""
    _, state = detect_reboot({}, BOOT_A, BOOT_A + 3600)
    new_boot = BOOT_A + 3600 + 60
    fired, state = detect_reboot(state, new_boot, new_boot + 40)
    assert fired is True
    # стейт частично откатился (флип boot_id назад), alerted-ключ уцелел
    stale = {BOOT_KEY: {"boot_id": int(BOOT_A),
                        "alerted_boot_id": state[BOOT_KEY]["alerted_boot_id"]}}
    fired, _ = detect_reboot(stale, new_boot, new_boot + 100)
    assert fired is False


def test_alerted_boot_id_survives_quiet_ticks():
    """Ключ дедупа не смеет стираться обычными тиками — иначе «once» живёт
    30 секунд."""
    _, state = detect_reboot({}, BOOT_A, BOOT_A + 3600)
    new_boot = BOOT_A + 3600 + 60
    fired, state = detect_reboot(state, new_boot, new_boot + 40)
    assert fired is True
    for k in range(3):
        fired, state = detect_reboot(state, new_boot, new_boot + 70 + k * 30)
        assert fired is False
    assert state[BOOT_KEY]["alerted_boot_id"] == int(new_boot)


def test_jitter_tolerance_below_real_double_reboot_gap():
    """Допуск дрейфа обязан быть меньше реального межребутного зазора 159с —
    иначе второй ребут инцидента 15.07 был бы съеден как «дрожание»."""
    assert 0 < BOOT_JITTER_TOLERANCE_S < (BOOT_B - BOOT_A)
    assert REBOOT_CLAIM_MAX_UPTIME_S == 30 * 60


def test_corrupt_alerted_key_does_not_crash_or_fire():
    """Кривой стейт с диска: мусорный alerted_boot_id не роняет и не фолсит."""
    bad = {BOOT_KEY: {"boot_id": int(BOOT_A), "alerted_boot_id": "мусор"}}
    fired, state = detect_reboot(bad, BOOT_A, BOOT_A + 60)
    assert fired is False
    assert state[BOOT_KEY]["boot_id"] == int(BOOT_A)
