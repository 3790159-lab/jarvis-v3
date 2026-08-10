"""DEV-26: ломаем фикс обратно и требуем КРАСНОГО от конкретного сторожа.

Тест, оставшийся зелёным на сломанной реализации, ничего не охраняет.
Каждая мутация — точечная замена в файле; после прогона файл возвращается
из git.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\jarvis")

# (имя, файл, что заменить, на что, какой тест ОБЯЗАН покраснеть)
MUTATIONS = [
    ("money: float/bool пролезает в конструктор", "chatter/payments/money.py",
     "        if isinstance(self.minor, bool) or not isinstance(self.minor, int):",
     "        if False:",
     "tests/chatter/test_payments_money.py::test_float_is_rejected_everywhere"),

    ("money: bool считается целым", "chatter/payments/money.py",
     "        if isinstance(self.minor, bool) or not isinstance(self.minor, int):",
     "        if not isinstance(self.minor, int):",
     "tests/chatter/test_payments_money.py::test_bool_is_not_an_int_here"),

    ("money: лишняя точность тихо округляется", "chatter/payments/money.py",
     "    if scaled != scaled.to_integral_value():",
     "    if False:",
     "tests/chatter/test_payments_money.py::test_extra_precision_is_an_error_not_a_rounding"),

    ("money: валюты смешиваются", "chatter/payments/money.py",
     "    if a.ccy != b.ccy:",
     "    if False:",
     "tests/chatter/test_payments_money.py::test_arithmetic_refuses_to_mix_currencies"),

    ("statuses: владелец ставит paid рукой", "chatter/payments/statuses.py",
     '    ("issued", "paid", _SYSTEM),',
     '    ("issued", "paid", frozenset({"system", "owner", "provider"})),',
     "tests/chatter/test_payments_statuses.py::test_money_statuses_are_a_projection_not_an_owner_write"),

    ("statuses: отмена доступна системе", "chatter/payments/statuses.py",
     '    ("issued", "cancelled", _OWNER),',
     '    ("issued", "cancelled", frozenset({"owner", "system", "provider"})),',
     "tests/chatter/test_payments_statuses.py::test_cancel_is_owner_only"),

    ("statuses: partially_paid считается закрытыми деньгами", "chatter/payments/statuses.py",
     '_SETTLED = frozenset({"paid"})',
     '_SETTLED = frozenset({"paid", "partially_paid", "overpaid"})',
     "tests/chatter/test_payments_statuses.py::test_is_settled_is_the_only_way_to_ask_about_money"),

    ("statuses: неизвестный актор проходит", "chatter/payments/statuses.py",
     "    if actor not in ACTORS:",
     "    if False:",
     "tests/chatter/test_payments_statuses.py::test_unknown_status_or_actor_is_an_error_not_a_silent_no"),

    ("pricing: верх сетки не сверяется с вилкой", "chatter/payments/pricing.py",
     "    if steps[0].amount != high:",
     "    if False:",
     "tests/chatter/test_payments_pricing.py::test_ladder_top_must_equal_price_range_upper"),

    ("pricing: пол не сверяется с вилкой", "chatter/payments/pricing.py",
     "    if steps[-1].amount != low:",
     "    if False:",
     "tests/chatter/test_payments_pricing.py::test_ladder_floor_must_equal_price_range_lower"),

    ("pricing: сетка может не убывать", "chatter/payments/pricing.py",
     "        if prev is not None and amount.minor >= prev:",
     "        if False:",
     "tests/chatter/test_payments_pricing.py::test_ladder_must_descend_strictly"),

    ("pricing: scope_key может повторяться (молча дешевле)", "chatter/payments/pricing.py",
     "        if scope in seen_scopes:",
     "        if False:",
     "tests/chatter/test_payments_pricing.py::test_every_step_must_carry_a_distinct_scope"),

    ("pricing: границы вилки не обязаны быть в knowledge", "chatter/payments/pricing.py",
     "        if not _literal_in_knowledge(bound, ccy, knowledge):",
     "        if False:",
     "tests/chatter/test_payments_pricing.py::test_range_bounds_must_be_literals_in_knowledge"),

    ("pricing: пол пробивается вниз", "chatter/payments/pricing.py",
     "    if is_floor(pos, idx):\n        return None",
     "    if idx + 1 >= len(pos.steps):\n        return pos.steps[-1]",
     "tests/chatter/test_payments_pricing.py::test_below_the_floor_there_is_nothing"),

    ("complexity: нет разбора → считаем простым", "chatter/payments/complexity.py",
     "    if not req.parsed or not req.items:",
     "    if False:",
     "tests/chatter/test_payments_complexity.py::test_no_parse_defaults_to_owner_not_to_simple"),

    ("complexity: количество ≤0 молча считается одним", "chatter/payments/complexity.py",
     '        elif item.qty < 1:\n            found.append("bad_quantity")',
     "        elif False:\n            pass",
     "tests/chatter/test_payments_complexity.py::test_non_positive_quantity_is_not_silently_one"),

    ("complexity: несколько единиц не признак", "chatter/payments/complexity.py",
     '        if item.qty > 1:\n            found.append("multiple_units")',
     "        if False:\n            pass",
     "tests/chatter/test_payments_complexity.py::test_one_service_several_units_goes_to_owner"),

    ("complexity: причины в порядке обхода входа", "chatter/payments/complexity.py",
     "    return tuple(r for r in REASONS if r in found)",
     "    return tuple(found)",
     "tests/chatter/test_payments_complexity.py::test_reasons_are_ordered_by_declaration_not_by_discovery"),
]


def run(test: str) -> bool:
    """True = тест зелёный."""
    p = subprocess.run([sys.executable, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
    return p.returncode == 0


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def main() -> int:
    blind = []
    for name, rel, old, new, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — искомый фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        try:
            green = run(test)
        finally:
            revert(rel)
        if green:
            print(f"[СЛЕП] {name}\n        {test} остался ЗЕЛЁНЫМ")
            blind.append((name, test))
        else:
            print(f"[ok]   {name} → сторож покраснел")
    print()
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
