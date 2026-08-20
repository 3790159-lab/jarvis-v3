# -*- coding: utf-8 -*-
"""Имя клиента на панели: экран не имеет права называть клиента чужим именем.

Спека `docs/superpowers/specs/2026-08-20-panel-client-name.md`, вариант Б
(решение владельца 20.08): копирайт переписан БЕЗ СКЛОНЕНИЙ, имя звучит ровно
один раз — в шапке, в називному відмінку, и берётся из `persona_name` конфига
клиента.

ПОЧЕМУ НЕ ВАРИАНТ А (три формы в конфиге). Украинский склоняет имя, и
подстановка одной формы даёт «Немає зв'язку з Ярина». Держать рядом с именем
ещё два поля можно, но тогда правильность экрана держится на том, что человек
не забудет их заполнить при подключении КАЖДОГО клиента, а ошибку увидит
КЛИЕНТ, а не мы. Вариант Б делает неправильное состояние невозможным.

Сторожа написаны ОТ СПЕКИ (§5), до кода. Главный — П2: сторож на ОДНОМ клиенте
прошёл бы и на зашитом литерале, поэтому клиентов в нём двое и у каждого
проверяется как своё имя, так и ОТСУТСТВИЕ чужого.
"""
from __future__ import annotations

import ast
import importlib
import re
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chatter.storage.db import Store

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "app" / "routers" / "tamapi_dashboard.py"
TEMPLATE_CLIENT = ROOT / "chatter" / "clients" / "yarina"

KEY = "panel-key-for-the-name-guard"
NOW = 1_800_000_000.0

# Литеральный список, а не выведенный из конфигов. Выведенный согласился бы с
# реализацией по определению: если завтра кто-то зашьёт в экран имя клиента,
# которого сегодня нет в `chatter/clients/`, интроспекция его не заметит.
# Формы перечислены ВСЕ, включая кличний — именно склонение и было причиной,
# по которой имя нельзя подставить одной строкой.
FORBIDDEN_NAMES: tuple[str, ...] = (
    "Ольга", "Ольги", "Ользі", "Ольгу", "Ольгою", "Ольго",
    "Ярина", "Ярини", "Ярині", "Ярину", "Яриною", "Ярино",
    "Аня", "Ані", "Аню", "Анею",
)

# Род персоны — такое же зашитое допущение, как и её имя. «Вона перестане
# відповідати» верно ровно до первого клиента с мужской персоной, и ломается
# оно молча: текст остаётся грамматически гладким и просто врёт про пол.
# Проверяется только в копирайте паузы — там, где местоимение относилось
# к персоне; в других строках «вона» может законно указывать на «картку».
FORBIDDEN_GENDER = ("Вона", "вона", "її")


def _screen_strings() -> list[tuple[int, str]]:
    """Строковые литералы модуля, КРОМЕ докстрингов и комментариев.

    Комментарий на экран не попадает, и запрещать историю в нём («двое суток
    лжи на экране Ольги») значит заставить будущего человека стирать причину
    правки. Докстринг — то же самое. Разбор идёт `ast`, а не regex по строкам:
    сканер, который не отличает литерал от комментария, либо краснеет на
    истории, либо (если его ослабить) слепнет на f-строке.
    """
    tree = ast.parse(DASHBOARD.read_text(encoding="utf-8"))
    docs: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docs.add(id(body[0].value))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs):
            out.append((node.lineno, node.value))
    return out


def _make_client(dst_root: Path, slug: str, name: str) -> None:
    """Каталог клиента с ЗАДАННЫМ `persona_name`.

    Копируем настоящего клиента и правим одно поле, а не собираем settings.yaml
    с нуля: собранный вручную минимальный конфиг разошёлся бы со схемой на
    первом же новом обязательном ключе, и сторож покраснел бы не про имя.
    """
    dst = dst_root / slug
    shutil.copytree(TEMPLATE_CLIENT, dst)
    p = dst / "settings.yaml"
    text = re.sub(r"(?m)^persona_name:.*$", 'persona_name: "%s"' % name,
                  p.read_text(encoding="utf-8"))
    assert 'persona_name: "%s"' % name in text, "подмена persona_name не сработала"
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    """Фабрика панелей: `build(slug, name)` -> HTML главного экрана.

    `clients=False` снимает каталог клиентов целиком — так воспроизводится
    клиент, чей конфиг панель прочитать НЕ МОЖЕТ (П4).
    """
    clients_root = tmp_path / "clients"
    clients_root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)

    def build(slug: str, name: str | None, *, clients: bool = True) -> str:
        if name is not None:
            _make_client(clients_root, slug, name)
        db = tmp_path / ("%s.db" % slug)
        s = Store(str(db))
        s.get_or_create_contact("777:%s" % slug)
        s.add_message("777:%s" % slug, "user", "скільки коштує", ts=NOW - 86400)
        del s

        beat = tmp_path / ("beat_%s.txt" % slug)
        beat.write_text("beat", encoding="utf-8")

        monkeypatch.setenv("TAMAPI_DB", str(db))
        monkeypatch.setenv("TAMAPI_SLUG", slug)
        monkeypatch.setenv("TAMAPI_HEARTBEAT", str(beat))
        monkeypatch.setenv(
            "CHATTER_CLIENTS_DIR",
            str(clients_root if clients else tmp_path / "catalog-is-gone"))

        import app.routers.panels_auth as pa
        import app.routers.tamapi_dashboard as td
        for m in (pa, td):
            importlib.reload(m)
        import app.panel_client as pc
        importlib.reload(pc)

        c = TestClient(pc.build_app())
        c.cookies.set("panels_key", KEY)
        r = c.get("/panel/tamapi")
        assert r.status_code == 200, r.text
        return r.text

    yield build
    for mod in ("app.panel_client", "app.routers.tamapi_dashboard",
                "app.routers.panels_auth"):
        sys.modules.pop(mod, None)


