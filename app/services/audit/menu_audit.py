# -*- coding: utf-8 -*-
"""Menu-audit library: is every menu command alive, broken, or dead?

Pure, $0, no network. Three axes (spec §2):
  A. routed_commands()   — static AST scan of handle_command → which commands
                           have a dispatch branch (dead = in MENU, not routed).
  B. undefined_globals() — disassemble every function; a LOAD_GLOBAL that
                           resolves to neither module globals nor builtins is a
                           missing import used inside a body (the /browse_check
                           'os' bug class). A plain import cannot catch it.
  C. dry_call()          — invoke a curated read-only command through the real
                           handler with send() captured; never for money/side-
                           effect commands.

See docs/superpowers/specs/2026-07-06-menu-audit-design.md.
"""
from __future__ import annotations

import ast
import builtins
import dis
import types


# ── Ось A: статический AST-разбор роутинга ──────────────────────────────────
def routed_commands(source: str, cmd_var: str = "cmd"):
    """Parse ``source`` (whole control module) and return (exact, prefixes) of
    routed command tokens. Dispatch is spread across many functions AND styles,
    so we model all of the real ones:

    - ``cmd (==|!=) "/x"`` / ``"/x" (==|!=) cmd``          → exact
    - ``cmd (in|not in) ("/a", "/b", …)``                  → exact (each element)
    - dict-table keys: ``{"/x": handler, …}`` (Constant "/x" keys)  → exact
    - ``cmd.startswith("/p")`` (or a tuple of prefixes)    → prefixes

    Only exact ``"/x"`` string constants count — a slash-command mentioned inside
    a longer help string is not matched. A menu command is routed iff it is in
    ``exact`` or starts with a prefix (see ``is_routed``)."""
    exact: set = set()
    prefixes: set = set()
    tree = ast.parse(source)

    def _is_cmd(node):
        return isinstance(node, ast.Name) and node.id == cmd_var

    def _str(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def _strs(node):
        if _str(node) is not None:
            return [_str(node)]
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return [_str(e) for e in node.elts if _str(e) is not None]
        return []

    def _dict_cmd_keys(d):
        return {_str(k) for k in d.keys
                if _str(k) is not None and _str(k).startswith("/")}

    name_to_dict = {}        # NAME = { ... }  (для правила membership-против-dict)
    member_names = set()     # имена, по которым идёт `cmd in NAME` / `not in NAME`

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    name_to_dict[tgt.id] = node.value
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op, left, right = node.ops[0], node.left, node.comparators[0]
            if isinstance(op, (ast.Eq, ast.NotEq)):
                if _is_cmd(left) and _str(right) is not None:
                    exact.add(_str(right))
                elif _is_cmd(right) and _str(left) is not None:
                    exact.add(_str(left))
            elif isinstance(op, (ast.In, ast.NotIn)) and _is_cmd(left):
                exact.update(_strs(right))                 # cmd in ("/a","/b")
                if isinstance(right, ast.Name):            # cmd in NAME (dict/collection)
                    member_names.add(right.id)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "startswith" and _is_cmd(node.func.value)):
            for arg in node.args:
                prefixes.update(_strs(arg))
        elif isinstance(node, ast.Dict):
            # genuine DISPATCH table: keys "/x" whose VALUES are handler-like
            # (lambda/name/call/attr), not constants. A label/intent map alone
            # ({"/x": "текст"}) does NOT prove a handler — unless it's used as a
            # `cmd in NAME` membership target (handled below).
            for k, v in zip(node.keys, node.values):
                s = _str(k)
                if s is not None and s.startswith("/") and not isinstance(v, ast.Constant):
                    exact.add(s)

    # membership-против-dict: `if cmd in mapped:` → все ключи mapped маршрутизированы
    for name in member_names:
        if name in name_to_dict:
            exact.update(_dict_cmd_keys(name_to_dict[name]))

    return exact, prefixes


def is_routed(cmd: str, exact, prefixes) -> bool:
    """True if ``cmd`` has a dispatch branch: exact match or a prefix guard."""
    return cmd in exact or any(cmd.startswith(p) for p in prefixes)


