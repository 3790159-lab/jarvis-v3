# -*- coding: utf-8 -*-
"""Unit tests for the menu-audit library (app/services/audit/menu_audit.py).

Pure functions, $0, no bot, no network. See spec:
docs/superpowers/specs/2026-07-06-menu-audit-design.md §2.
"""
import types

from app.services.audit import menu_audit as ma


def _mod(name, code):
    m = types.ModuleType(name)
    exec(compile(code, name, "exec"), m.__dict__)
    return m


# ── Ось A: статический AST-разбор роутинга handle_command ───────────────────
def test_routed_commands_collects_eq_literals():
    src = '''
def handle_command(chat_id, cmd, query, state):
    if cmd == "/foo":
        do_foo()
    elif cmd == "/bar":
        do_bar()
'''
    exact, prefixes = ma.routed_commands(src)
    assert "/foo" in exact and "/bar" in exact
    assert prefixes == set()


def test_routed_commands_collects_in_tuple_and_startswith():
    src = '''
def handle_command(chat_id, cmd, query, state):
    if cmd in ("/browse_check", "/browse_watch"):
        _browse_dispatch(chat_id, cmd, query)
    if cmd.startswith("/swapbatch"):
        _swapbatch_dispatch(chat_id, cmd)
'''
    exact, prefixes = ma.routed_commands(src)
    assert {"/browse_check", "/browse_watch"} <= exact
    assert "/swapbatch" in prefixes


def test_routed_commands_collects_dispatch_dict_keys():
    # real control file routes many commands via a dict table, not if/elif
    src = '''
def route(chat_id, cmd):
    table = {
        "/faceswap": lambda: handle_faceswap_start(chat_id),
        "/me_roles": lambda: handle_me_roles(chat_id),
    }
    table[cmd]()
'''
    exact, _ = ma.routed_commands(src)
    assert {"/faceswap", "/me_roles"} <= exact


def test_routed_commands_ignores_dict_with_constant_values():
    # a label/intent map (string values) NOT used as a cmd-membership target and
    # not a callable dispatch table → keys not routed
    src = '''
LABELS = {"/faceswap": "замена лиц"}
'''
    exact, _ = ma.routed_commands(src)
    assert "/faceswap" not in exact


def test_routed_commands_dict_used_as_cmd_membership_routes_keys():
    # real pattern: `if cmd in mapped: run_intent(...)` — mapped's keys ARE routed
    # even though its values are strings (intent names), because membership routes
    src = '''
def handle_command(chat_id, cmd, query, state):
    mapped = {"/brain": "brain", "/research": "research"}
    if cmd in mapped:
        run_intent(chat_id, {"intent": mapped[cmd]}, state)
'''
    exact, _ = ma.routed_commands(src)
    assert {"/brain", "/research"} <= exact


def test_routed_commands_scans_all_functions_and_not_in():
    # dispatch is spread across functions; membership (incl. `not in`) counts
    src = '''
def pre(cmd):
    if cmd not in ("/my_stats", "/admin_costs"):
        return
def other(cmd):
    if cmd == "/faceswap":
        go()
'''
    exact, _ = ma.routed_commands(src)
    assert {"/my_stats", "/admin_costs", "/faceswap"} <= exact


def test_routed_commands_ignores_command_inside_help_text():
    # a slash-command mentioned inside a longer help string is NOT routing
    src = 'def h(cmd):\n    send("- /faceswap — замена лица")\n'
    exact, _ = ma.routed_commands(src)
    assert "/faceswap" not in exact


def test_is_routed_matches_exact_or_prefix():
    exact, prefixes = {"/foo"}, {"/swapbatch"}
    assert ma.is_routed("/foo", exact, prefixes)
    assert ma.is_routed("/swapbatch_set_quality", exact, prefixes)   # prefix
    assert not ma.is_routed("/ghost", exact, prefixes)               # dead


# ── Ось B: глубокий LOAD_GLOBAL-скан ────────────────────────────────────────
def test_undefined_globals_flags_name_used_only_in_body():
    # exactly the /browse_check bug: os used inside the function, never imported
    m = _mod("fakehandler", "def run():\n    return os.getenv('X')\n")
    found = ma.undefined_globals(m)
    assert ("run", "os") in found


def test_undefined_globals_clean_module_is_empty():
    m = _mod("clean", "import os\ndef run():\n    return os.getenv('X')\n")
    assert ma.undefined_globals(m) == []


def test_undefined_globals_recurses_into_comprehension():
    m = _mod("comp", "def run(xs):\n    return [zzz(x) for x in xs]\n")
    assert ("run", "zzz") in ma.undefined_globals(m)


def test_scan_modules_aggregates_with_module_name():
    m = _mod("brokenmod", "def h():\n    return missing_thing\n")
    hits = ma.scan_modules({"brokenmod": m})
    assert ("brokenmod", "h", "missing_thing") in hits