# ── П1 ───────────────────────────────────────────────────────────────────────
def test_no_client_name_is_hardcoded_in_the_screen():
    """Ни одного имени клиента в тексте, который попадает на экран."""
    hits = [(ln, s) for ln, s in _screen_strings()
            if any(n in s for n in FORBIDDEN_NAMES)]
    assert not hits, (
        "имя клиента зашито в текст панели — этот экран увидит ДРУГОЙ клиент:\n"
        + "\n".join("  %s:%d  %r" % (DASHBOARD.name, ln, s) for ln, s in hits))


def test_no_persona_gender_is_hardcoded_in_the_pause_copy():
    """Пол персоны — такое же допущение, как имя, и ломается так же молча."""
    hits = [(ln, s) for ln, s in _screen_strings()
            if ("зупинити" in s.lower() or "перестане" in s.lower())
            and any(g in s for g in FORBIDDEN_GENDER)]
    assert not hits, (
        "копирайт паузы согласован по РОДУ с женской персоной:\n"
        + "\n".join("  %s:%d  %r" % (DASHBOARD.name, ln, s) for ln, s in hits))


# ── П2 (главный) ─────────────────────────────────────────────────────────────
def test_two_clients_see_their_own_name_and_not_the_other(panel):
    """ДВА клиента в одном стороже. На одном прошёл бы и зашитый литерал."""
    olga = panel("alpha", "Ольга")
    yarina = panel("beta", "Ярина")

    assert "Ольга" in olga, "панель клиента «Ольга» не назвала его имени"
    assert "Ярина" in yarina, "панель клиента «Ярина» не назвала его имени"
    assert "Ярина" not in olga, (
        "на панели одного клиента видно имя ДРУГОГО — ровно то, из-за чего "
        "экран нельзя было передавать клиенту")
    assert "Ольга" not in yarina, (
        "на панели одного клиента видно имя ДРУГОГО — ровно то, из-за чего "
        "экран нельзя было передавать клиенту")


def test_the_name_sounds_exactly_once(panel):
    """Ровно ОДНО упоминание. Вариант Б держится на том, что склонять нечего."""
    html = panel("gamma", "Ольга")
    assert html.count("Ольга") == 1, (
        "имя звучит на экране %d раз(а): второе упоминание рано или поздно "
        "попросит косвенный падеж, и мы вернёмся к «Немає зв'язку з Ярина»"
        % html.count("Ольга"))


def test_the_name_stands_in_the_header_next_to_the_slug(panel):
    """Имя не просто «где-то есть», а стоит там, где спека его оставила."""
    html = panel("delta", "Ольга")
    assert re.search(r"Ольга\s*·\s*TAMAPI\s*·\s*клієнт\s*delta", html), (
        "имя не в шапке рядом со слагом: спека §3 оставляет ровно это место")


# ── П3 ───────────────────────────────────────────────────────────────────────
def test_the_name_comes_from_persona_name_not_from_env(panel, tmp_path):
    """Правка конфига меняет ЭКРАН. Источник имени один — `persona_name`."""
    before = panel("epsilon", "Ольга")
    assert "Ольга" in before

    cfg = tmp_path / "clients" / "epsilon" / "settings.yaml"
    cfg.write_text(re.sub(r"(?m)^persona_name:.*$", 'persona_name: "Соломія"',
                          cfg.read_text(encoding="utf-8")), encoding="utf-8")

    after = panel("epsilon", None)
    assert "Соломія" in after, (
        "имя на экране не пошло за конфигом — значит источник второй "
        "(env либо литерал), и он разойдётся с конфигом молча")
    assert "Ольга" not in after


# ── П4 ───────────────────────────────────────────────────────────────────────
def test_unreadable_config_refuses_loudly_and_does_not_kill_the_screen(panel):
    """Конфига нет: экран жив, но про имя говорит ПРЯМО, а не подставляет чужое."""
    html = panel("zeta", None, clients=False)

    assert "TAMAPI" in html and "zeta" in html, (
        "нечитаемый конфиг уронил шапку — статус клиент увидеть обязан всегда")
    for n in FORBIDDEN_NAMES:
        assert n not in html, (
            "конфиг не прочитан, а экран всё равно назвал имя %r — это тихая "
            "подстановка чужой персоны" % n)
    assert "не прочитано" in html, (
        "конфиг не прочитан, а экран об этом молчит: пустое место в шапке "
        "читается как «так и задумано»")