# ── Ось B: глубокий LOAD_GLOBAL-скан ────────────────────────────────────────
def undefined_globals(module):
    """[(func_name, name), …] for every LOAD_GLOBAL in a function of ``module``
    that resolves to neither the module's globals nor a builtin — i.e. a name
    used inside a body but never imported/defined (the /browse_check 'os' bug).
    Recurses into nested code objects (comprehensions, closures)."""
    ns = vars(module)
    out = []

    def _scan(code, where):
        for ins in dis.get_instructions(code):
            if ins.opname == "LOAD_GLOBAL":
                n = ins.argval
                if n not in ns and not hasattr(builtins, n):
                    out.append((where, n))
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                _scan(const, where)

    for obj_name, obj in ns.items():
        if isinstance(obj, types.FunctionType) and obj.__module__ == module.__name__:
            _scan(obj.__code__, obj_name)
    return sorted(set(out))


ALIVE, BROKEN, DEAD = "🟢 жив", "🟡 сломан", "🔴 мёртв"

# Отчёт группируется по категориям; свежайший код — первым (уточнение №1).
REPORT_FIRST_CAT_IDS = ("browser", "devops")


def order_rows(rows):
    """Rows grouped by category, cat_ids in REPORT_FIRST_CAT_IDS first, then the
    order categories first appear in ``rows`` (i.e. MENU order). Stable within a
    category."""
    seen_order, first_seen = [], {}
    for i, r in enumerate(rows):
        cid = r["cat_id"]
        if cid not in first_seen:
            first_seen[cid] = i
            seen_order.append(cid)

    def _cat_key(cid):
        if cid in REPORT_FIRST_CAT_IDS:
            return (0, REPORT_FIRST_CAT_IDS.index(cid))
        return (1, seen_order.index(cid))

    return sorted(rows, key=lambda r: (_cat_key(r["cat_id"]), first_seen[r["cat_id"]]))


def classify(*, routed: bool, dry_result):
    """Crisp per-command verdict (spec §3). Axis B (undefined globals) is
    project-wide, reported separately — not folded in here.

    - not routed            → 🔴 мёртв (A): пункт меню без ветки диспетча
    - dry-call failed        → 🟡 сломан (C): allowlist-команда упала/«unknown»
    - otherwise             → 🟢 жив
    """
    if not routed:
        return DEAD, "A", "нет ветки в handle_command"
    if dry_result is not None and not dry_result.get("ok", False):
        return BROKEN, "C", dry_result.get("error") or "dry-вызов не прошёл"
    return ALIVE, "", ""


# ── Ось C: dry-call harness ─────────────────────────────────────────────────
# Ответы бота, означающие «команда не распознана» — dry-вызов, вернувший такое,
# = сломан роутинг (сентинел; список расширяем).
DEFAULT_UNKNOWN_MARKERS = ("Неизвестная команда", "Неизвестное действие",
                           "Неизвестн", "unknown command")


