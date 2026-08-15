"""DEV-31: гейт не мутирует дерево, из которого поднимается прод.

Приёмка DEV-31 из бэклога, дословно:

1. Ветка в работе не может уехать в прод молча — либо её физически нет в живом
   дереве, либо подъём называет ветку вслух.
2. Процедура создания worktree копирует gitignored-состав, и `pytest tests/` в
   свежем worktree даёт тот же baseline, что и в живом дереве.
3. Сторож на само правило, а не запись в памяти.

Четвёртая сторона DEV-31 найдена 15.08 в журнале самой панели: проба
`worktree` записала прод-дерево с МУТАНТОМ внутри (`dirty: app/routers/
jarvis_panel.py`, `dirty: app/services/jarvis_farm.py`, `dirty: scripts/
ops_watchdog.py`). Мутация живёт в файле секунды, но триггер гардиана — смерть
процесса, а она не спрашивает, чем мы заняты.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gate_guard  # noqa: E402
import new_worktree  # noqa: E402

LIVE = "C:/jarvis"
GATES = sorted(SCRIPTS.glob("mutate_*.py"))


def guardian_row(pid, script, root=LIVE):
    """Строка таблицы процессов вида «гардиан поднимает прод из дерева»."""
    return (pid, "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", "%s/scripts/%s" % (root, script)])


# ── дерево, из которого поднимается прод ────────────────────────────────────

def test_a_guardian_launching_prod_from_the_tree_blocks_the_gate():
    found = gate_guard.deployers(LIVE, [guardian_row(7964, "backend_guardian_detached.ps1")])
    assert [pid for pid, _ in found] == [7964]


def test_an_interpreter_from_the_tree_blocks_the_gate_even_without_a_script_path():
    """Раннер запускается модулем (`-m chatter.telethon_run`) — пути скрипта в
    командной строке нет вовсе, и по нему одному процесс был бы невидим."""
    row = (4848, "C:/jarvis/.venv/Scripts/python.exe",
           ["python.exe", "-u", "-m", "chatter.telethon_run", "--llm", "real"])
    assert [pid for pid, _ in gate_guard.deployers(LIVE, [row])] == [4848]


def test_a_process_that_only_MENTIONS_the_tree_is_not_a_deployer():
    """Запуск ≠ упоминание. Панель на этом уже разбилась 14.08: процесс,
    назвавший раннера в своей командной строке, был принят за раннера."""
    row = (999, "C:/Python314/python.exe",
           ["python.exe", "-c",
            "print('смотри C:/jarvis/app/routers/jarvis_panel.py внимательно')"])
    assert gate_guard.deployers(LIVE, [row]) == []


def test_a_sibling_directory_with_the_same_prefix_is_not_inside_the_tree():
    """`C:/jarvis_worktrees/...` начинается с `C:/jarvis`, но лежит СНАРУЖИ.
    Сравнение префиксом строк запретило бы гейт ровно там, куда мы его гоним."""
    assert gate_guard.under("C:/jarvis/app/x.py", LIVE)
    assert not gate_guard.under("C:/jarvis_worktrees/panels/app/x.py", LIVE)


def test_a_process_whose_exe_is_a_bare_name_is_not_placed_inside_the_tree():
    """Псевдопроцессы Windows (`Registry`, `MemCompression`) отдают вместо пути
    голое имя. `abspath` доклеивает к нему ТЕКУЩИЙ каталог — каталог гейта, —
    и гейт отказал сам себе в собственном worktree (замерено 15.08)."""
    rows = [(136, "Registry", []), (2464, "MemCompression", [])]
    assert gate_guard.deployers("C:/jarvis_worktrees/dev31-gates", rows) == []


def test_the_gate_does_not_count_ITSELF_a_deployer():
    """Гейт запускается интерпретатором из `C:/jarvis/.venv`, то есть в живом
    дереве совпадает сам с собой. Отказ по себе был бы верным по итогу и
    неверным по причине — а чинят по причине."""
    row = (4242, "C:/jarvis/.venv/Scripts/python.exe",
           ["python.exe", "scripts/mutate_panels_hierarchy.py"])
    assert gate_guard.deployers(LIVE, [row]) != []
    assert gate_guard.deployers(LIVE, [row], exclude={4242}) == []


def test_the_main_worktree_is_refused_even_when_the_farm_is_down():
    """«Ферма не запущена» ≠ «дерево безопасно»: задача гардиана поднимет её в
    любую секунду по абсолютному пути, прибитому к главному дереву."""
    reasons = gate_guard.refusal_reasons(LIVE, table=[], main_worktree=True)
    assert reasons and "ГЛАВНОЕ дерево" in reasons[0]


def test_a_worktree_with_no_live_process_is_allowed():
    assert gate_guard.refusal_reasons(LIVE, table=[], main_worktree=False) == []


def test_a_tree_git_cannot_be_asked_about_counts_as_the_main_one(tmp_path):
    """Не сумев спросить, сторож обязан отказать, а не разрешить."""
    assert gate_guard.is_main_worktree(tmp_path) is True


def test_the_refusal_names_the_way_out():
    """Отказ без выхода превращается в предложение его обойти."""
    with pytest.raises(SystemExit) as exc:
        gate_guard.refuse_if_live_tree(LIVE, gate="гейт", table=[], main_worktree=True)
    assert "new_worktree.py" in str(exc.value)


# ── правило распространяется на ВСЕ гейты, а не на тот, где о нём вспомнили ──

def test_every_mutation_gate_asks_the_guard_before_it_mutates():
    forgot = [p.name for p in GATES
              if "refuse_if_live_tree(ROOT)" not in p.read_text(encoding="utf-8")]
    assert forgot == [], "гейты мутируют дерево, не спросив сторожа: %s" % forgot


def _pytest_calls(path: Path):
    """Вызовы subprocess.run(...), которые запускают pytest."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "run"):
            continue
        literals = [e.value for e in ast.walk(node)
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if "pytest" in literals:
            yield node


def test_every_gate_reads_pytest_output_in_utf8():
    """`text=True` берёт cp1251, где байт `0x98` не определён, а он приходит из
    «И» (`D0 98`) и «‘» (`E2 80 98`). Одна заглавная «И» в выводе упавшего
    теста роняет читающий поток, вывод приходит ПУСТЫМ — и пойманная мутация
    печатается слепой. Замерено 15.08: «38 слепых из 100» на краснеющих
    сторожах. Вердикт гейта не имеет права зависеть от буквы в чужом ассерте."""
    bad = []
    for path in GATES:
        for call in _pytest_calls(path):
            kw = {k.arg for k in call.keywords}
            if "encoding" not in kw:
                bad.append("%s:%d" % (path.name, call.lineno))
    assert bad == [], "гейт читает вывод pytest локальной кодировкой: %s" % bad


# ── состав, без которого baseline снимается другим набором файлов ───────────

def test_the_composition_list_is_parsed_without_comments():
    globs = new_worktree.composition_globs(
        "# коммент\n\n  chatter/clients/*/requisites.yaml  # хвост\n")
    assert globs == ["chatter/clients/*/requisites.yaml"]


def test_the_composition_names_the_file_whose_absence_reddened_22_tests():
    globs = new_worktree.composition_globs(
        new_worktree.COMPOSITION.read_text(encoding="utf-8"))
    assert any("requisites.yaml" in g for g in globs)


def test_the_composition_lists_only_gitignored_paths():
    """Трекаемый путь в списке — ложь: он и так приезжает с checkout, а список
    перестаёт отвечать на вопрос «чего в worktree не хватает»."""
    globs = new_worktree.composition_globs(
        new_worktree.COMPOSITION.read_text(encoding="utf-8"))
    tracked = []
    for glob in globs:
        probe = glob.replace("*", "x")
        rc = subprocess.run(["git", "check-ignore", "-q", probe],
                            cwd=str(ROOT)).returncode
        if rc != 0:
            tracked.append(glob)
    assert tracked == [], "эти пути git и так трекает: %s" % tracked


def test_the_gitignored_composition_is_PRESENT_in_this_very_tree():
    """Прямая проверка приёмки №2. Без состава `pytest tests/` даёт 22 ложных
    падения, и они читаются как регресс кода — этот сторож превращает их в
    одно внятное красное: «worktree сделан мимо процедуры»."""
    globs = new_worktree.composition_globs(
        new_worktree.COMPOSITION.read_text(encoding="utf-8"))
    missing = [g for g in globs if not list(ROOT.glob(g))]
    assert missing == [], (
        "в этом дереве нет gitignored-состава: %s\n"
        "worktree создаётся так: python scripts/new_worktree.py --name <имя> "
        "--branch <ветка>" % missing)


# ── свежий worktree обязан быть чистым, и молча гасить правки нельзя ────────

def test_a_byte_identical_file_is_called_a_normalization_artifact(monkeypatch):
    monkeypatch.setattr(new_worktree, "git",
                        lambda *a, **k: "a11fc9c1c24b5faccf0b0c27c165c6294d85bc64\n")
    assert new_worktree.is_normalization_artifact("x.py", Path("."))


def test_a_file_that_really_differs_is_NOT_an_artifact(monkeypatch):
    answers = iter(["aaaaaaa\n", "bbbbbbb\n"])
    monkeypatch.setattr(new_worktree, "git", lambda *a, **k: next(answers))
    assert not new_worktree.is_normalization_artifact("x.py", Path("."))


def test_a_real_difference_in_a_FRESH_worktree_stops_the_procedure(monkeypatch):
    """Свежий worktree никто не правил. Отличие по содержимому означает, что
    сломано что-то ещё, и гасить его `--skip-worktree` значит спрятать."""
    monkeypatch.setattr(new_worktree, "git",
                        lambda *a, **k: " M app/routers/jarvis_panel.py\n")
    monkeypatch.setattr(new_worktree, "is_normalization_artifact",
                        lambda rel, tree: False)
    with pytest.raises(SystemExit) as exc:
        new_worktree.settle(Path("."))
    assert "jarvis_panel.py" in str(exc.value)


def test_the_status_letters_are_stripped_from_the_path():
    """« M x» и «M  x» — одно состояние файла; буквы в пути сделали бы
    `skip-worktree` промахом по несуществующему имени."""
    assert new_worktree.modified_paths(" M a/b.py\nM  c/d.py\n") == ["a/b.py", "c/d.py"]
