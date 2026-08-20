# -*- coding: utf-8 -*-
"""Сторож: свидетельства подключения (арка T7) обязаны попадать в бэкап state/.

ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ СТРОЧКА В test_state_backup.py.
`state/connect/` держит ДВЕ улики, которых нет больше нигде:

* `<slug>.consent.md` — согласие клиента на то, что бот читает ВСЮ личку его
  Telegram-аккаунта. `state/` не версионируется, значит единственная копия
  улики доступа к чужой переписке живёт на одном диске, и её сохранность
  держится ровно этим бэкапом (об этом прямо сказано в докстринге
  `chatter.connect.probes.probe_s5`);
* `<slug>.bundle.txt` — маркер того, что бандл секретов пересняли после логина
  и какие сессии в него вошли.

ВТОРАЯ ПОЛОВИНА СТОРОЖА ВАЖНЕЕ ПЕРВОЙ. Белый список `CRITICAL_PATTERNS`
существует не чтобы «набрать файлов», а чтобы в бэкап НИКОГДА не уехал
секрет. Каталог `state/connect/` соседствует с session-файлами Telethon и
может собрать любой мусор, поэтому широкий глоб вида `connect/*` здесь —
это утечка, а не удобство. Тесты ниже фиксируют границу с обеих сторон:
две улики берём по ТОЧНЫМ хвостам имён, всё остальное в том же каталоге —
включая человеческий журнал `<slug>.md` — не берём.
"""
from __future__ import annotations

from pathlib import Path

from app.services import state_backup as sb

SLUG = "volska"


def _connect_tree(tmp_path: Path) -> Path:
    """`state/` с каталогом connect/, где рядом лежат улики, журнал и секреты."""
    root = tmp_path / "state"
    connect = root / "connect"
    connect.mkdir(parents=True)

    # Улики — обязаны попасть в бэкап.
    (connect / f"{SLUG}.consent.md").write_text(
        "2026-08-20, согласие получено от Ольги голосовым", encoding="utf-8")
    (connect / f"{SLUG}.bundle.txt").write_text(
        "BUNDLE: volska.session", encoding="utf-8")

    # Журнал подключения — для ЧЕЛОВЕКА, не улика; в бэкапе не нужен.
    (connect / f"{SLUG}.md").write_text("S1 ok\nS2 ok\n", encoding="utf-8")

    # Секреты и мусор в ТОМ ЖЕ каталоге — не должны попасть никогда.
    (connect / "secret.env").write_text("API_ID=1", encoding="utf-8")
    (connect / ".env").write_text("API_HASH=deadbeef", encoding="utf-8")
    (connect / f"{SLUG}.session").write_bytes(b"\x00sqlite-telethon")
    (connect / f"{SLUG}.session-journal").write_bytes(b"\x00")
    (connect / "api_keys.json").write_text('{"key": "secret"}', encoding="utf-8")
    (connect / f"{SLUG}.bundle.txt.bak").write_text("BUNDLE: old", encoding="utf-8")

    return root


def _rels(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in sb.discover_backup_files(root)}


def test_connect_consent_is_backed_up(tmp_path):
    """Согласие на доступ к личной переписке существует в одном экземпляре."""
    root = _connect_tree(tmp_path)
    assert f"connect/{SLUG}.consent.md" in _rels(root)


def test_connect_bundle_marker_is_backed_up(tmp_path):
    """Маркер бандла — вторая улика подключения, тоже вне git."""
    root = _connect_tree(tmp_path)
    assert f"connect/{SLUG}.bundle.txt" in _rels(root)


def test_connect_human_journal_is_not_backed_up(tmp_path):
    """`<slug>.md` — журнал для человека, а не свидетельство.

    Он же ловит слишком широкий глоб `connect/*.md`: такой глоб забрал бы и
    consent, и журнал, и тест на согласие остался бы зелёным при утечке
    границы.
    """
    root = _connect_tree(tmp_path)
    assert f"connect/{SLUG}.md" not in _rels(root)


def test_connect_secrets_never_enter_the_allowlist(tmp_path):
    """ГРАНИЦА: ни один секрет из connect/ не попадает в отбор.

    Проверяем поимённо, а не «не пусто»: широкий `connect/*` прошёл бы любую
    проверку на присутствие улик и утащил бы session-файл Telethon — готовый
    доступ к аккаунту клиента — в облачный бэкап.
    """
    root = _connect_tree(tmp_path)
    rels = _rels(root)

    assert f"connect/{SLUG}.session" not in rels
    assert f"connect/{SLUG}.session-journal" not in rels
    assert "connect/secret.env" not in rels
    assert "connect/.env" not in rels
    assert "connect/api_keys.json" not in rels
    assert f"connect/{SLUG}.bundle.txt.bak" not in rels
    assert not any(r.endswith(".env") for r in rels)
    assert not any(r.endswith(".session") for r in rels)


def test_connect_patterns_are_narrow_not_a_directory_wildcard(tmp_path):
    """Из каталога connect/ берём РОВНО две улики и ничего больше.

    Сторож на составе отбора, а не на отдельных именах: любой будущий файл,
    положенный рядом (дамп, бэкап сессии, черновик), обязан требовать
    осознанного расширения списка, а не проезжать по инерции.
    """
    root = _connect_tree(tmp_path)
    from_connect = sorted(r for r in _rels(root) if r.startswith("connect/"))
    assert from_connect == [
        f"connect/{SLUG}.bundle.txt",
        f"connect/{SLUG}.consent.md",
    ]
