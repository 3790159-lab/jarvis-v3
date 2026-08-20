"""DEV-26: мутационный гейт обязан отдавать каждой мутации СВОЙ байткод.

Python признаёт `.pyc` актуальным по паре (mtime в ЦЕЛЫХ секундах, размер
исходника). Две мутации одного размера, записанные в одну секунду, для него
неотличимы — вторая исполняется байткодом первой. Гейт при этом печатает
бодрое `[ok]`, ничего не проверив: сторож «покраснел» на чужом коде.

Дыру нашли в платежах, и она вернулась в `mutate_ops_watchdog.py` — там все
семнадцать мутаций бьют в ОДИН файл, то есть окно совпадения максимально
широкое. Поэтому сторож здесь не на конкретный скрипт, а на все сразу:
следующий гейт напишут копипастой, и без проверки дыра приедет вместе с ней.

────────────────────────────────────────────────────────────────────────────
🔴 ВТОРОЙ СПОСОБ ПРОЙТИ МИМО ЭТОГО ФАЙЛА, замерен 20.08.

Гейт `mutate_panel_client_supervisor` объявил `write_mutant(rel, text, bom)`
— по делу: `.ps1` нужен BOM, `.py` нужен LF. Мета-сторож позвать его не смог:

    TypeError: write_mutant() missing 1 required positional argument: 'bom'

Гейт проверку НЕ ПРОВАЛИЛ — он из-под неё ВЫШЕЛ. Защита от чужого байткода
осталась никем не измеренной, а гейт продолжал рапортовать «все мутации
пойманы». Ровно тот класс, который мы весь день выкапываем: вещь, выглядящая
покрытием.

Отсюда две правки, и обе — про механику, а не про этот конкретный гейт.

1. КОНТРАКТ ТЕРПИМ К БУДУЩИМ АРГУМЕНТАМ. Новые параметры будут появляться и
   дальше — BOM сегодня, кодировка или права завтра. Контракт сузился до
   того, что действительно общее:

       write_mutant(<путь>, <текст>, **что_угодно_с_умолчаниями)

   Первые два параметра позиционны и означают путь и текст. Всё сверх них
   мета-сторож разбирает `inspect`'ом и НЕ ПЕРЕДАЁТ, если у параметра есть
   умолчание; а если умолчания нет — подставляет безопасное значение, чтобы
   ПОВЕДЕНИЕ всё равно было измерено. Требование «умолчание обязательно»
   живёт отдельным сторожем с внятным текстом, а не всплывает `TypeError`ом
   посреди чужого падения.

2. СТОРОЖ НА САМ МЕТА-СТОРОЖ. Раньше «не смогли позвать» и «позвали,
   поведение верное» различал только текст падения — то есть человек,
   который посмотрел. Теперь каждый найденный гейт обязан быть ФАКТИЧЕСКИ
   ИЗМЕРЕН, и гейт, которого позвать не удалось, называется ПОИМЁННО.

Терпимость касается ТОЛЬКО формы вызова. Суть не сдвинулась ни на шаг: две
записи ОДНОГО РАЗМЕРА обязаны получить РАЗНЫЕ mtime.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
GATES = sorted(SCRIPTS.glob("mutate_*.py"))

# Гейты импортируют соседей по каталогу (`from gate_guard import ...`).
# Кладём `scripts/` на путь ЗДЕСЬ, а не надеемся, что его туда положит
# какой-нибудь другой тест раньше по алфавиту: сторож, работающий только в
# компании, не сторож. Прямое следствие — этот файл гоняется в одиночку.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

_LOADED: dict[str, object] = {}

# Что подставить параметру БЕЗ умолчания, чтобы измерить поведение всё равно.
# Отказаться от вызова было бы удобнее и хуже: непроверенная защита от чужого
# байткода — это и есть дыра, которую файл закрывает.
_SAFE_BY_ANNOTATION = {
    bool: False, int: 0, float: 0.0, str: "", bytes: b"",
    "bool": False, "int": 0, "float": 0.0, "str": "", "bytes": b"",
}


def _load(path: Path):
    if path.stem in _LOADED:
        return _LOADED[path.stem]
    spec = importlib.util.spec_from_file_location(f"_gate_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _LOADED[path.stem] = mod
    return mod


def _writer(gate: Path):
    """(callable, signature). Бросает AssertionError с внятным текстом."""
    mod = _load(gate)
    writer = getattr(mod, "write_mutant", None)
    assert writer is not None, (
        f"{gate.name}: пишет мутанта голым write_text — байткод предыдущей "
        f"мутации переживёт запись, и гейт скажет [ok], ничего не проверив")
    return writer, inspect.signature(writer)


def _positional(sig: inspect.Signature) -> list[inspect.Parameter]:
    kinds = (inspect.Parameter.POSITIONAL_ONLY,
             inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return [p for p in sig.parameters.values() if p.kind in kinds]


def _extras_without_default(sig: inspect.Signature) -> list[str]:
    """Параметры сверх (path, text), у которых нет умолчания.

    `*args` / `**kwargs` сюда не попадают: они вызову двумя аргументами не
    мешают."""
    out = []
    for p in list(sig.parameters.values())[2:]:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL,
                      inspect.Parameter.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            out.append(p.name)
    return out


def _safe_value(p: inspect.Parameter, target: Path):
    """Значение для параметра без умолчания. По аннотации, иначе None."""
    ann = p.annotation
    if ann in _SAFE_BY_ANNOTATION:
        return _SAFE_BY_ANNOTATION[ann]
    if ann is Path or (isinstance(ann, str) and ann.endswith("Path")):
        return target
    return None


def _call(writer, sig: inspect.Signature, target: Path, text: str) -> None:
    """Позвать writer по ТЕРПИМОМУ контракту: путь и текст позиционно, всё
    остальное — умолчаниями, а без умолчания — безопасным значением."""
    kwargs = {}
    for p in list(sig.parameters.values())[2:]:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL,
                      inspect.Parameter.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            kwargs[p.name] = _safe_value(p, target)
    writer(target, text, **kwargs)


# Сколько записей подряд делаем. Три, а не две, и вот почему.
#
# 🔴 ЗАМЕР 20.08: прежний сторож сравнивал mtime как ДРОБНЫЕ числа. NTFS
# держит время с точностью до 100 нс, поэтому две записи подряд почти всегда
# получают разные дробные mtime — И БЕЗ ВСЯКОЙ ПОДПИСИ. То есть сторож,
# написанный ровно против «гейт не стампует mtime», на такой гейт НЕ КРАСНЕЛ.
# Проверено пустышкой: голый write_text дважды — тест зелёный.
#
# Python сверяет .pyc по mtime в ЦЕЛЫХ СЕКУНДАХ. Сравнивать надо ровно так же,
# и тогда писатель «по часам» упирается в потолок: одно значение в секунду.
# Три записи подряд занимают микросекунды, поэтому у писателя без подписи
# совпадут как минимум две; у писателя с подписью все три различны ПО
# ПОСТРОЕНИЮ — стамп у них монотонный счётчик, а не часы.
_WRITES = 3


def _measure(gate: Path, tmp_path: Path) -> dict:
    """Один замер: несколько записей ОДИНАКОВОГО размера подряд.

    Возвращает словарь, а не бросает: «не смогли позвать» — это ОТДЕЛЬНОЕ
    показание, и сторож на покрытие ниже читает именно его."""
    out = {"gate": gate.stem, "called": False, "reason": "",
           "seconds": [], "sizes": [], "landed": False}
    try:
        writer, sig = _writer(gate)
    except AssertionError as exc:
        out["reason"] = f"{type(exc).__name__}: {exc}"
        return out
    except Exception as exc:
        out["reason"] = f"модуль не загрузился — {type(exc).__name__}: {exc}"
        return out

    target = tmp_path / f"{gate.stem}_m.py"
    try:
        for n in range(_WRITES):
            # Тексты РАЗНЫЕ, а длина ОДНА: ровно та пара, которую Python не
            # различает. Одинаковый текст сделал бы проверку бессмысленной.
            _call(writer, sig, target, f"A = {n}")
            if not target.exists():
                out["landed"] = False
                out["reason"] = (
                    "первый аргумент не считается ПУТЁМ: файла по указанному "
                    f"пути нет ({target})")
                return out
            out["landed"] = True
            st = target.stat()
            out["seconds"].append(int(st.st_mtime))
            out["sizes"].append(st.st_size)
    except Exception as exc:
        out["reason"] = f"{type(exc).__name__}: {exc}"
        return out
    out["called"] = True
    return out


# ──────────────────────── что вообще охраняем ────────────────────────

def test_there_are_gates_to_check():
    """Сторож обязан кого-то охранять: переименуют папку — молча пройдёт."""
    assert len(GATES) >= 5, f"мутационных гейтов не найдено: {GATES}"


# ─────────────── контракт подписи: терпимый, но НАЗВАННЫЙ ───────────────

@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.stem)
def test_gate_writer_takes_a_path_and_a_text_positionally(gate):
    """Первые два параметра — путь и текст, позиционно. Это весь контракт.

    Имена не проверяем: `path`/`target`/`dst` — дело автора гейта. Проверяем
    ФОРМУ, которая нужна зовущему снаружи."""
    _writer_, sig = _writer(gate)
    pos = _positional(sig)
    assert len(pos) >= 2, (
        f"{gate.name}: write_mutant{sig} — первые два параметра обязаны быть "
        f"позиционными (путь, текст), иначе мета-сторож позвать его не может")


@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.stem)
def test_gate_writer_gives_every_extra_parameter_a_default(gate):
    """🔴 ТРЕБОВАНИЕ, КОТОРОЕ РАНЬШЕ ВСПЛЫВАЛО TypeError'ом.

    Всё сверх (путь, текст) обязано иметь умолчание. Тогда любой зовущий
    снаружи — этот файл, соседний гейт, будущий инструмент — работает вызовом
    из двух аргументов, а гейт волен добавлять сколько угодно своего.

    Сказано ОТДЕЛЬНЫМ сторожем с внятным текстом намеренно: `TypeError`
    посреди падения про mtime читается как поломка сторожа, а не как
    нарушение контракта, и 20.08 именно так и прочитался."""
    _writer_, sig = _writer(gate)
    missing = _extras_without_default(sig)
    assert not missing, (
        f"{gate.name}: write_mutant{sig} — у параметров {missing} нет "
        f"умолчания. Контракт: write_mutant(путь, текст, **всё_остальное_с_"
        f"умолчаниями). Без умолчания гейт ВЫХОДИТ из-под проверки байткода, "
        f"а не проваливает её")


# ──────────────────────── ПОВЕДЕНИЕ, не форма ────────────────────────

@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.stem)
def test_gate_stamps_a_unique_mtime_on_every_write(gate, tmp_path):
    """Записи одного размера подряд обязаны получить РАЗНЫЕ mtime В СЕКУНДАХ.

    Проверяем поведение, а не наличие строки `os.utime`: подпись можно
    оставить на месте и передать ей одно и то же значение.

    Секунды, а не дробные mtime — см. `_WRITES`: дробные различаются сами
    собой на любой нормальной ФС, и сторож на них зелен ровно на том гейте,
    против которого написан.

    Терпимость к форме вызова НЕ ослабила требование: размеры сверяются
    отдельно, чтобы «разные mtime» не оказались верными по случайной причине
    (writer, меняющий длину, сам себе даёт новый байткод и без подписи)."""
    m = _measure(gate, tmp_path)
    assert m["called"], f"{gate.name}: замер не состоялся — {m['reason']}"
    assert len(set(m["sizes"])) == 1, (
        f"{gate.name}: предпосылка сломана, записи разного размера "
        f"({m['sizes']}) — совпадение mtime такой паре не грозит, и проверка "
        f"ничего не значит")
    assert len(set(m["seconds"])) == len(m["seconds"]), (
        f"{gate.name}: {len(m['seconds'])} записи одного размера дали mtime "
        f"{m['seconds']} — совпавшие секунды означают, что вторая мутация "
        f"исполнится байткодом первой. Подписи нет либо она берётся с часов")


@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.stem)
def test_gate_writer_writes_where_it_is_told(gate, tmp_path):
    """Первый аргумент — НАСТОЯЩИЙ путь, а не имя относительно корня репо.

    Не педантизм: мета-сторож пишет во временный каталог. Writer, трактующий
    первый аргумент как относительный, полез бы в РАБОЧЕЕ ДЕРЕВО — то есть
    тест начал бы править исходники."""
    m = _measure(gate, tmp_path)
    assert m["landed"], f"{gate.name}: {m['reason'] or 'файл не появился'}"
    assert not list(tmp_path.glob("**/scripts")), (
        f"{gate.name}: writer собрал путь сам и создал чужую иерархию")


# ──────── сторож НА САМ МЕТА-СТОРОЖ: измерен КАЖДЫЙ, поимённо ────────

def test_every_gate_was_actually_measured(tmp_path):
    """🔴 БЕЗ ЭТОГО СТОРОЖА «не смогли позвать» выглядит как «всё хорошо».

    Довод владельца дословно: «сейчас вызов просто упал, и никто бы не узнал,
    если бы ты не посмотрел». Параметризованные сторожа выше падают по одному
    и в общем прогоне теряются среди чужих красных; здесь — один список, и в
    нём КАЖДЫЙ гейт назван по имени вместе с причиной, по которой его не
    удалось измерить.

    Замер настоящий, а не пересказ чужих результатов: файл могли запустить
    выборочно (`-k`), и сторож, читающий чужие следы, оказался бы зелёным
    просто потому, что до него никто ничего не записал."""
    results = []
    for gate in GATES:
        # Каталог создаём ДО замера: writer пишет файл, а не строит дерево,
        # и отсутствие каталога сделало бы «не смогли позвать» из ничего.
        sandbox = tmp_path / gate.stem
        sandbox.mkdir(parents=True, exist_ok=True)
        results.append(_measure(gate, sandbox))

    unmeasured = [(r["gate"], r["reason"]) for r in results if not r["called"]]
    assert not unmeasured, (
        "эти гейты НЕ БЫЛИ ПРОВЕРЕНЫ на защиту от чужого байткода — их не "
        "удалось позвать, и раньше это выглядело бы как отсутствие проблемы:\n"
        + "\n".join(f"  * {name}: {why}" for name, why in unmeasured))
    assert len(results) == len(GATES) and results, "измерять оказалось нечего"


# ──────────────────────── запись мимо помощника ────────────────────────

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
