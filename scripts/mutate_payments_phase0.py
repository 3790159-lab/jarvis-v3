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
     "tests/chatter/test_payments_volska_config.py::test_volska_loads_with_payments_off"),

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

    ("volska: фича включена прямо в файле", "chatter/clients/volska/settings.yaml",
     "  enabled: false", "  enabled: true",
     "tests/chatter/test_payments_volska_config.py::test_volska_loads_with_payments_off"),

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
     "            if intent.wants_invoice:", "            if True:",
     "tests/chatter/test_payments_dialogue.py::test_price_question_quotes_without_an_invoice_and_demands_the_disclaimer"),

    ("проводка: оговорка перестаёт требоваться", "chatter/payments/dialogue.py",
     "                requires_disclaimer = True", "                requires_disclaimer = False",
     "tests/chatter/test_payments_dialogue.py::test_price_question_quotes_without_an_invoice_and_demands_the_disclaimer"),

    ("проводка: сумма берётся с ПОЛА сетки, а не с верха вилки",
     "chatter/payments/dialogue.py", "            step = position.top",
     "            step = position.floor",
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
     "chatter/payments/dialogue.py", '"slug": f"inv-{invoice_id}"', '"slug": "inv"',
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

    ("пульт: файл не откатывается на упавшей валидации", "chatter/telethon_run.py",
     '            path.write_text(old, encoding="utf-8")   # вернуть заведомо рабочий файл\n'
     '            self.reload_configs()\n'
     '            return cfg_text("cfg_pay_fail", language, reason=err)',
     '            return cfg_text("cfg_pay_fail", language, reason=err)',
     "tests/chatter/test_payments_toggle_command.py::test_toggle_is_guarded_by_the_same_validator_as_start"),
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
            "ОТКАЗ: рабочее дерево грязное — откат мутаций сотрёт эти правки.\n"
            "Закоммить их и повтори прогон:\n" + out)


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
