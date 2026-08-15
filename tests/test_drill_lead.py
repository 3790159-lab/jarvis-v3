# -*- coding: utf-8 -*-
"""Отправитель реплик лида (стенд v2, Э2).

Процесс держит ЧУЖУЮ (тестовую) сессию и умеет ровно одно — сказать текст в
один разрешённый чат. Поэтому тесты здесь — про предохранители, а не про
фичи: цена ошибки — сообщение живому человеку из аккаунта, который он не
ждал, либо флуд с тестового аккаунта (бан ловят ОБА аккаунта, включая
клиентский).

$0: ни сети, ни Telethon — отправка инъектируется.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drill_lead.py"


def _load():
    spec = importlib.util.spec_from_file_location("drill_lead_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drill_lead_script"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Spy:
    """Заглушка отправителя: помнит всё, что «ушло», и в каком порядке
    относительно пауз."""

    def __init__(self):
        self.sent: list[str] = []
        self.events: list[str] = []
        self.closed = False

    def send(self, text: str) -> int:
        self.sent.append(text)
        self.events.append(f"send:{text}")
        return 100 + len(self.sent)

    def close(self) -> None:
        self.closed = True


def _run(mod, argv, stdin_text, *, spy=None, root=None):
    spy = spy or _Spy()
    slept: list[float] = []

    def sleep(sec):
        slept.append(sec)
        spy.events.append(f"sleep:{sec}")

    out = io.StringIO()
    code = mod.main(
        argv,
        stdin=io.StringIO(stdin_text),
        sender=spy,
        sleep=sleep,
        out=out,
        root=root,
    )
    return code, spy, slept, out.getvalue()


@pytest.fixture()
def mod():
    return _load()


@pytest.fixture()
def root(tmp_path):
    """Корень с разрешённым peer'ом и тестовой сессией — «нормальные» условия,
    от которых отталкиваются негативные тесты."""
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "drill_lead.session.enc").write_bytes(b"x")
    (tmp_path / ".secrets" / "drill_lead_peers.txt").write_text(
        "# кому лиду разрешено писать\n777000\n", encoding="utf-8")
    return tmp_path


# ── ПРЕДОХРАНИТЕЛЬ 1: peer-allowlist ─────────────────────────────────────────


def test_peer_outside_allowlist_sends_nothing(mod, root):
    """Опечатка в id не должна становиться сообщением незнакомому человеку."""
    code, spy, _, out = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777001"], "SAY привет\nQUIT\n", root=root)
    assert code != 0
    assert spy.sent == []
    assert "777001" in out


def test_empty_allowlist_refuses(mod, tmp_path):
    """Пустой список = «разрешено всё» при молчаливом дефолте. Молчаливый
    дефолт — класс бага (P17): отказ, а не отправка."""
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "drill_lead.session.enc").write_bytes(b"x")
    code, spy, _, out = _run(
        mod, ["--session", str(tmp_path / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY привет\n", root=tmp_path)
    assert code != 0
    assert spy.sent == []


def test_allowed_peer_sends(mod, root):
    code, spy, _, out = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY привіт\nQUIT\n", root=root)
    assert code == 0
    assert spy.sent == ["привіт"]
    assert "SENT " in out


def test_peers_file_junk_line_is_loud(mod, root):
    """Мусор в файле разрешений — ошибка, а не «пропустим строку»: иначе
    затёртый id молча превращается в пустой список."""
    (root / ".secrets" / "drill_lead_peers.txt").write_text(
        "777000\nолька\n", encoding="utf-8")
    code, spy, _, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY x\n", root=root)
    assert code != 0
    assert spy.sent == []


def test_peers_file_ignores_comments_and_blanks(mod, root):
    (root / ".secrets" / "drill_lead_peers.txt").write_text(
        "\n# коммент\n  777000  \n\n", encoding="utf-8")
    code, spy, _, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY x\nQUIT\n", root=root)
    assert code == 0
    assert spy.sent == ["x"]


# ── ПРЕДОХРАНИТЕЛЬ 2: только текст ───────────────────────────────────────────


def test_source_has_no_media_or_forward(mod):
    """Ни файлов, ни медиа, ни пересылок — не «мы так не делаем», а нечем."""
    src = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("send_file", "forward_messages", "upload_file",
                      "download_media", "send_photo"):
        assert forbidden not in src, forbidden


def test_unknown_command_is_refused(mod, root):
    code, spy, _, out = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SEND_FILE /etc/passwd\n", root=root)
    assert code != 0
    assert spy.sent == []


def test_empty_say_is_refused(mod, root):
    code, spy, _, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY    \n", root=root)
    assert code != 0
    assert spy.sent == []


# ── ПРЕДОХРАНИТЕЛЬ 3: потолок сообщений ──────────────────────────────────────


def test_message_cap_stops_the_flood(mod, root):
    """Зацикленный оркестратор не превращается в флуд с тестового аккаунта."""
    code, spy, _, out = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000", "--max-messages", "3"],
        "SAY 1\nSAY 2\nSAY 3\nSAY 4\nQUIT\n", root=root)
    assert spy.sent == ["1", "2", "3"]
    assert code != 0
    assert "потолок" in out.lower()


# ── ПРЕДОХРАНИТЕЛЬ 4: человеческие паузы ─────────────────────────────────────


def test_pause_precedes_every_send_and_is_human(mod, root):
    """Мгновенные ответы на дистанции — сигнал автоматики для антифрода, а
    рискует и клиентский аккаунт тоже."""
    code, spy, slept, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY a\nSAY b\nQUIT\n", root=root)
    assert code == 0
    assert len(slept) == 2
    assert all(mod.PAUSE_MIN <= s <= mod.PAUSE_MAX for s in slept), slept
    assert spy.events == ["sleep:%s" % slept[0], "send:a",
                          "sleep:%s" % slept[1], "send:b"]


def test_pause_bounds_are_configurable_but_stay_positive(mod, root):
    code, spy, slept, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000", "--pause-min", "0", "--pause-max", "0"],
        "SAY a\nQUIT\n", root=root)
    assert code == 0
    assert slept == [0.0]


def test_pause_min_above_max_is_refused(mod, root):
    code, spy, _, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000", "--pause-min", "30", "--pause-max", "5"],
        "SAY a\n", root=root)
    assert code != 0
    assert spy.sent == []


# ── ПРЕДОХРАНИТЕЛЬ 5: своя (и только своя) сессия ────────────────────────────


def test_client_session_is_refused(mod, root):
    """🔴 Инвариант v2: боевая сессия и сессия лида НИКОГДА не живут в одном
    процессе. Здесь это проверяется ДО открытия чего-либо."""
    (root / ".secrets" / "demo.session.enc").write_bytes(b"x")
    code, spy, _, out = _run(
        mod, ["--session", str(root / ".secrets" / "demo.session"),
              "--peer", "777000"], "SAY x\n", root=root)
    assert code != 0
    assert spy.sent == []
    assert "demo" in out


def test_drill_session_stems_do_not_contain_client_slugs(mod):
    assert "demo" not in mod.DRILL_SESSION_STEMS
    assert "volska" not in mod.DRILL_SESSION_STEMS
    assert "drill_lead" in mod.DRILL_SESSION_STEMS


def test_missing_encrypted_session_is_loud(mod, root):
    (root / ".secrets" / "drill_lead.session.enc").unlink()
    code, spy, _, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY x\n", root=root)
    assert code != 0
    assert spy.sent == []


def test_sender_uses_blocking_telethon(mod):
    """Голый `from telethon import TelegramClient` вернул бы корутины, и
    офлайн-зелёный лид умер бы на первом ЖИВОМ вызове (урок 26.07 про
    классификатор: офлайн-тесты со швом не ловят контракт API)."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "from telethon.sync import TelegramClient" in src
    assert "from telethon import TelegramClient" not in src


