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
    fired, state = detect_reboot({}, BOOT_A)
    assert fired is False
    assert state[BOOT_KEY]["boot_id"] == int(BOOT_A)


def test_same_boot_is_silent():
    """Обычный тик каждые 30с не должен слать ничего."""
    _, state = detect_reboot({}, BOOT_A)
    for _ in range(5):
        fired, state = detect_reboot(state, BOOT_A)
        assert fired is False


def test_changed_boot_fires():
    _, state = detect_reboot({}, BOOT_A)
    fired, state = detect_reboot(state, BOOT_B)
    assert fired is True
    assert state[BOOT_KEY]["boot_id"] == int(BOOT_B)


def test_double_reboot_fires_twice():
    """Регрессия на реальный инцидент: два ребута за три минуты = ДВА алерта.
    Один алерт скрыл бы, что машина ушла в перезагрузку дважды — а это меняет
    вывод (чекпойнт не спас бы, задача умерла бы на втором)."""
    _, state = detect_reboot({}, 1784478000.0)      # базовая линия
    fired_a, state = detect_reboot(state, BOOT_A)
    fired_b, state = detect_reboot(state, BOOT_B)
    assert fired_a is True and fired_b is True


def test_fires_even_if_watchdog_started_late():
    """Порог аптайма пропустил бы ребут, случившийся до старта watchdog.
    Смена boot_id — нет: сравниваем с тем, что видели В ПРОШЛЫЙ РАЗ."""
    _, state = detect_reboot({}, BOOT_A)
    # watchdog не работал час; за это время машина перезагрузилась
    fired, _ = detect_reboot(state, BOOT_A + 3600)
    assert fired is True


def test_does_not_mutate_input_state():
    state = {BOOT_KEY: {"boot_id": int(BOOT_A)}}
    snapshot = {BOOT_KEY: {"boot_id": int(BOOT_A)}}
    detect_reboot(state, BOOT_B)
    assert state == snapshot


def test_survives_corrupt_boot_entry():
    """Стейт читается с диска — он может быть кривым. Падать нельзя:
    watchdog обязан продолжать следить (DEV-18 — не молча, но и не умирая)."""
    for bad in ({BOOT_KEY: None}, {BOOT_KEY: "мусор"}, {BOOT_KEY: {}}, {BOOT_KEY: []}):
        fired, state = detect_reboot(bad, BOOT_A)
        assert fired is False                       # база, а не ложный алерт
        assert state[BOOT_KEY]["boot_id"] == int(BOOT_A)


def test_string_boot_id_from_json_still_compares():
    """JSON мог сохранить число строкой — сравнение обязано выжить."""
    fired, _ = detect_reboot({BOOT_KEY: {"boot_id": str(int(BOOT_A))}}, BOOT_A)
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
    text = reboot_alert_text(BOOT_A, BOOT_A - 10, localtime=_fake_localtime)
    assert "-" not in text.split("аптайм")[1]


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
