"""DEV-26: мутационный гейт обязан отдавать каждой мутации СВОЙ байткод.

Python признаёт `.pyc` актуальным по паре (mtime в ЦЕЛЫХ секундах, размер
исходника). Две мутации одного размера, записанные в одну секунду, для него
неотличимы — вторая исполняется байткодом первой. Гейт при этом печатает
бодрое `[ok]`, ничего не проверив: сторож «покраснел» на чужом коде.

Дыру нашли в платежах, и она вернулась в `mutate_ops_watchdog.py` — там все
семнадцать мутаций бьют в ОДИН файл, то есть окно совпадения максимально
широкое. Поэтому сторож здесь не на конкретный скрипт, а на все сразу: следующий
гейт напишут копипастой, и без проверки дыра приедет вместе с ней.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATES = sorted((ROOT / "scripts").glob("mutate_*.py"))


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_gate_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_there_are_gates_to_check():
    """Сторож обязан кого-то охранять: переименуют папку — молча пройдёт."""
    assert len(GATES) >= 5, f"мутационных гейтов не найдено: {GATES}"


@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.stem)
def test_gate_stamps_a_unique_mtime_on_every_write(gate, tmp_path):
    """Два ОДИНАКОВЫХ ПО РАЗМЕРУ файла подряд обязаны получить разные mtime.

    Проверяем поведение, а не наличие строки `os.utime`: подпись можно оставить
    на месте и передать ей одно и то же значение.
    """
    mod = _load(gate)
    writer = getattr(mod, "write_mutant", None)
    assert writer is not None, (
        f"{gate.name}: пишет мутанта голым write_text — байткод предыдущей "
        f"мутации переживёт запись, и гейт скажет [ok], ничего не проверив")

    target = tmp_path / "m.py"
    writer(target, "A = 1")
    first = target.stat().st_mtime
    writer(target, "A = 2")          # ровно тот же размер
    second = target.stat().st_mtime
    assert first != second, (
        f"{gate.name}: две записи одного размера получили один и тот же mtime "
        f"({first}) — вторая мутация исполнится байткодом первой")


@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.stem)
def test_gate_writes_only_through_the_stamping_helper(gate):
    """Голый `write_text` в теле гейта — это обход подписи мимо помощника.

    Ищем по исходнику: помощник может существовать и не использоваться, и
    прошлый раз дыра выглядела именно так.
    """
    src = gate.read_text(encoding="utf-8")
    body = src.split("def write_mutant", 1)
    outside = body[1].split("\n\n\n", 1)[1] if len(body) > 1 else src
    stray = [ln.strip() for ln in outside.splitlines()
             if re.search(r"\.write_text\(", ln)]
    assert not stray, (
        f"{gate.name}: запись мутанта мимо write_mutant — {stray}")
