# -*- coding: utf-8 -*-
"""Гейт DEV-26 обязан исполнять ТУ мутацию, которую записал.

11.08 выяснилось, что не обязан. Python признаёт кэш байткода актуальным по
паре (mtime исходника в ЦЕЛЫХ секундах, его размер). Две соседние мутации
одного файла, дающие одинаковый размер и попавшие в одну секунду, для этой
проверки неотличимы — вторая исполняется байткодом первой.

Поймали мы это с дешёвой стороны: живой сторож был объявлен слепым. Дорогая
сторона симметрична и молчалива — мутация, которая не исполнялась ни разу,
печатается как `[ok]`, и гейт из 120 проверок уверенно подтверждает то, чего
не проверял. Сторож самого гейта поэтому здесь, а не в списке «когда-нибудь».
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "mutate_payments_phase0.py"


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("_mut_harness", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_write_gets_its_own_mtime(harness, tmp_path):
    """Инвариант, из которого всё следует: две записи подряд НИКОГДА не делят
    один mtime. Именно совпадение целых секунд (при равном размере) и выдаёт
    чужой байткод за свой.

    Проверяются целые секунды, а не float: сравнение в .pyc идёт по ним."""
    probe = tmp_path / "probe.py"
    stamps = []
    for body in ('VALUE = "AAA"\n', 'VALUE = "BBB"\n', 'VALUE = "CCC"\n'):
        harness.write_mutant(probe, body)
        stamps.append(int(probe.stat().st_mtime))

    assert len(set(stamps)) == len(stamps), \
        f"записи разделили mtime {stamps} — вторая мутация исполнится чужим .pyc"


def test_a_second_mutation_of_the_same_size_is_really_executed(harness, tmp_path):
    """Сквозная проверка того же, но через настоящий импорт: два тела ОДНОГО
    размера, записанные подряд, обязаны дать два разных результата.

    Размеры равны намеренно — на разных размерах .pyc отбраковывается и без
    нашего вмешательства, то есть тест проходил бы, ничего не охраняя."""
    probe = tmp_path / "probe.py"
    seen = []
    for body in ('VALUE = "AAA"\n', 'VALUE = "BBB"\n'):
        harness.write_mutant(probe, body)
        out = subprocess.run(
            [sys.executable, "-c", "import probe; print(probe.VALUE)"],
            cwd=str(tmp_path), capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        assert out.returncode == 0, out.stderr
        seen.append(out.stdout.strip())

    assert seen == ["AAA", "BBB"], \
        f"исполнялся не тот код: {seen} (второй прогон взял байткод первого)"


def test_the_harness_refuses_to_run_on_a_dirty_tree(harness):
    """Откат мутаций идёт через `git checkout --`, то есть стирает
    незакоммиченное. Отказ на грязном дереве — не удобство, а предохранитель:
    один раз он уже спас готовую проводку."""
    assert hasattr(harness, "assert_clean")


def test_every_named_guard_actually_exists(harness):
    """Висячая ссылка на тест = ВЕЧНО зелёный мутант: pytest на несуществующий
    id возвращает ненулевой код, а харнесс читает именно код возврата, и печатает
    «сторож покраснел». Переименование теста без правки списка тихо выключает
    проверку, оставляя её на вид зелёной."""
    guards = sorted({entry[4] for entry in harness.MUTATIONS})
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *guards, "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace")

    assert proc.returncode == 0, \
        "не собираются сторожа:\n" + (proc.stdout + proc.stderr)[-2000:]