def test_source_never_writes_plaintext_session(mod):
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "save_string_session" not in src
    assert "StringSession.save" not in src


# ── СТОРОЖ: лид не поллит Telegram и не трогает пульт ────────────────────────


def test_lead_never_polls_updates(mod):
    """Тот же инвариант, что у харнесса: второй потребитель апдейтов роняет
    живой пульт владельца."""
    src = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("getUpdates", "run_until_disconnected", "on(events",
                      "add_event_handler", "iter_dialogs", "get_dialogs"):
        assert forbidden not in src, forbidden


# ── протокол stdin ───────────────────────────────────────────────────────────


def test_parse_command(mod):
    assert mod.parse_command("SAY привет мир") == ("SAY", "привет мир")
    assert mod.parse_command("QUIT") == ("QUIT", "")
    assert mod.parse_command("  SAY  два  пробела ") == ("SAY", "два  пробела")
    with pytest.raises(mod.LeadError):
        mod.parse_command("say lower")
    with pytest.raises(mod.LeadError):
        mod.parse_command("HELLO")


def test_sent_line_carries_timestamp(mod, root):
    code, spy, _, out = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY x\nQUIT\n", root=root)
    line = [ln for ln in out.splitlines() if ln.startswith("SENT ")][0]
    assert float(line.split()[1]) > 1_700_000_000


def test_eof_without_quit_closes_cleanly(mod, root):
    code, spy, _, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000"], "SAY x\n", root=root)
    assert code == 0
    assert spy.closed is True


def test_sender_is_closed_even_on_error(mod, root):
    """Оркестратор гасит лида в finally, но и сам лид не оставляет живую
    сессию висеть на аварии."""
    code, spy, _, _ = _run(
        mod, ["--session", str(root / ".secrets" / "drill_lead.session"),
              "--peer", "777000", "--max-messages", "1"],
        "SAY a\nSAY b\n", root=root)
    assert code != 0
    assert spy.closed is True