def dry_call(cmd, *, module, handler_name="handle_command", send_attr="send",
             state=None, unknown_markers=DEFAULT_UNKNOWN_MARKERS, chat_id="0"):
    """Invoke ``cmd`` through the real handler with ``send`` captured, then
    restore. NEVER call this on money/side-effect commands — only the curated
    read-only allowlist (spec §5). Returns {ok, sent, error}.

    ok = handler raised nothing AND no captured reply matched an unknown-marker.
    ``send`` is always restored (even on exception)."""
    captured = []
    orig = getattr(module, send_attr)

    def _capture(cid, text="", *a, **k):
        captured.append(str(text))

    setattr(module, send_attr, _capture)
    try:
        getattr(module, handler_name)(chat_id, cmd, "", state if state is not None else {})
    except Exception as exc:                       # any raise = broken dry-call
        return {"ok": False, "sent": captured, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        setattr(module, send_attr, orig)
    bad = any(m in s for s in captured for m in unknown_markers)
    return {"ok": not bad, "sent": captured,
            "error": "ответ похож на «неизвестная команда»" if bad else None}


def collect_commands():
    """Flatten tools.jarvis_menu.MENU into report rows (source of truth for the
    audited command set). role = 'friend' if the item is friend-visible else
    'admin'. Category order preserved from the registry."""
    from tools.jarvis_menu import MENU
    rows = []
    for cat in MENU:
        for it in cat.items:
            rows.append({
                "cmd": it.cmd,
                "category": cat.title,
                "cat_id": cat.cat_id,
                "role": "friend" if it.friend else "admin",
            })
    return rows


def scan_modules(modules: dict):
    """Run undefined_globals across ``{module_name: module}`` → sorted list of
    ``(module_name, func_name, name)`` triples. Empty = clean."""
    hits = []
    for name, mod in modules.items():
        for where, missing in undefined_globals(mod):
            hits.append((name, where, missing))
    return sorted(set(hits))


# ── Живая обвязка (для CLI и регресс-зуба) ──────────────────────────────────
CONTROL_MODULE = "tools.jarvis_smart_telegram_control"

# Read-only allowlist для dry-вызовов (spec §5). Tier A — по умолчанию.
# Tier B (пограничные) вынесены отдельно и НЕ вызываются по умолчанию.
ALLOWLIST_TIER_A = (
    "/me_roles", "/me_places", "/me_styles",
    "/dish_styles", "/party_themes",
    "/capabilities", "/my_stats", "/menu",
)
ALLOWLIST_TIER_B = (
    "/git_status", "/logs_tail", "/health", "/smart_health",
    "/status", "/costs", "/stats", "/agents",
)


def _iter_app_services():
    """Fully-qualified names of every submodule under app.services (recursive)."""
    import pkgutil
    import app.services as _svc
    names = []
    for m in pkgutil.walk_packages(_svc.__path__, prefix="app.services."):
        names.append(m.name)
    return names


def classify_import_exc(exc) -> str:
    """'optional' if the import failed only because a *third-party* module isn't
    installed (ModuleNotFoundError on a non-app/tools top-level dep, e.g.
    replicate/openpyxl) — environmental, not our bug. Otherwise 'broken' (our own
    code: a missing name/attribute, a bad app.* submodule, a syntax/runtime error
    at import)."""
    if isinstance(exc, ModuleNotFoundError):
        miss = (getattr(exc, "name", None) or "").split(".")[0]
        if miss and miss not in ("app", "tools"):
            return "optional"
    return "broken"


def discover_handler_modules(extra=("tools.jarvis_menu", CONTROL_MODULE)):
    """Import every handler-backing module. Returns (imported, broken, optional):
    imported = {name: module}, broken = [(name, 'ExcType: msg')] (our-code import
    breakage — the gated signal), optional = [(name, 'missing dep')] (third-party
    dep not installed — reported, not gated)."""
    import importlib
    imported, broken, optional = {}, [], []
    for name in list(extra) + _iter_app_services():
        try:
            imported[name] = importlib.import_module(name)
        except Exception as exc:
            if classify_import_exc(exc) == "optional":
                optional.append((name, "нет пакета '%s'" % (getattr(exc, "name", "?"))))
            else:
                broken.append((name, "%s: %s" % (type(exc).__name__, exc)))
    return imported, broken, optional


def _control_source():
    import inspect
    import importlib
    return inspect.getsource(importlib.import_module(CONTROL_MODULE))


# ── Известные пре-существующие находки (spec §8 Task 4) ─────────────────────
# Реальные баги/поломки, НЕ внесённые этой аркой — вынесены в тикеты-фиксы
# (нарезка dev-задач). Зуб зелёный на них (baseline), но падает на ЛЮБОЙ НОВОЙ.
# Убирать строку отсюда = починили → зуб снова её сторожит.
KNOWN_UNDEFINED_GLOBALS = frozenset()   # time_brain.get_zone ПОЧИНЕН → зуб снова сторожит
KNOWN_BROKEN_IMPORTS = frozenset({
    # supervisor импортирует IntakeRequest из app.models, которого там больше нет.
    "app.services.supervisor",
})


def run_audit(dry_allowlist=ALLOWLIST_TIER_A):
    """Live audit over the whole MENU registry. Returns a report dict:
    {rows, import_broken, import_optional, scan_hits, summary}. Rows ordered
    🌐/🛠 first (order_rows); each carries status/axis/reason (+dry on allowlist)."""
    imported, import_broken, import_optional = discover_handler_modules()
    exact, prefixes = routed_commands(_control_source())
    scan_hits = scan_modules(imported)
    control = imported.get(CONTROL_MODULE)

    rows = []
    for row in collect_commands():
        cmd = row["cmd"]
        routed = is_routed(cmd, exact, prefixes)
        dry = None
        if cmd in dry_allowlist and routed and control is not None:
            dry = dry_call(cmd, module=control)
        status, axis, reason = classify(routed=routed, dry_result=dry)
        rows.append({**row, "status": status, "axis": axis, "reason": reason,
                     "dry": dry})
    rows = order_rows(rows)

    summary = {
        "alive": sum(r["status"] == ALIVE for r in rows),
        "broken": sum(r["status"] == BROKEN for r in rows),
        "dead": sum(r["status"] == DEAD for r in rows),
        "b_hits": len(scan_hits),
        "import_broken": len(import_broken),
        "import_optional": len(import_optional),
        "total": len(rows),
    }
    return {"rows": rows, "import_broken": import_broken,
            "import_optional": import_optional, "scan_hits": scan_hits,
            "summary": summary}


def render_report(report) -> str:
    """Human-readable table: grouped by category (🌐/🛠 first), per-category mini
    summary, axis-B section, optional-skipped section, overall ИТОГО. Same text
    for stdout and the markdown snapshot."""
    rows = report["rows"]
    out = ["# Аудит меню — жив / сломан / мёртв", ""]

    # group preserving order_rows() ordering
    seen, groups = [], {}
    for r in rows:
        if r["category"] not in groups:
            groups[r["category"]] = []
            seen.append(r["category"])
        groups[r["category"]].append(r)

    for cat in seen:
        grp = groups[cat]
        a = sum(x["status"] == ALIVE for x in grp)
        b = sum(x["status"] == BROKEN for x in grp)
        d = sum(x["status"] == DEAD for x in grp)
        out.append("## %s   (🟢%d 🟡%d 🔴%d)" % (cat, a, b, d))
        for r in grp:
            tail = ""
            if r["status"] != ALIVE:
                tail = "  — [%s] %s" % (r["axis"], r["reason"])
            elif r["dry"] is not None:
                tail = "  — dry-вызов ✓"
            out.append("  %-8s %-22s %-7s%s" % (r["status"], r["cmd"], r["role"], tail))
        out.append("")

    out.append("## ⚙️ Ось B — импорты и глобалы")
    if report["scan_hits"]:
        out.append("  🟡 undefined globals (имя в теле функции без импорта):")
        for mod, fn, name in report["scan_hits"]:
            known = " (известное, в baseline)" if (mod, fn, name) in KNOWN_UNDEFINED_GLOBALS else " ⚠️ НОВОЕ"
            out.append("    %s : %s() → '%s'%s" % (mod, fn, name, known))
    if report["import_broken"]:
        out.append("  🟡 битые импорты (наш код):")
        for name, err in report["import_broken"]:
            known = " (известное, в baseline)" if name in KNOWN_BROKEN_IMPORTS else " ⚠️ НОВОЕ"
            out.append("    %s — %s%s" % (name, err, known))
    if report["import_optional"]:
        out.append("  ⚪ пропущено (нет опц. зависимости, не гейтится):")
        for name, err in report["import_optional"]:
            out.append("    %s — %s" % (name, err))
    if not (report["scan_hits"] or report["import_broken"] or report["import_optional"]):
        out.append("  чисто.")
    out.append("")

    s = report["summary"]
    out.append("## ИТОГО: 🟢%d жив / 🟡%d сломан / 🔴%d мёртв  (из %d)"
               % (s["alive"], s["broken"], s["dead"], s["total"]))
    out.append("Ось B: %d undefined-globals, %d битых импортов (наш код), %d пропущено (опц.дэпы)."
               % (s["b_hits"], s["import_broken"], s["import_optional"]))
    nf = new_findings(report)
    beyond = sum(len(v) for v in nf.values())
    out.append("Сверх baseline (гейтится зубом): %d находок → %s"
               % (beyond, "🚫 РЕГРЕСС" if beyond else "✅ чисто"))
    return "\n".join(out)


def new_findings(report):
    """Findings BEYOND the documented known-exceptions baseline — what the tooth
    gates on. Returns {dead, broken_cmds, undefined_globals, broken_imports}."""
    return {
        "dead": [r["cmd"] for r in report["rows"] if r["status"] == DEAD],
        "broken_cmds": [r["cmd"] for r in report["rows"] if r["status"] == BROKEN],
        "undefined_globals": [h for h in report["scan_hits"]
                              if h not in KNOWN_UNDEFINED_GLOBALS],
        "broken_imports": [e for e in report["import_broken"]
                           if e[0] not in KNOWN_BROKEN_IMPORTS],
    }


def audit_ok(report) -> bool:
    """CLI exit-code verdict: nothing red at all (incl. known pre-existing)."""
    s = report["summary"]
    return (s["dead"] == 0 and s["broken"] == 0 and s["b_hits"] == 0
            and s["import_broken"] == 0)


def gate_ok(report) -> bool:
    """Regress-tooth verdict: no findings BEYOND the known-exceptions baseline.
    Green now on pre-existing issues, RED on any newly-introduced one."""
    nf = new_findings(report)
    return not any(nf.values())
