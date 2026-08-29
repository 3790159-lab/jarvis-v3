# -*- coding: utf-8 -*-
"""DEV-72: корзины эталона не имеют права разъехаться с самим эталоном.

`known_failures` читают ДВОЕ (`app/services/devtask/regress_batches.py`,
`tools/jarvis_observe.py`); корзины не читает никто. Поле, которое никто не
читает, — это комментарий, и через месяц оно врёт: кто-то поправит список
падений и не тронет разложение. Поэтому корзины привязаны МЕХАНИЧЕСКИ: сумма
трёх обязана быть равна списку, по которому реально судят
([[jarvis-loud-failure-next-to-a-soothing-lamp]]).

🔴 ПОЧЕМУ ФАЙЛ ПРОВЕРЯЕТСЯ ТОЛЬКО КОГДА ОН ЕСТЬ, И ПОЧЕМУ ЭТО НЕ ДЫРА.
`state/` не в git: в свежем worktree эталона нет ПО ПОСТРОЕНИЮ, и «нет
эталона» — законное состояние самого продукта (`jarvis_observe`: «baseline не
задан»). Сторож, падающий на его отсутствие, давал бы ложный красный в каждом
новом дереве — ровно тот класс, что уже стоил дому 22 ложных падения
([[jarvis-worktree-missing-gitignored-client-config]]).

Но «пропустить» — это способ зеленеть молча. Поэтому проверка разделена
надвое: САМА ЛОГИКА сверки гоняется всегда, на синтетике, и краснеет, если
разучится ловить; ЖИВОЙ артефакт проверяется этой же логикой, когда он под
рукой. Так пропуск не уносит с собой проверку — он уносит только её предмет.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "state" / "regress_baseline.json"

BUCKETS = ("known_failures_debt", "known_failures_env", "known_failures_unknown")


def problems(d: dict) -> list[str]:
    """Все претензии к эталону СПИСКОМ, а не первой попавшейся.

    Списком намеренно: эталон правят руками после долгого прогона, и «почини
    одно, узнай про второе» — это ещё один прогон. Претензии формулируются так,
    чтобы по ним можно было починить, не открывая этот файл.
    """
    out: list[str] = []

    known = d.get("known_failures")
    if not isinstance(known, list):
        return ["нет списка `known_failures` — по нему судят, без него эталона нет"]
    known_set = set(known)

    union: set[str] = set()
    seen: dict[str, str] = {}
    for b in BUCKETS:
        names = d.get(b)
        if not isinstance(names, list):
            out.append(f"нет корзины {b!r}: разложение DEV-72 неполно")
            continue
        for name in names:
            if name in seen:
                out.append(
                    f"{name!r} лежит сразу в двух корзинах ({seen[name]} и {b}): "
                    f"сумма больше целого, и любой вывод «сколько из этого наш "
                    f"долг» неверен")
            seen[name] = b
        union |= set(names)

    if union != known_set:
        only_b = sorted(union - known_set)
        only_k = sorted(known_set - union)
        out.append(
            f"корзины разошлись со списком: только в корзинах {only_b}, "
            f"только в списке {only_k}. Судят по списку, разложение читает "
            f"человек — расхождение значит, что человек читает не про тот прогон")

    failed = d.get("failed")
    if failed != len(known):
        out.append(
            f"`failed`={failed}, а имён в списке {len(known)}: два числа на одну "
            f"вещь, и меньшее гасит большее молча")

    comp = d.get("composition", "")
    for token in (".env", "requisites.yaml", "new_worktree.py"):
        if token not in comp:
            out.append(
                f"в `composition` не назван {token!r}. Состав обязан быть назван "
                f"ПОИМЁННО: неполный состав не просто завышает число, он меняет "
                f"состав имён В ОБЕ СТОРОНЫ (замер DEV-72: 97 против 44, и два "
                f"теста «без ключа обязано падать» в неполном дереве зелены)")
    return out


# ── логика сверки: гоняется ВСЕГДА ────────────────────────────────────────

_GOOD = {
    "failed": 2,
    "known_failures": ["t/a.py::x", "t/b.py::y"],
    "known_failures_debt": ["t/a.py::x"],
    "known_failures_env": ["t/b.py::y"],
    "known_failures_unknown": [],
    "composition": "ПОЛНЫЙ: .env, .env.runpod, requisites.yaml; scripts/new_worktree.py",
}


def test_pravilnyi_etalon_pretenzii_ne_vyzyvaet():
    assert problems(dict(_GOOD)) == []


@pytest.mark.parametrize("what, broken", [
    ("имя пропало из корзин",
     {**_GOOD, "known_failures_env": []}),
    ("имя есть в корзине, но не в списке",
     {**_GOOD, "known_failures_unknown": ["t/c.py::z"]}),
    ("имя посчитано дважды",
     {**_GOOD, "known_failures_unknown": ["t/a.py::x"]}),
    ("`failed` от прошлого прогона",
     {**_GOOD, "failed": 97}),
    ("корзины нет вовсе",
     {k: v for k, v in _GOOD.items() if k != "known_failures_debt"}),
    ("состав дерева не назван",
     {**_GOOD, "composition": "worktree"}),
])
def test_logika_lovit_kazhdyi_vid_rashozhdeniya(what, broken):
    """Ловит: сверку, которая разучилась ловить.

    Без этого набора `problems()` могла бы однажды начать возвращать пустой
    список на любом входе — и живая проверка ниже стала бы зелёной навсегда,
    не изменившись ни строкой.
    """
    assert problems(broken), f"расхождение «{what}» прошло незамеченным"


# ── живой артефакт: проверяется той же логикой, когда он есть ─────────────

def test_zhivoi_etalon_soglasen_sam_s_soboi():
    if not BASELINE.is_file():
        pytest.skip(
            "state/regress_baseline.json нет в этом дереве — законное "
            "состояние свежего worktree (`state/` не в git) и самого продукта "
            "(«baseline не задан»). Логика сверки при этом проверена выше."
        )
    found = problems(json.loads(BASELINE.read_text(encoding="utf-8")))
    assert not found, "эталон не согласен сам с собой:\n- " + "\n- ".join(found)
