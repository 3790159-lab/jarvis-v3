"""DEV-100: агент, который отчитывается `ok`, не сделав работы, удалён насовсем.

`app/agents/*.py` объявляли роли (architect, qa, backend_fix, full_creator,
design_to_code, night_mode_strategist), но `run()` не делал работы. Целиком:

    def run(self, task):
        checks = task.get("checks") or ["compile", "health", "artifacts"]
        return AgentResult(status="ok", agent=self.name,
                           message="QA checklist prepared", ...)

Мёртвый код молчит. Этот ГОВОРИЛ `ok` — и в любой оркестрации читался бы как
«шаг пройден». Решение владельца 07.09.2026: удалить весь каскад, не оставлять.

Удаление потянуло 13 файлов и 849 строк, потому что `app/main.py` монтировал
роутер `/api/claude-ecosystem` голым top-level импортом. Подсистема была мертва
по замеру: 11 эндпоинтов, **0 обращений из 11491** в логе бэкенда, следов в
`state/` нет, из `tools/` и `scripts/` не зовётся, тестов ноль.

Список ниже ЛИТЕРАЛЬНЫЙ. Интроспекция здесь врала бы в обе стороны: «в пакете
нет модулей» зеленеет и на удалённом пакете, и на пустом, и на переименованном.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = Path(__file__).resolve().parents[1]

# Все 13 файлов каскада, снятого по DEV-100.
REMOVED = (
    "app/agents/__init__.py",
    "app/agents/architect_agent.py",
    "app/agents/backend_fix_agent.py",
    "app/agents/base_agent.py",
    "app/agents/design_to_code_agent.py",
    "app/agents/full_creator_agent.py",
    "app/agents/night_mode_strategist.py",
    "app/agents/qa_agent.py",
    "app/services/claude_ecosystem_orchestrator.py",
    "app/services/claude_ecosystem_execution_bridge.py",
    "app/services/claude_ecosystem_registry.py",
    "app/services/ultra_upgrade_engine.py",
    "app/routers/claude_ecosystem.py",
)

# Где ищем упоминания: код, а не документация и не журналы.
SEARCH_DIRS = ("app", "scripts", "tools", "chatter")


def test_every_removed_file_stays_removed():
    alive = [rel for rel in REMOVED if (ROOT / rel).exists()]
    assert not alive, "вернулись удалённые файлы каскада DEV-100: " + ", ".join(alive)


def test_agents_package_has_no_sources_left():
    """Не только перечисленные файлы: в каталоге не должно остаться ИСХОДНИКОВ.

    Строгое «каталога нет» здесь было бы сторожем, который врёт по среде:
    `git rm -r` снимает отслеживаемые файлы, но `__pycache__` не отслеживается
    и остаётся на диске — в этом дереве он и остался, а в свежем клоне его нет.
    Такой сторож краснел бы в проде из-за остатка сборки, а не из-за кода.

    Остаток без исходников пакетом не является: импортировать из голого
    `__pycache__` нечего, `import app.agents` падает — это и проверяет
    `test_app_main_still_imports` вместе с остальными.
    """
    folder = ROOT / "app" / "agents"
    if not folder.exists():
        return
    leftovers = sorted(p.name for p in folder.iterdir() if p.name != "__pycache__")
    assert not leftovers, "в app/agents остались файлы: " + ", ".join(leftovers)


def test_nothing_imports_the_removed_modules():
    """Импорт удалённого — это отложенное падение, а не ошибка компиляции."""
    needles = ("app.agents", "claude_ecosystem", "ultra_upgrade_engine")
    offenders = []
    for folder in SEARCH_DIRS:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.relative_to(ROOT)} -> {needle}")
    assert not offenders, "ссылки на удалённое: " + "; ".join(offenders)


def test_app_main_still_imports():
    """Сторож на САМ каскад, а не на его результат.

    Замер 07.09 до удаления: снять только `app/agents` — и `app.main` падает с
    `ModuleNotFoundError: No module named 'app.agents'`, то есть бэкенд не
    стартует. Этот сторож краснеет на любом НЕДОДЕЛАННОМ удалении: снял слой,
    забыл снять монтирование — увидишь здесь, а не на проде.
    """
    import importlib

    module = importlib.import_module("app.main")
    assert getattr(module, "app", None) is not None
