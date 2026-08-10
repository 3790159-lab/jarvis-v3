"""DEV-26: ломаем фикс обратно и требуем КРАСНОГО от конкретного сторожа.

Тест, оставшийся зелёным на сломанной реализации, ничего не охраняет.
Каждая мутация — точечная замена в файле; после прогона файл возвращается
из git.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

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

    # ── утечка тестовых активов: самый дорогой класс ошибки арки ────────────
    ("gate: пропускает кого угодно", "chatter/payments/drill_gate.py",
     "    if not is_drill_contact(contact_id):",
     "    if False:",
     "tests/chatter/test_payments_drill_gate.py::test_gate_refuses_live_contact_loudly"),

    ("gate: пустой contact_id считается своим", "chatter/payments/drill_gate.py",
     "    return contact_id in DRILL_CONTACTS",
     "    return contact_id is None or contact_id in DRILL_CONTACTS",
     "tests/chatter/test_payments_drill_gate.py::test_gate_refuses_empty_or_unknown_contact"),

    ("gate: список расширен живым контактом", "chatter/payments/drill_gate.py",
     '    "8849893367:volska",',
     '    "8849893367:volska",\n    "555000111:volska",',
     "tests/chatter/test_payments_instructions.py::test_test_requisites_never_leak_to_a_live_contact"),

    ("gate: список разошёлся с каноном scripts/", "chatter/payments/drill_gate.py",
     '    "237616472:volska",',
     '    "237616472:volska",\n    "999:volska",',
     "tests/chatter/test_payments_drill_gate.py::test_canonical_list_is_the_same_as_the_scripts_one"),

    ("реквизиты: живой контакт заглядывает в test_templates",
     "chatter/payments/instructions.py",
     "    if is_drill_contact(contact_id):",
     "    if True:",
     "tests/chatter/test_payments_instructions.py::test_test_requisites_never_leak_to_a_live_contact"),

    ("реквизиты: тестовые как фоллбэк при отсутствии клиентских",
     "chatter/payments/instructions.py",
     '    body = book.templates.get(key, "")',
     '    body = book.templates.get(key, "") or book.test_templates.get(key, "")',
     "tests/chatter/test_payments_instructions.py::test_test_requisites_never_leak_to_a_live_contact"),

    ("реквизиты: живому отдаются клиентские, но подписаны как test",
     "chatter/payments/instructions.py",
     '                body_text=test_body, requisites_ref=key, source="test")',
     '                body_text=test_body, requisites_ref=key, source="config")',
     "tests/chatter/test_payments_instructions.py::test_drill_contact_gets_the_test_requisites"),

    ("реквизиты: пустое тело считается выданным", "chatter/payments/instructions.py",
     "    if not body:",
     "    if body is None:",
     "tests/chatter/test_payments_instructions.py::test_empty_body_counts_as_absent"),

    ("реквизиты: mode=auto тихо деградирует в manual", "chatter/payments/instructions.py",
     '    if channel.mode == "auto":',
     "    if False:",
     "tests/chatter/test_payments_instructions.py::test_channel_mode_auto_is_declared_but_not_implemented_in_phase0"),

    ("объём: заглушка уходит живому контакту", "chatter/payments/scope.py",
     "    if entry.placeholder:\n        guard_test_asset",
     "    if False:\n        guard_test_asset",
     "tests/chatter/test_payments_scope.py::test_placeholder_refuses_live_contact"),

    ("объём: конфиг с заглушками проходит валидацию", "chatter/payments/scope.py",
     "    if stubs:",
     "    if False:",
     "tests/chatter/test_payments_scope.py::test_full_config_with_stubs_fails_validation_for_a_live_contact"),

    ("объём: ступень без текста не замечается", "chatter/payments/scope.py",
     "    if missing:",
     "    if False:",
     "tests/chatter/test_payments_scope.py::test_missing_text_for_a_declared_step_is_caught_by_the_same_check"),

    ("объём: заглушка угадывается по виду строки", "chatter/payments/scope.py",
     '            placeholder = bool(entry.get("placeholder", False))',
     '            placeholder = bool(entry.get("placeholder", False)) or text.startswith("TODO")',
     "tests/chatter/test_payments_scope.py::test_marker_must_be_explicit_not_guessed"),

    # ── пункт 1: модель счёта и перестройка payments ───────────────────────
    ("миграция: окно не проверяется, строки конвертируются молча",
     "chatter/storage/db.py", "        if n:", "        if False:",
     "tests/chatter/test_payments_store.py::test_rebuild_refuses_when_rows_exist"),

    ("миграция: старая схема не распознаётся", "chatter/storage/db.py",
     'return bool(cols) and "dedup_key" not in cols', "return False",
     "tests/chatter/test_payments_store.py::test_legacy_empty_table_is_rebuilt"),

    ("миграция: бэкап не делается", "chatter/storage/db.py",
     '                if pre_existing:\n                    self._backup(path, tag="payments")',
     '                if False:\n                    self._backup(path, tag="payments")',
     "tests/chatter/test_payments_store.py::test_rebuild_makes_a_backup_first"),

    ("payments: UNIQUE снят со схемы", "chatter/storage/db.py",
     "    UNIQUE (contact_id, dedup_key)\n);",
     "    x_unused INTEGER\n);",
     "tests/chatter/test_payments_store.py::test_unique_index_exists_in_the_database_itself"),

    ("payments: повтор не правит сумму, а игнорируется", "chatter/storage/db.py",
     " ON CONFLICT(contact_id, dedup_key) DO UPDATE SET",
     " ON CONFLICT(contact_id, dedup_key) DO NOTHING --",
     "tests/chatter/test_payments_store.py::test_two_taps_on_the_same_card_are_one_payment"),

    ("счёт: идемпотентность выставления снята", "chatter/storage/db.py",
     "            if origin_msg_id is not None:", "            if False:",
     "tests/chatter/test_payments_store.py::test_invoice_issuing_is_idempotent_on_origin_msg_id"),

    ("счёт: сумма не обязательна для issued", "chatter/storage/db.py",
     '        if total is None and status not in ("draft", "awaiting_owner"):',
     "        if False:",
     "tests/chatter/test_payments_store.py::test_issued_invoice_cannot_exist_without_an_amount"),

    ("счёт: first_payment_ts берёт последнюю оплату", "chatter/storage/db.py",
     '"SELECT MIN(ts) AS t FROM payments WHERE invoice_id=?"',
     '"SELECT MAX(ts) AS t FROM payments WHERE invoice_id=?"',
     "tests/chatter/test_payments_store.py::test_first_payment_ts_records_the_first_not_the_last"),

    ("котировка: прежняя не становится superseded", "chatter/storage/db.py",
     "                \"UPDATE quotes SET status='superseded'\"",
     "                \"UPDATE quotes SET status='active'\"",
     "tests/chatter/test_payments_store.py::test_quote_history_is_append_only"),

    ("проекция: недоплата считается оплатой", "chatter/payments/model.py",
     '        target = "overdue" if overdue else "partially_paid"',
     '        target = "paid"',
     "tests/chatter/test_payments_model.py::test_partial_is_not_paid"),

    ("проекция: переплата считается оплатой", "chatter/payments/model.py",
     '        target = "overpaid"', '        target = "paid"',
     "tests/chatter/test_payments_model.py::test_more_than_total_is_overpaid_not_paid"),

    ("проекция: отменённый счёт воскресает от поступления",
     "chatter/payments/model.py",
     "    if current in _NOT_MONEY:\n        return current",
     "    if False:\n        return current",
     "tests/chatter/test_payments_model.py::test_projection_does_not_touch_non_money_states"),

    ("ключ: пустая личность превращается в сентинел", "chatter/payments/model.py",
     "    if not text:", "    if False:",
     "tests/chatter/test_payments_model.py::test_empty_identity_is_rejected"),

    ("пульт: оплата без личности пишется сентинелом",
     "chatter/notify/control_bot.py",
     '            log.warning("route_callback: оплата без личности события (%s)", contact_id)',
     '            dedup_key = "panel:0"\n            log.warning("x", contact_id)',
     "tests/chatter/test_paid_action.py::test_payment_without_identity_is_refused_and_writes_nothing"),

    ("пульт: токен панели игнорируется", "chatter/notify/control_bot.py",
     'dedup_key = make_dedup_key("panel", event_token)',
     'dedup_key = make_dedup_key("panel", "fixed")',
     "tests/chatter/test_paid_action.py::test_two_panel_payments_with_different_tokens_are_two_rows"),

    ("пульт: сумма снова через float", "chatter/notify/control_bot.py",
     'amount = from_major((paid_amount_raw or "").replace(",", ".").strip(), "USD")',
     'amount = Money(int(float((paid_amount_raw or "0")) * 100), "USD")',
     "tests/chatter/test_paid_action.py::test_money_is_stored_in_minor_units"),
    # ── пункт 2: versioned-кодек callback'ов ───────────────────────────────
    ("кодек: legacy-формат выпал из реестра", "chatter/payments/callbacks.py",
     '    "paidamt": _parse_paidamt_v1,', '    "paidamtX": _parse_paidamt_v1,',
     "tests/chatter/test_payments_callbacks.py::test_legacy_paid_with_dollar_amount"),

    ("кодек: строитель снова выпускает v1", "chatter/payments/callbacks.py",
     'return _guard_limit(f"paidamt2:{amount.minor}:{amount.ccy}:{contact_id}")',
     'return _guard_limit(f"paidamt:{amount.minor // 100}:{contact_id}")',
     "tests/chatter/test_payments_callbacks.py::test_builder_emits_the_current_version_not_legacy"),

    ("кодек: лимит 64 байта не проверяется", "chatter/payments/callbacks.py",
     "    if len(data.encode(\"utf-8\")) > CALLBACK_LIMIT:", "    if False:",
     "tests/chatter/test_payments_callbacks.py::test_builder_refuses_to_emit_oversized_data"),

    ("кодек: действие по счёту принимает contact_id", "chatter/payments/callbacks.py",
     '        if not rest.startswith("INV-"):', "        if False:",
     "tests/chatter/test_payments_callbacks.py::test_invoice_actions_are_not_addressed_by_contact"),

    ("кодек: мусорная сумма становится нулём", "chatter/payments/callbacks.py",
     "    if amount.minor <= 0:\n        return None",
     "    if False:\n        return None",
     "tests/chatter/test_payments_callbacks.py::test_broken_amount_is_none_not_zero"),

    ("кодек: неизвестная валюта принимается", "chatter/payments/callbacks.py",
     "or ccy not in MINOR_EXPONENT:", "or False:",
     "tests/chatter/test_payments_callbacks.py::test_unknown_currency_is_rejected"),
]


def run(test: str) -> bool:
    """True = тест зелёный."""
    p = subprocess.run([sys.executable, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
    return p.returncode == 0


def revert(rel: str) -> None:
    subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)


def assert_clean() -> None:
    """Отказ работать на грязном дереве.

    Откат мутаций идёт через `git checkout --`, то есть НЕЗАКОММИЧЕННЫЕ правки
    он сотрёт. Один раз этого хватило, чтобы потерять готовую проводку: харнесс
    честно вернул файл к последнему коммиту. Теперь он сначала проверяет, что
    терять нечего."""
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if out:
        raise SystemExit(
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.
"
            "Закоммить их и повтори прогон:
" + out)


def main() -> int:
    assert_clean()
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