# ── Реестр команд из MENU ───────────────────────────────────────────────────
def test_collect_commands_reads_menu_registry():
    rows = ma.collect_commands()
    by_cmd = {r["cmd"]: r for r in rows}
    # a known friend command in 🌐? no — browser is admin-only; check a real one
    assert by_cmd["/browse_check"]["category"] == "🌐 Браузер"
    assert by_cmd["/browse_check"]["role"] == "admin"      # friend=False
    assert by_cmd["/animate"]["role"] == "friend"          # friend=True
    assert by_cmd["/dev_task"]["category"] == "🛠 Разработка"
    # every row carries the fields the report needs
    assert {"cmd", "category", "cat_id", "role"} <= set(rows[0].keys())


def test_collect_commands_covers_full_registry():
    from tools.jarvis_menu import MENU
    expected = sum(len(c.items) for c in MENU)
    assert len(ma.collect_commands()) == expected


# ── Ось C: dry-call harness (инъекция модуля) ───────────────────────────────
def _handler_mod(body):
    code = "def send(cid, text, *a, **k):\n    _SENT.append(text)\n" + body
    m = _mod("fakectl", code)
    m._SENT = []
    return m


def test_dry_call_ok_when_handler_sends_and_no_unknown():
    m = _handler_mod(
        "def handle_command(chat, cmd, q, state):\n    send(chat, 'готово ' + cmd)\n")
    res = ma.dry_call("/foo", module=m)
    assert res["ok"] is True
    assert any("готово" in s for s in res["sent"])


def test_dry_call_flags_unknown_reply():
    m = _handler_mod(
        "def handle_command(chat, cmd, q, state):\n    send(chat, 'Неизвестная команда: ' + cmd)\n")
    res = ma.dry_call("/ghost", module=m, unknown_markers=("Неизвестная команда",))
    assert res["ok"] is False


def test_dry_call_catches_exception_and_restores_send():
    m = _handler_mod(
        "def handle_command(chat, cmd, q, state):\n    raise RuntimeError('boom')\n")
    orig = m.send
    res = ma.dry_call("/foo", module=m)
    assert res["ok"] is False and "boom" in (res["error"] or "")
    assert m.send is orig            # patch restored even on exception


# ── Классификация статуса ───────────────────────────────────────────────────
def test_classify_dead_when_not_routed():
    status, axis, _ = ma.classify(routed=False, dry_result=None)
    assert status == ma.DEAD and axis == "A"


def test_classify_broken_when_dry_call_fails():
    status, axis, reason = ma.classify(
        routed=True, dry_result={"ok": False, "error": "boom", "sent": []})
    assert status == ma.BROKEN and axis == "C" and "boom" in reason


def test_classify_alive_when_routed_and_dry_ok():
    status, axis, _ = ma.classify(routed=True, dry_result={"ok": True, "sent": [], "error": None})
    assert status == ma.ALIVE and axis == ""


def test_classify_alive_when_routed_and_no_dry_call():
    status, axis, _ = ma.classify(routed=True, dry_result=None)   # not on allowlist
    assert status == ma.ALIVE


# ── Порядок отчёта: 🌐 и 🛠 первыми (уточнение №1) ──────────────────────────
def test_report_order_puts_browser_and_devops_first():
    rows = [
        {"category": "🎬 Видео и анимация", "cat_id": "video"},
        {"category": "🌐 Браузер", "cat_id": "browser"},
        {"category": "🛠 Разработка", "cat_id": "devops"},
    ]
    ordered = ma.order_rows(rows)
    cats = [r["category"] for r in ordered]
    assert cats[0] == "🌐 Браузер" and cats[1] == "🛠 Разработка"
    assert cats.index("🌐 Браузер") < cats.index("🎬 Видео и анимация")


# ── Классификация ошибок импорта: наш код vs опц. 3rd-party ──────────────────
def test_import_error_optional_when_third_party_module_missing():
    exc = ModuleNotFoundError("No module named 'replicate'")
    exc.name = "replicate"
    assert ma.classify_import_exc(exc) == "optional"


def test_import_error_broken_when_our_code_name_missing():
    exc = ImportError("cannot import name 'IntakeRequest' from 'app.models'")
    assert ma.classify_import_exc(exc) == "broken"


def test_import_error_broken_when_our_submodule_missing():
    exc = ModuleNotFoundError("No module named 'app.services.ghost'")
    exc.name = "app.services.ghost"
    assert ma.classify_import_exc(exc) == "broken"


# ── Рендер отчёта ───────────────────────────────────────────────────────────
def test_render_report_groups_by_category_and_shows_summary():
    report = ma.run_audit()
    text = ma.render_report(report)
    # browser/devops headings appear before video (order)
    assert "🌐 Браузер" in text and "🛠 Разработка" in text
    assert text.index("🌐 Браузер") < text.index("🎬 Видео и анимация")
    assert "ИТОГО" in text and "/browse_check" in text
    assert "Ось B" in text                     # axis-B section always rendered
