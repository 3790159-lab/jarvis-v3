"""DEV-26: ломаем фикс обратно и требуем КРАСНОГО от конкретного сторожа.

Тест, оставшийся зелёным на сломанной реализации, ничего не охраняет.
Каждая мутация — точечная замена в файле; после прогона файл возвращается
из git.
"""
from __future__ import annotations

import os
import subprocess
import sys
from itertools import count
from pathlib import Path
from gate_guard import refuse_if_live_tree   # DEV-31: гейт мутирует только worktree

ROOT = Path(__file__).resolve().parents[1]   # работает и в worktree

# Python признаёт кэш байткода актуальным по паре (mtime исходника в ЦЕЛЫХ
# секундах, его размер). Две соседние мутации ОДНОГО файла, дающие одинаковый
# размер и попавшие в одну секунду, для этой проверки неотличимы — вторая
# исполняется байткодом первой.
#
# Это не теория: 11.08 пара мутаций в prompt.py (обе дают 17304 байта) дала
# ложное [СЛЕП] на живом стороже. Обратная сторона дороже — мутация, которая
# не исполнялась ни разу, отчитывается как [ok], и гейт из 120 проверок
# уверенно подтверждает то, чего не проверял.
#
# Лечим в источнике: каждой записи — свой уникальный mtime, который не
# повторится. Тогда ни один .pyc не может совпасть с чужой мутацией.
_MTIME_BASE = 2_000_000_000          # заведомо в будущем: с реальными не пересечётся
_mtime_seq = count()


