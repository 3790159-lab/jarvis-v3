# -*- coding: utf-8 -*-
"""Import-safety tooth for the browser package. $0, no browser, no mocks.

Two guarantees, both learned from a live crash (`/browse_check` → NameError:
name 'os' is not defined, session browse_35e7ad9a):

1. Every submodule imports for real (no mocks) — catches import-time breakage.
2. No function references an *undefined global* — catches names used only inside
   a function body (e.g. ``os.getenv`` in a ``# pragma: no cover`` live path that
   tests never call). A plain ``import`` cannot catch this: the NameError only
   fires when the function runs. Here we disassemble every function and assert
   each LOAD_GLOBAL resolves to a module global or a builtin.
"""
import builtins
import dis
import importlib
import pkgutil
import types

import app.services.browser as _pkg

_SUBMODULES = [
    "app.services.browser." + m.name
    for m in pkgutil.iter_modules(_pkg.__path__)
]


def test_all_browser_submodules_import_for_real():
    assert _SUBMODULES, "browser package exposes no submodules — discovery broke"
    for name in _SUBMODULES:
        importlib.import_module(name)               # no mocks: a bad import raises here


def _undefined_globals(module):
    """Names loaded via LOAD_GLOBAL that are neither module globals nor builtins.
    Recurses into nested functions (comprehensions, closures)."""
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


def test_no_function_uses_an_undefined_global():
    problems = []
    for name in _SUBMODULES:
        mod = importlib.import_module(name)
        for where, missing in _undefined_globals(mod):
            problems.append("%s: %s() references undefined global '%s'" % (name, where, missing))
    assert not problems, "undefined globals (missing imports?):\n" + "\n".join(problems)