def write_mutant(path: Path, text: str) -> None:
    """Записать мутацию так, чтобы её НЕЛЬЗЯ было спутать с предыдущей."""
    path.write_text(text, encoding="utf-8")
    stamp = _MTIME_BASE + next(_mtime_seq)
    os.utime(path, (stamp, stamp))

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
     '    ("issued", "cancelled", _OWNER | _UPSELL),',
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

    # Прежняя мутация ломает список ЛИШНИМ контактом, а 14.08 случилось
    # обратное: `f63f347e` добавил персону в канон и в drop_phantom, а эту
    # копию забыл. Отказ был не «список шире канона», а «список ОТСТАЛ», и
    # ловил его сторож из другого прогона — красным он простоял сутки.
    ("gate: копия ОТСТАЛА от канона — персоны демо в ней нет",
     "chatter/payments/drill_gate.py",
     '    "8849893367:yarina",\n})',
     "})",
     "tests/test_drill_contacts_sync.py::test_every_script_resolves_the_same_drill_contacts"),

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
     '    else:\n'
     '        log.warning("route_callback: оплата без личности события (%s)", contact_id)\n'
     '        return CallbackResult(\n'
     '            feedback_html=console_text("fb_paid_no_identity", language),\n'
     '            answer=console_text("fb_paid_no_identity", language))',
     '    else:\n        dedup_key = "panel:0"',
     "tests/chatter/test_paid_action.py::test_payment_without_identity_is_refused_and_writes_nothing"),

    ("пульт: токен панели игнорируется", "chatter/notify/control_bot.py",
     'dedup_key = make_dedup_key("panel", event_token)',
     'dedup_key = make_dedup_key("panel", "fixed")',
     "tests/chatter/test_paid_action.py::test_two_panel_payments_with_different_tokens_are_two_rows"),

    ("кодек: legacy-сумма снова через float", "chatter/payments/callbacks.py",
     '        amount = from_major(raw.replace(",", ".").strip(), "USD")',
     '        amount = Money(int(float(raw.replace(",", ".").strip()) * 100), "USD")',
     "tests/chatter/test_payments_callbacks.py::test_legacy_fractional_amount"),
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

    # --- п.3: тумблер и валидатор конфига на СТАРТЕ (§5.1) -------------------

    ("settings: тумблер приводится bool()-ом", "chatter/payments/settings.py",
     "    if not isinstance(val, bool):", "    if False:",
     "tests/chatter/test_payments_settings.py::test_enabled_must_be_a_real_bool_not_a_truthy_string"),

    ("settings: кап 0 проходит (фича выключена молча)", "chatter/payments/settings.py",
     "    if val <= 0:", "    if False:",
     "tests/chatter/test_payments_settings.py::test_caps_and_due_hours_must_be_positive"),

    ("settings: порог принимает голое число", "chatter/payments/settings.py",
     '    if not isinstance(val, dict) or set(val) != {"amount", "currency"}:',
     "    if False:",
     "tests/chatter/test_payments_settings.py::test_owner_approval_above_bare_number_is_an_error"),

    ("settings: дубль id канала проходит", "chatter/payments/settings.py",
     "    if cid in seen:", "    if False:",
     "tests/chatter/test_payments_settings.py::test_duplicate_channel_id_is_an_error"),

    ("settings: неизвестный ключ блока молчит", "chatter/payments/settings.py",
     "    unknown = sorted(set(raw) - _ALLOWED_KEYS)", "    unknown = []",
     "tests/chatter/test_payments_settings.py::test_unknown_key_in_the_block_is_loud"),

    ("settings: пустое тело реквизитов считается реквизитами",
     "chatter/payments/settings.py",
     '    return bool((book.get(key) or "").strip())', "    return key in book",
     "tests/chatter/test_payments_settings.py::test_enabled_with_empty_requisites_body_fails_start"),

    ("settings: auto-канал считается рабочим в Ф0", "chatter/payments/settings.py",
     '        if c.mode == "manual"\n        and (_has_body(payments.requisites.templates, c.requisites_template)',
     '        if c.mode in CHANNEL_MODES\n        and (_has_body(payments.requisites.templates, c.requisites_template)',
     "tests/chatter/test_payments_settings.py::test_auto_channel_alone_fails_start"),

    ("settings: валидатор старта молчит всегда", "chatter/payments/settings.py",
     "    if not payments.enabled:\n        return", "    if True:\n        return",
     "tests/chatter/test_payments_settings.py::test_enabled_without_channels_fails_start"),

    ("settings: дрил-состояние проходит молча", "chatter/payments/settings.py",
     "    if not client_ready:", "    if False:",
     "tests/chatter/test_payments_settings.py::test_test_only_requisites_start_but_shout"),

    ("settings: ступень без текста объёма не ловится на старте",
     "chatter/payments/settings.py", "        if orphan:", "        if False:",
     "tests/chatter/test_payments_settings.py::test_ladder_step_without_scope_text_fails_start"),

    ("yaml_edit: правится закомментированный образец, а не живой ключ",
     "chatter/config/yaml_edit.py",
     "    target = live if live is not None else commented",
     "    target = commented if commented is not None else live",
     "tests/chatter/test_payments_toggle_command.py::test_live_key_wins_over_the_commented_sample"),

    ("loader: валидатор старта не зовётся", "chatter/config/loader.py",
     "        assert_startable(payments, slug=slug)", "        pass",
     "tests/chatter/test_payments_toggle_command.py::test_enabled_without_requisites_is_a_start_error"),

    ("пульт: включение без confirm", "chatter/telethon_run.py",
     '        if action == "on" and not confirmed:\n            return cfg_text("cfg_pay_confirm", language)',
     "        if False:\n            pass",
     "tests/chatter/test_payments_toggle_command.py::test_on_without_confirm_only_warns"),

    # --- п.4: блок счёта в промпте и порядок модель → guardrails → подстановка -

    ("prompt: оплаченный счёт продолжает висеть в промпте", "chatter/payments/prompt.py",
     '    if invoice is None or invoice.get("status") in _SILENT_STATUSES:',
     "    if invoice is None:",
     "tests/chatter/test_payments_prompt.py::test_paid_invoice_does_not_hang_in_the_prompt"),

    ("prompt: неизвестный плейсхолдер не считается нехваткой",
     "chatter/payments/prompt.py",
     '            if not str(values.get(name, "")).strip()]',
     "            if name in PLACEHOLDERS and not str(values.get(name, '')).strip()]",
     "tests/chatter/test_payments_prompt.py::test_unknown_placeholder_counts_as_missing"),

    ("prompt: плейсхолдером считается любая фигурная скобка",
     "chatter/payments/prompt.py",
     r'PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z_]*)\}")',
     r'PLACEHOLDER_RE = re.compile(r"\{([A-Za-z][A-Za-z_]*)\}")',
     "tests/chatter/test_payments_prompt.py::test_lowercase_braces_are_not_placeholders"),

    ("prompt: срока нет → тихая пустая строка", "chatter/payments/prompt.py",
     '        raise UnsubstitutedPlaceholder("срок оплаты не задан — подставить нечего")',
     '        return ""',
     "tests/chatter/test_payments_prompt.py::test_due_without_timestamp_is_an_error_not_an_empty_string"),

    ("prompt: awaiting_owner обещает срок и сумму", "chatter/payments/prompt.py",
     '    if invoice.get("status") == "awaiting_owner" or invoice.get("amount_total") is None:',
     "    if False:",
     "tests/chatter/test_payments_prompt.py::test_awaiting_owner_invoice_does_not_promise_a_deadline"),

    ("brain: блок счёта не доезжает до модели", "chatter/core/brain.py",
     "        if invoice_block:", "        if False:",
     "tests/chatter/test_payments_run_wiring.py::test_invoice_block_rides_after_the_cache_breakpoint"),

    ("run: подстановка ДО guardrails (§14 п.16)", "chatter/run.py",
     "            invoice_block=pay_block,\n        )",
     "            invoice_block=pay_block,\n        )\n        reply = finalize(reply, pay_values)",
     "tests/chatter/test_payments_run_wiring.py::test_substituted_amount_survives_the_guardrail"),

    ("run: неподставленный плейсхолдер уходит лиду", "chatter/run.py",
     '        deps.store.add_event("payments_placeholder_unresolved",\n'
     "                             contact_id=contact_id, detail=str(exc), ts=deps.clock())\n"
     "        return",
     '        deps.store.add_event("payments_placeholder_unresolved",\n'
     "                             contact_id=contact_id, detail=str(exc), ts=deps.clock())",
     "tests/chatter/test_payments_run_wiring.py::test_reply_with_an_unsubstitutable_placeholder_is_suppressed_whole"),

    ("run: проверка плейсхолдеров только при включённой фиче", "chatter/run.py",
     "        reply = finalize(reply, pay_values)",
     "        reply = finalize(reply, pay_values) if pay_values else reply",
     "tests/chatter/test_payments_run_wiring.py::test_placeholder_never_reaches_the_lead_even_with_payments_off"),

    ("control_bot: оплата не привязывается к счёту", "chatter/notify/control_bot.py",
     "        invoice_id=invoice_id, stage_no=None if invoice_id is None else 1,",
     "        invoice_id=None, stage_no=None,",
     "tests/chatter/test_payments_run_wiring.py::test_owner_tap_attaches_the_payment_to_the_open_invoice"),

    ("db: повтор тапа отвязывает оплату от счёта", "chatter/storage/db.py",
     '                "   invoice_id=COALESCE(excluded.invoice_id, payments.invoice_id),"',
     '                "   invoice_id=excluded.invoice_id,"',
     "tests/chatter/test_payments_run_wiring.py::test_two_taps_on_one_card_stay_one_payment"),

    # --- п.5: боевой конфиг volska. Конфиг проверяется как КОД ---------------

    ("volska: сетка торга уходит ниже опубликованного пола",
     "chatter/clients/volska/settings.yaml",
     "          - {amount: 750, scope_key: smm_floor}",
     "          - {amount: 700, scope_key: smm_floor}",
     "tests/chatter/test_payments_volska_config.py::test_the_ladder_never_goes_below_the_published_floor"),

    ("volska: верх вилки не опубликован в knowledge",
     "chatter/clients/volska/settings.yaml",
     "        price_range: [750, 900]", "        price_range: [750, 950]",
     "tests/chatter/test_payments_volska_config.py::test_price_bounds_are_literals_from_knowledge"),

    ("volska: заглушка потеряла пометку placeholder",
     "chatter/clients/volska/settings.yaml",
     '    smm_full: {text: "ЗАГЛУШКА: повний обсяг ведення", placeholder: true}',
     '    smm_full: {text: "ЗАГЛУШКА: повний обсяг ведення", placeholder: false}',
     "tests/chatter/test_payments_volska_config.py::test_all_scope_texts_are_marked_as_stubs_today"),

    ("volska: канал объявлен merchant'ским (mode: auto) в Ф0",
     "chatter/clients/volska/settings.yaml",
     "      mode: manual", "      mode: auto",
     "tests/chatter/test_payments_volska_config.py::test_only_manual_channels_are_declared"),

    # Прежняя мутация здесь подменяла `enabled: false` на `enabled: true` в
    # боевом yaml. Она замолчала 11.08, когда владелец законно включил оплату:
    # искомого фрагмента в файле не стало, и харнесс печатал «НЕ ПРИМЕНИЛАСЬ»
    # — то есть сторож не проверялся ВООБЩЕ, а выглядело это как одна строка
    # шума среди 92 зелёных. Мутация, привязанная к сегодняшнему значению,
    # обязана была протухнуть; эта привязана к правилу.
    ("volska: загрузка теряет значение тумблера", "chatter/payments/settings.py",
     '        enabled=_bool(raw, "enabled", False),',
     "        enabled=False,",
     "tests/chatter/test_payments_volska_config.py::test_the_config_loads_at_either_toggle_value"),

    # --- пульт коммитит тумблер (решение владельца 11.08) -------------------
    # Дороже всего здесь ТИШИНА: тумблер уже применён, и неудавшийся коммит,
    # о котором промолчали, возвращает ровно то состояние, из которого выходим.

    ("коммит: предупреждение о провале не доходит до владельца",
     "chatter/telethon_run.py",
     '        if out.ok:\n            return ""',
     '        if True:\n            return ""',
     "tests/chatter/test_toggle_commit_wiring.py::test_a_failed_commit_is_said_out_loud_in_the_reply"),

    ("коммит: причина провала не названа", "chatter/config/config_commit.py",
     "        return CommitOutcome(False, False, _tail(res))",
     '        return CommitOutcome(False, False, "коммит не прошёл")',
     "tests/chatter/test_config_commit.py::test_a_failing_hook_is_loud_and_names_the_hook_output"),

    ("коммит: вывод хука читается только из stdout", "chatter/config/config_commit.py",
     '    text = ((proc.stdout or "") + "\\n" + (proc.stderr or "")).strip()',
     '    text = (proc.stdout or "").strip()',
     "tests/chatter/test_config_commit.py::test_a_failing_hook_is_loud_and_names_the_hook_output"),

    ("коммит: забирает чужую незаконченную работу", "chatter/config/config_commit.py",
     '    res = _git_run(["commit", "-m", message, "--", str(path)], cwd=cwd)',
     '    res = _git_run(["commit", "-a", "-m", message], cwd=cwd)',
     "tests/chatter/test_config_commit.py::test_only_the_named_file_is_committed"),

    ("коммит: ложится в чужую ветку", "chatter/config/config_commit.py",
     "    if current != trunk:", "    if False:",
     "tests/chatter/test_config_commit.py::test_a_tree_off_the_trunk_refuses_to_commit"),

    ("коммит: незавершённый merge не замечается", "chatter/config/config_commit.py",
     "        if (gd / marker).exists():", "        if False:",
     "tests/chatter/test_config_commit.py::test_an_unfinished_merge_is_loud_and_leaves_the_tree_dirty"),

    ("коммит: «нечего коммитить» считается провалом (ложная тревога)",
     "chatter/config/config_commit.py",
     "        if any(marker in blob for marker in _NOTHING):", "        if False:",
     "tests/chatter/test_config_commit.py::test_no_change_is_success_and_silence"),

    ("коммит: копия папки вне репозитория считается провалом",
     "chatter/config/config_commit.py",
     "        if any(marker in err for marker in _NOT_A_REPO):", "        if False:",
     "tests/chatter/test_config_commit.py::test_a_config_outside_any_repository_is_silent_success"),

    ("коммит: пропавший git проглатывается как успех",
     "chatter/config/config_commit.py",
     '        return CommitOutcome(False, False, "git не запустился (нет бинаря или таймаут)")',
     "        return CommitOutcome(True, False)",
     "tests/chatter/test_config_commit.py::test_git_missing_is_loud_not_silent"),

    # --- проводка в диалог: обе операции §8.2 -------------------------------
    # Здесь ломается не расчёт, а ЗВЕНО. Каждая мутация отвечает на вопрос
    # «если убрать это, путь всё ещё работает?» — и если сторож остаётся
    # зелёным, значит путь он не охранял, а только его кусок.

    ("предпасс: окно количества расширено до года", "chatter/payments/intent.py",
     "_QTY_WINDOW = 2", "_QTY_WINDOW = 5",
     "tests/chatter/test_payments_intent.py::test_a_number_far_from_the_alias_is_not_a_quantity"),

    ("предпасс: граница правдоподобного количества снята",
     "chatter/payments/intent.py",
     "        return n if 1 <= n <= _MAX_QTY else None", "        return n",
     "tests/chatter/test_payments_intent.py::test_a_year_right_next_to_the_alias_is_still_not_a_quantity"),

    ("предпасс: пара основ упрощена до одной (ложная готовность платить)",
     "chatter/payments/intent.py",
     "        any(all(_has(tokens, stem) for stem in pair) for pair in _READY_PAIRS)",
     "        any(any(_has(tokens, stem) for stem in pair) for pair in _READY_PAIRS)",
     "tests/chatter/test_payments_intent.py::test_ordinary_talk_triggers_neither_operation"),

    ("предпасс: пустой ход считается разобранным", "chatter/payments/intent.py",
     "        return Intent(False, False, QuoteRequest(parsed=False))",
     "        return Intent(False, False, QuoteRequest(parsed=True))",
     "tests/chatter/test_payments_intent.py::test_empty_text_parses_to_nothing_rather_than_to_something"),

    ("предпасс: клиент без прайса считается разобранным",
     "chatter/payments/intent.py",
     "        return Intent(asks_price, wants_invoice, QuoteRequest(parsed=False))",
     "        return Intent(asks_price, wants_invoice, QuoteRequest(parsed=True))",
     "tests/chatter/test_payments_intent.py::test_pricing_absent_means_no_facts_at_all"),

    ("предпасс: порядок позиций взят из конфига, а не из сообщения",
     "chatter/payments/intent.py",
     "    return tuple(found.values())",
     "    return tuple(sorted(found.values(), key=lambda it: it.position_id))",
     "tests/chatter/test_payments_intent.py::test_items_are_ordered_by_the_message_not_by_the_config"),

    # --- готовность КУПИТЬ, а не только заплатить (прогон Т1 11.08) ---------

    ("предпасс: готовность заказать выпала из списка",
     "chatter/payments/intent.py",
     '    ("готов", "замов"), ("готов", "заказ"),\n', "",
     "tests/chatter/test_payments_intent.py"
     "::test_readiness_to_buy_is_recognised_not_only_as_readiness_to_pay"),

    ("предпасс: форма первого лица «закажем» снова не ловится",
     "chatter/payments/intent.py",
     '    ("давайте", "замов"), ("давайте", "заказ"), ("давайте", "закаж"),',
     '    ("давайте", "замов"), ("давайте", "заказ"),',
     "tests/chatter/test_payments_intent.py"
     "::test_readiness_to_buy_is_recognised_not_only_as_readiness_to_pay"),

    ("предпасс: одиночная словоформа упрощена до основы (счёт деепричастию)",
     "chatter/payments/intent.py",
     "        or (any(t in _READY_WORDS for t in tokens)",
     "        or (any(_has(tokens, t) for t in _READY_WORDS)",
     "tests/chatter/test_payments_intent.py"
     "::test_one_word_signal_does_not_fire_on_look_alike_phrases"),

    ("предпасс: вето снято — «беру паузу» стало покупкой",
     "chatter/payments/intent.py",
     "            and not any(_has(tokens, stem) for stem in _READY_WORD_VETO)))",
     "            and True))",
     "tests/chatter/test_payments_intent.py"
     "::test_one_word_signal_does_not_fire_on_look_alike_phrases"),

    # --- валюта в лицо лиду ровно один раз ----------------------------------

    ("подстановка: знак валюты ПЕРЕД суммой больше не снимается",
     "chatter/payments/prompt.py",
     '    text = _SIGN_BEFORE_AMOUNT.sub("", text or "")',
     '    text = text or ""',
     "tests/chatter/test_payments_prompt.py"
     "::test_currency_symbol_next_to_the_amount_is_dropped"),

    ("подстановка: знак валюты ПОСЛЕ суммы больше не снимается",
     "chatter/payments/prompt.py",
     '    return _SIGN_AFTER_AMOUNT.sub("", text)',
     "    return text",
     "tests/chatter/test_payments_prompt.py"
     "::test_currency_symbol_next_to_the_amount_is_dropped"),

    ("подстановка: чистка валюты бьёт по всей фразе, а не по соседу суммы",
     "chatter/payments/prompt.py",
     '_SIGN_BEFORE_AMOUNT = re.compile(f"[{_CURRENCY_SIGNS}]" + _SPACE + r"(?=\\{AMOUNT\\})")',
     '_SIGN_BEFORE_AMOUNT = re.compile(f"[{_CURRENCY_SIGNS}]" + _SPACE)',
     "tests/chatter/test_payments_prompt.py"
     "::test_currency_symbol_away_from_the_amount_is_left_alone"),

    # --- у долга по счёту один хозяин, и это код (дубль 12.08) --------------

    ("слот: гард снят — классификатор снова заводит свой долг по счёту",
     "chatter/core/obligations_slot.py",
     "        if (kind == \"other\" and invoice_debt_open\n"
     "                and model_okey(u) not in existing_okeys):",
     "        if False:",
     "tests/chatter/test_obligations_slot.py"
     "::test_model_may_not_open_other_while_an_invoice_debt_is_open"),

    ("слот: гард ослеп — ищет долг счёта не по тому префиксу",
     "chatter/core/obligations_slot.py",
     '_INVOICE_OKEY_PREFIX = "other:inv-"',
     '_INVOICE_OKEY_PREFIX = "other:invoice-"',
     "tests/chatter/test_obligations_slot.py"
     "::test_model_may_not_open_other_while_an_invoice_debt_is_open"),

    ("слот: гард глушит other НАВСЕГДА, а не пока счёт открыт",
     "chatter/core/obligations_slot.py",
     "        o.status == \"open\" and o.okey.startswith(_INVOICE_OKEY_PREFIX)",
     "        o.okey.startswith(_INVOICE_OKEY_PREFIX)",
     "tests/chatter/test_obligations_slot.py"
     "::test_a_settled_invoice_debt_no_longer_blocks_the_model"),

    ("слот: гард запрещает и ВЕСТИ уже заведённый other, не только заводить",
     "chatter/core/obligations_slot.py",
     "        if (kind == \"other\" and invoice_debt_open\n"
     "                and model_okey(u) not in existing_okeys):",
     "        if kind == \"other\" and invoice_debt_open:",
     "tests/chatter/test_obligations_slot.py"
     "::test_model_may_still_close_an_other_it_already_owns"),

    ("слот: вывод ключа модели игнорирует slug (гард бьёт мимо строки)",
     "chatter/core/obligations_slot.py",
     "    slug = upd.get(\"slug\")\n"
     "    if kind == \"other\" and not slug:",
     "    slug = None\n"
     "    if kind == \"other\" and not slug:",
     "tests/chatter/test_obligations_slot.py"
     "::test_model_may_still_close_an_other_it_already_owns"),

    ("оплата: долг по счёту не закрывается деньгами",
     "chatter/notify/control_bot.py",
     "        if settled is not None and is_settled(settled[\"status\"]):",
     "        if False:",
     "tests/chatter/test_payments_run_wiring.py"
     "::test_payment_closes_the_code_owned_invoice_debt"),

    ("оплата: долг закрывает сам ТАП, а не деньги (недоплата снимает долг)",
     "chatter/notify/control_bot.py",
     "        if settled is not None and is_settled(settled[\"status\"]):",
     "        if settled is not None:",
     "tests/chatter/test_payments_run_wiring.py"
     "::test_a_partially_paid_invoice_keeps_the_debt_open"),

    # --- сброс дрил-контакта чистит и деньги --------------------------------

    ("сброс: денежные таблицы снова переживают дрил",
     "scripts/drill_reset.py",
     '                "quotes", "invoices", "payments")',
     "                )",
     "tests/test_drill_reset.py::test_money_tables_are_wiped_for_the_drill_contact"),

    ("сброс: ступени счёта осиротели (чистятся ПОСЛЕ счетов)",
     "scripts/drill_reset.py",
     "    for table in _WIPE_BY_INVOICE:\n"
     "        conn.execute(f\"DELETE FROM {table} {_BY_INVOICE_WHERE}\", (contact,))\n"
     "    for table in _WIPE_TABLES:",
     "    for table in _WIPE_TABLES:",
     "tests/test_drill_reset.py::test_invoice_stages_are_wiped_through_their_invoice"),

    ("сброс: план молчит про денежные таблицы (стираем втихую)",
     "scripts/drill_reset.py",
     '        print(f"\\nсбрасываю: {\', \'.join((*_WIPE_TABLES, *_WIPE_BY_INVOICE))}"',
     '        print(f"\\nсбрасываю: {\', \'.join(_WIPE_TABLES[:5])}"',
     "tests/test_drill_reset.py::test_plan_names_the_money_tables"),

    # --- публичные ступени объёма (решение владельца 12.08) -----------------

    ("ярусы: сетка торга снова уживается со ступенями объёма",
     "chatter/payments/pricing.py",
     '    for forbidden in ("ladder", "price_range"):',
     "    for forbidden in ():",
     "tests/chatter/test_payments_pricing.py::test_ladder_next_to_tiers_is_a_start_error"),

    ("ярусы: одна ступень считается выбором объёма",
     "chatter/payments/pricing.py",
     "    if not isinstance(raw_tiers, (list, tuple)) or len(raw_tiers) < 2:",
     "    if not isinstance(raw_tiers, (list, tuple)) or len(raw_tiers) < 1:",
     "tests/chatter/test_payments_pricing.py::test_a_single_tier_is_not_a_choice"),

    ("ярусы: порядок ступеней перестал быть строгим",
     "chatter/payments/pricing.py",
     "        if prev is not None and amount.minor <= prev:",
     "        if False:",
     "tests/chatter/test_payments_pricing.py::test_tiers_must_strictly_ascend"),

    ("ярусы: цена ступени больше не сверяется с knowledge (правило №5)",
     "chatter/payments/pricing.py",
     "        if not _literal_in_knowledge(amount, ccy, knowledge):\n"
     "            raise PricingConfigError(\n"
     "                f\"позиция {pid!r}, ступень {tid!r}: цена отсутствует в knowledge \"",
     "        if False:\n"
     "            raise PricingConfigError(\n"
     "                f\"позиция {pid!r}, ступень {tid!r}: цена отсутствует в knowledge \"",
     "tests/chatter/test_payments_pricing.py"
     "::test_every_tier_amount_must_be_published_in_knowledge"),

    ("ярусы: при невыбранном объёме стартуем с ДЕШЁВОЙ ступени",
     "chatter/payments/pricing.py",
     "        if self.tiers:\n            top = self.tiers[-1]",
     "        if self.tiers:\n            top = self.tiers[0]",
     "tests/chatter/test_payments_pricing.py"
     "::test_the_top_offer_of_a_tiered_position_is_its_priciest_tier"),

    ("ярусы: неузнанная ступень молча игнорируется вместо владельца",
     "chatter/payments/complexity.py",
     "        elif (item.tier_id is not None\n"
     "              and pricing.positions[item.position_id].tier(item.tier_id) is None):\n"
     "            found.append(\"unknown_tier\")",
     "        elif False:\n"
     "            found.append(\"unknown_tier\")",
     "tests/chatter/test_payments_complexity.py::test_an_unknown_tier_calls_the_owner"),

    ("ярусы: счёт выставляется по ВЕРХНЕЙ ступени, а не по выбранной",
     "chatter/payments/dialogue.py",
     "                amount, scope_key = chosen.amount, chosen.tier_text_key",
     "                amount, scope_key = position.top.amount, chosen.tier_text_key",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_the_invoice_is_issued_by_the_chosen_tier"),

    ("ярусы: оговорка требуется и при выбранном объёме (ложь про точную цену)",
     "chatter/payments/dialogue.py",
     "                requires_disclaimer = chosen is None",
     "                requires_disclaimer = True",
     "tests/chatter/test_payments_tiers_dialogue.py::test_a_chosen_tier_needs_no_caveat"),

    ("ярусы: guardrail оговорки снова не знает про {TIERS}",
     "chatter/payments/prompt.py",
     '_MONEY_PLACEHOLDERS: tuple[str, ...] = ("{AMOUNT}", "{TIERS}")',
     '_MONEY_PLACEHOLDERS: tuple[str, ...] = ("{AMOUNT}",)',
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_a_reply_listing_prices_without_a_caveat_is_suppressed"),

    ("ярусы: заглушка описания объёма уходит живому лиду",
     "chatter/payments/tier_texts.py",
     "        guard_test_asset(\n"
     "            contact_id=contact_id,\n"
     "            what=\"заглушки описаний объёма [\" + \", \".join(stubs) + \"]\")",
     "        pass",
     "tests/chatter/test_payments_tier_texts.py"
     "::test_stubs_block_a_live_contact_but_not_the_drill"),

    ("ярусы: выбор объёма перестал быть денежным ходом",
     "chatter/payments/dialogue.py",
     "            intent.wants_invoice or intent.asks_price or tier_now):",
     "            intent.wants_invoice or intent.asks_price):",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_moving_up_a_tier_is_a_new_quote_and_supersedes_the_old"),

    ("ярусы: ступень наследуется от котировки вместо чтения из хода",
     "chatter/payments/dialogue.py",
     "    tier_id = read_tier(text, position) if position is not None else None",
     "    tier_id = None",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_moving_up_a_tier_is_a_new_quote_and_supersedes_the_old"),

    # --- апселл по выставленному счёту (решение владельца 12.08) ------------

    ("апселл: счёт с ПРИШЕДШИМИ деньгами снимается ботом",
     "chatter/payments/dialogue.py",
     '    if store.received_minor(invoice["invoice_id"]) > 0:\n'
     '        return "money_received"',
     "    if False:\n"
     '        return "money_received"',
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_the_reason_shown_to_the_owner_is_the_money_not_the_status"),

    ("апселл: ступень ВНИЗ проходит как апселл (бот раздаёт скидки)",
     "chatter/payments/dialogue.py",
     "    if total is None or new_amount.minor <= int(total):",
     "    if total is None:",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_a_cheaper_tier_after_the_invoice_goes_to_the_owner"),

    ("апселл: та же ступень заново выставляет второй счёт",
     "chatter/payments/dialogue.py",
     "    if total is None or new_amount.minor <= int(total):",
     "    if total is None or new_amount.minor < int(total):",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_the_same_tier_again_changes_nothing"),

    ("апселл: счёт НЕ в issued тоже снимается",
     "chatter/payments/dialogue.py",
     '    if invoice["status"] != "issued":\n        return "not_issued"',
     "    if False:\n        return \"not_issued\"",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_an_invoice_awaiting_the_owner_is_not_replaced_by_the_bot"),

    ("апселл: причина отмены не записывается",
     "chatter/storage/db.py",
     '        if not (reason or "").strip():\n'
     '            raise ValueError("отмена счёта без причины запрещена")',
     "        reason = reason or \"\"",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_cancelling_without_a_reason_is_refused"),

    ("апселл: право отмены не проверяется по карте переходов",
     "chatter/storage/db.py",
     '        assert_transition(inv["status"], "cancelled", actor)',
     "        pass",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_cancelling_checks_the_right_by_the_transition_map"),

    ("апселл: право снимать счёт отдано системе целиком",
     "chatter/payments/statuses.py",
     '    ("issued", "cancelled", _OWNER | _UPSELL),',
     '    ("issued", "cancelled", _OWNER | _UPSELL | _SYSTEM),',
     "tests/chatter/test_payments_statuses.py"
     "::test_only_the_owner_and_the_upsell_may_cancel_an_issued_invoice"),

    ("апселл: долг снятого счёта остаётся открытым навсегда",
     "chatter/payments/dialogue.py",
     "                _close_obligation(store, contact_id, invoice[\"invoice_id\"],\n"
     "                                  now=now, msg_id=msg_id)",
     "                pass",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_the_debt_of_the_replaced_invoice_is_closed"),

    ("апселл: новый счёт после замены не выставляется (лид остался без счёта)",
     "chatter/payments/dialogue.py",
     "            if intent.wants_invoice or reissue:",
     "            if intent.wants_invoice:",
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_upselling_an_untouched_invoice_replaces_it"),

    ("отмена: cancelled снова считается проекцией от сумм",
     "chatter/payments/model.py",
     '_NOT_MONEY = frozenset({"draft", "awaiting_owner", "cancelled",',
     '_NOT_MONEY = frozenset({"draft", "awaiting_owner",',
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_recompute_leaves_a_cancelled_invoice_alone"),

    ("отмена: снятый счёт снова виден как открытый",
     "chatter/payments/prompt.py",
     '_SILENT_STATUSES = frozenset({"paid", "cancelled", "refunded", "refund_requested"})',
     '_SILENT_STATUSES = frozenset({"paid", "refunded", "refund_requested"})',
     "tests/chatter/test_payments_tiers_dialogue.py"
     "::test_a_lone_cancelled_invoice_leaves_no_open_one"),

    # --- панели: вход, имена, подтверждение (12.08) -------------------------

    ("панель: ручка входа не проверяет ключ вовсе",
     "app/routers/panels_auth.py",
     "    if not key.strip() or not _same(key, _expected()):",
     "    if False:",
     "tests/chatter/test_panels_web.py::test_a_wrong_key_is_refused_and_sets_nothing"),

    ("панель: cookie входа доступна скрипту (HttpOnly снят)",
     "app/routers/panels_auth.py", "        httponly=True,", "        httponly=False,",
     "tests/chatter/test_panels_web.py"
     "::test_the_cookie_is_httponly_samesite_strict_and_panel_scoped"),

    ("панель: cookie уходит на чужие сайты (SameSite ослаблен)",
     "app/routers/panels_auth.py", '        samesite="strict",', '        samesite="lax",',
     "tests/chatter/test_panels_web.py"
     "::test_the_cookie_is_httponly_samesite_strict_and_panel_scoped"),

    ("панель: открытый редирект с ручки входа",
     "app/routers/panels_auth.py",
     "    target = _safe_next(str(form.get(\"next\") or _NEXT_DEFAULT))",
     "    target = str(form.get(\"next\") or _NEXT_DEFAULT)",
     "tests/chatter/test_panels_web.py::test_the_redirect_target_cannot_be_an_arbitrary_site"),

    ("панель: вход работает при выключенных панелях",
     "app/routers/panels_auth.py",
     # Логика переехала в отдельный `_require_enabled()` (её зовут и страница
     # формы, и её приём). Мутируем ИМЕННО тело: пустой ключ перестаёт быть
     # причиной отказа, и обе двери открываются при выключенных панелях.
     "    if not _expected():\n"
     "        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,\n"
     '                            "panels disabled: JARVIS_PANELS_KEY not set")',
     "    if False:\n"
     "        pass",
     "tests/chatter/test_panels_web.py"
     "::test_the_login_route_itself_is_dead_while_panels_are_disabled"),

    ("панель: сравнение ключа вернулось на строки (не-ASCII = 500)",
     "app/routers/panels_auth.py",
     "    return secrets.compare_digest(\n"
     '        provided.strip().encode("utf-8"), expected.encode("utf-8"))',
     "    return secrets.compare_digest(provided.strip(), expected)",
     "tests/chatter/test_panels_web.py::test_a_non_ascii_key_is_a_refusal_not_a_crash"),

    ("панель: одиночный POST снова взводит глобальную заглушку",
     "app/routers/tamapi_dashboard.py",
     # Возвращаем ИМЕННО прежнее поведение: снимаем и гард, и требование
     # подтверждения. Мутация, ломающая только маршрут, оставила бы тест
     # зелёным по другой причине — «неизвестное действие ничего не делает».
     '    if data == "stop_all":\n'
     "        return JSONResponse({\n"
     '            "confirm": True,\n'
     '            "feedback": "Зупинити бота ВСІМ лідам? Підтвердіть ще раз.",\n'
     "        })\n"
     '    if data == "stop_all confirm":',
     '    if data == "stop_all":',
     "tests/chatter/test_panels_web.py::test_a_single_post_does_not_arm_the_global_mute"),

    ("панель: подтверждение stop_all требуется и на возврат из паузы",
     "app/routers/tamapi_dashboard.py",
     '    if data == "resume_all":',
     '    if data == "resume_all confirm":',
     "tests/chatter/test_panels_web.py::test_resume_needs_no_confirmation"),

    ("панель: имя лида не показывается, снова голый id",
     "app/services/tamapi_metrics.py",
     '    return (display_name or "").strip() or contact_id.split(":", 1)[0]',
     '    return contact_id.split(":", 1)[0]',
     "tests/chatter/test_panels_web.py::test_the_feed_shows_the_name_when_it_is_known"),

    ("панель: пустое имя затирает известное",
     "chatter/storage/db.py",
     "        clean = (name or \"\").strip()\n        if not clean:\n            return",
     "        clean = (name or \"\").strip()",
     "tests/chatter/test_payments_store.py::test_the_name_is_not_overwritten_by_a_blank"),

    ("панель: неузнанный отправитель затирает имя",
     "chatter/telethon_run.py",
     "        if not name:\n            return          # не узнали ничего",
     "        if not name:\n            name = str(user_id)  # не узнали ничего",
     "tests/chatter/test_panels_web.py::test_an_unknown_sender_does_not_erase_a_known_name"),

    ("панель: подпись «за оцінкою асистента» пропала",
     "app/routers/tamapi_dashboard.py",
     '("Кваліфіковано", f["qualified"], "за оцінкою асистента", ""),',
     '("Кваліфіковано", f["qualified"], "", ""),',
     "tests/chatter/test_panels_web.py"
     "::test_qualified_is_labelled_as_the_assistants_opinion"),

    ("панель: пакет тихо сменил величину",
     "app/routers/tamapi_dashboard.py",
     'limit = int(os.getenv("TAMAPI_PACKAGE", "500"))',
     'limit = int(os.getenv("TAMAPI_PACKAGE", "1000"))',
     "tests/chatter/test_panels_web.py::test_the_package_limit_is_five_hundred_by_default"),

    ("прайс: многословный алиас проходит и не совпадает никогда",
     "chatter/payments/pricing.py", "        if len(alias.split()) > 1:",
     "        if False:",
     "tests/chatter/test_payments_pricing.py::test_a_multiword_alias_is_an_error_not_a_dead_string"),

    ("volska: позиция осталась без алиасов (тихо неузнаваема)",
     "chatter/clients/volska/settings.yaml",
     '        aliases: ["логотип", "лого", "logo"]\n', "",
     "tests/chatter/test_payments_volska_config.py::test_every_position_has_aliases"),

    ("volska: алиас с опечаткой ведёт в чужую позицию",
     "chatter/clients/volska/settings.yaml",
     '        aliases: ["рефайн", "редизайн"]', '        aliases: ["логотип"]',
     "tests/chatter/test_payments_volska_config.py::test_each_alias_resolves_back_to_its_own_position"),

    ("проводка: счёт выставляется без реквизитов", "chatter/payments/dialogue.py",
     "    if invoice is None and instruction is not None and (",
     "    if invoice is None and (",
     "tests/chatter/test_payments_dialogue.py::test_no_requisites_means_no_invoice_at_all"),

    ("проводка: пригодность прайса не проверяется (заглушка живому лиду)",
     "chatter/payments/dialogue.py",
     "        assert_pricing_usable(payments.pricing, payments.scope_texts,\n"
     "                              contact_id=contact_id)",
     "        pass",
     "tests/chatter/test_payments_dialogue.py::test_stub_scope_texts_stop_the_quote_for_a_live_lead"),

    ("проводка: вопрос цены сразу выставляет счёт", "chatter/payments/dialogue.py",
     "            if intent.wants_invoice or reissue:", "            if True:",
     "tests/chatter/test_payments_dialogue.py::test_price_question_quotes_without_an_invoice_and_demands_the_disclaimer"),

    ("проводка: оговорка перестаёт требоваться", "chatter/payments/dialogue.py",
     "                requires_disclaimer = chosen is None",
     "                requires_disclaimer = False",
     "tests/chatter/test_payments_dialogue.py::test_price_question_quotes_without_an_invoice_and_demands_the_disclaimer"),

    ("проводка: сумма берётся с ПОЛА сетки, а не с верха вилки",
     "chatter/payments/dialogue.py",
     "                top = position.top       # price_upper",
     "                top = position.floor     # price_upper",
     "tests/chatter/test_payments_path_e2e.py::test_ready_lead_turn_creates_the_invoice_object_itself"),

    ("проводка: счёт не привязан к котировке", "chatter/payments/dialogue.py",
     'quote_id=quote["quote_id"],', "quote_id=None,",
     "tests/chatter/test_payments_path_e2e.py::test_ready_lead_turn_creates_the_invoice_object_itself"),

    ("проводка: снапшот отправленной инструкции не пишется",
     "chatter/payments/dialogue.py",
     "                    instruction_snapshot=instruction.body_text)",
     "                    instruction_snapshot=None)",
     "tests/chatter/test_payments_path_e2e.py::test_the_invoice_carries_the_snapshot_of_what_was_actually_sent"),

    ("проводка: долг оплаты повешен на бота", "chatter/payments/dialogue.py",
     '        [{"kind": "other", "owed_by": "client", "status": "open",',
     '        [{"kind": "other", "owed_by": "bot", "status": "open",',
     "tests/chatter/test_payments_path_e2e.py::test_the_invoice_turn_puts_the_debt_on_the_client"),

    ("проводка: ключ обязательства без номера счёта (второй затрёт первый)",
     "chatter/payments/dialogue.py", '"slug": invoice_slug(invoice_id)',
     '"slug": "inv"',
     "tests/chatter/test_payments_path_e2e.py::test_the_invoice_turn_puts_the_debt_on_the_client"),

    ("проводка: позиция угадывается при пустом разборе",
     "chatter/payments/dialogue.py",
     "    if request.items:\n        return request", "    if True:\n        return request",
     "tests/chatter/test_payments_dialogue.py::test_readiness_without_a_service_falls_back_to_the_active_quote"),

    ("проводка: причина ухода к владельцу теряется", "chatter/payments/dialogue.py",
     "                       else (outcome.why,))", "                       else ())",
     "tests/chatter/test_payments_dialogue.py::test_the_owner_note_names_why_the_amount_was_withheld"),

    ("срок: не прижимается к рабочим часам клиента", "chatter/payments/prompt.py",
     "    if dt.hour < start:", "    if False:",
     "tests/chatter/test_payments_dialogue.py::test_due_landing_before_the_working_day_moves_to_its_start"),

    ("срок: остаётся с минутами («до 10:06 у неділю»)", "chatter/payments/prompt.py",
     "    dt = dt.replace(minute=0, second=0, microsecond=0)",
     "    dt = dt.replace(second=0, microsecond=0)",
     "tests/chatter/test_payments_dialogue.py::test_due_is_a_round_hour_inside_the_working_day"),

    ("блок А: инструкция про реквизиты не доезжает до модели",
     "chatter/payments/prompt.py", "    if not channels:\n        return \"\"",
     "    if True:\n        return \"\"",
     "tests/chatter/test_payments_path_e2e.py::test_lead_asking_where_to_pay_gets_the_requisites_in_the_same_turn"),

    ("оговорка: узнаётся в любом тексте", "chatter/payments/prompt.py",
     "    return any(marker in low for marker in DISCLAIMER_MARKERS)",
     "    return True",
     "tests/chatter/test_payments_path_e2e.py::test_an_amount_without_the_disclaimer_never_reaches_the_lead"),

    ("оговорка: не узнаётся никогда (глушит и корректный ответ)",
     "chatter/payments/prompt.py",
     "    return any(marker in low for marker in DISCLAIMER_MARKERS)",
     "    return False",
     "tests/chatter/test_payments_path_e2e.py::test_the_same_amount_with_the_disclaimer_goes_through"),

    ("run: ответ без оговорки уходит лиду", "chatter/run.py",
     '        deps.store.add_event("payments_disclaimer_missing", contact_id=contact_id,\n'
     "                             detail=reply[:120], ts=deps.clock())\n        return",
     '        deps.store.add_event("payments_disclaimer_missing", contact_id=contact_id,\n'
     "                             detail=reply[:120], ts=deps.clock())",
     "tests/chatter/test_payments_path_e2e.py::test_an_amount_without_the_disclaimer_never_reaches_the_lead"),

    ("run: карточка счёта владельцу не уходит", "chatter/run.py",
     "    if pay.owner_note is not None:", "    if False:",
     "tests/chatter/test_payments_path_e2e.py::test_awaiting_owner_invoice_reaches_the_owner_as_a_card"),

    ("run: деньги считаются ДО лимитов", "chatter/run.py",
     "    limits = deps.cfg.settings.limits\n"
     "    if not within_hourly_limit(deps.store, contact_id, now=deps.clock(), limit=limits.per_contact_hourly):\n"
     '        print(f"  [rate limit] hourly limit hit for {contact_id}; skipping")\n'
     "        return",
     "    pay = _payment_context(deps, contact_id, text=text)\n"
     "    limits = deps.cfg.settings.limits\n"
     "    if not within_hourly_limit(deps.store, contact_id, now=deps.clock(), limit=limits.per_contact_hourly):\n"
     '        print(f"  [rate limit] hourly limit hit for {contact_id}; skipping")\n'
     "        return",
     "tests/chatter/test_payments_path_e2e.py::test_a_rate_limited_turn_creates_no_money_object"),

    # --- гейт сторожит сам себя -------------------------------------------
    # Единственная мутация, которая ломает не продукт, а проверку продукта.
    # Без неё возврат к «просто write_text» прошёл бы незамеченным, и гейт
    # снова начал бы подтверждать то, чего не проверял.

    ("гейт: мутация пишется без уникального mtime (исполнится чужой байткод)",
     "scripts/mutate_payments_phase0.py",
     "    stamp = _MTIME_BASE + next(_mtime_seq)\n    os.utime(path, (stamp, stamp))",
     "    return",
     "tests/test_mutation_harness.py::test_every_write_gets_its_own_mtime"),

    ("пульт: файл не откатывается на упавшей валидации", "chatter/telethon_run.py",
     '            path.write_text(old, encoding="utf-8")   # вернуть заведомо рабочий файл\n'
     '            self.reload_configs()\n'
     '            return cfg_text("cfg_pay_fail", language, reason=err)',
     '            return cfg_text("cfg_pay_fail", language, reason=err)',
     "tests/chatter/test_payments_toggle_command.py::test_toggle_is_guarded_by_the_same_validator_as_start"),
]


def run(test: str) -> int:
    """Код возврата pytest. 0 = зелёный, 1 = красный, ОСТАЛЬНОЕ = харнесс врёт.

    Второй способ соврать (13.08): мутация целится в ПЕРЕИМЕНОВАННЫЙ тест.
    pytest не находит узел, выходит с 4 — и прежнее `returncode == 0` читало
    это как «сторож покраснел». Две мутации cookie панели (HttpOnly, SameSite)
    отчитывались `[ok]` с 12.08, ни разу не выполнив ни одной проверки.
    Поэтому «не 0» больше не значит «красный»: красный — это РОВНО 1.
    """
    p = subprocess.run([sys.executable, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode


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
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + out)


def main() -> int:
    refuse_if_live_tree(ROOT)
    assert_clean()
    blind = []
    for name, rel, old, new, test in MUTATIONS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            print(f"[!] МУТАЦИЯ НЕ ПРИМЕНИЛАСЬ: {name} — искомый фрагмент не найден")
            blind.append((name, "фрагмент не найден"))
            continue
        write_mutant(path, text.replace(old, new, 1))
        try:
            rc = run(test)
        finally:
            revert(rel)
        if rc == 0:
            print(f"[СЛЕП] {name}\n        {test} остался ЗЕЛЁНЫМ")
            blind.append((name, test))
        elif rc == 1:
            print(f"[ok]   {name} → сторож покраснел")
        else:
            # 4 = узла нет (переименовали тест), 5 = ничего не собралось,
            # 2/3 = сам pytest сломался. Ни одно из этого не «поймал».
            print(f"[ВРЁТ] {name}\n        {test} — pytest вышел с {rc}, "
                  f"проверка НЕ выполнялась")
            blind.append((name, f"{test} (pytest rc={rc})"))
    print()
    if blind:
        print(f"СЛЕПЫХ СТОРОЖЕЙ: {len(blind)} из {len(MUTATIONS)}")
        return 1
    print(f"Все {len(MUTATIONS)} мутаций пойманы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
