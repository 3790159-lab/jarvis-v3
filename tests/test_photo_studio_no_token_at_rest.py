"""DEV-74/DEV-75: готовая ссылка не переживает вызов, file_id хранится вместо неё.

Требование владельца (25.08.2026): «чиним хранение, не путь».

Почему именно так. Файловый API Telegram отдаёт ссылку вида
``/file/bot<ТОКЕН>/<file_path>`` — токен является частью пути по спецификации,
и на стороне URL это не обходится. Значит обходить надо на стороне ХРАНЕНИЯ:
в состоянии диалога лежит ``file_id`` (не секрет), а ссылка живёт внутри одного
вызова и на диск не попадает.

Побочно ссылка ещё и НЕДОЛГОВЕЧНА: `file_path` от Telegram протухает примерно
через час. То есть сохранённая ссылка — это не только утечка, но и битый линк:
хранить её нет никакой выгоды, только риск.

DEV-75 (вариант Б владельца): брошенные состояния перестаём хранить вовсе.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Токен в любом виде: `bot<цифры>:` — так он выглядит и в URL, и в конфиге.
TOKEN_RE = re.compile(r"bot\d{6,}:")

CHAT = "424242"


@pytest.fixture()
def ps(tmp_path, monkeypatch):
    """Модуль с ПОДМЕНЁННЫМ каталогом состояния.

    Настоящий `state/conversations/` трогать нельзя: там живут `.jsonl`
    истории ассистента, и тест, промахнувшийся мимо расширения, стёр бы
    переписку. Каталог подменяем, а не «аккуратно ходим» по живому.
    """
    import tools.photo_studio_telegram as mod
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    monkeypatch.setattr(mod, "_CONV_DIR", conv_dir)
    monkeypatch.setattr(mod, "_ROOT", tmp_path)
    return mod


def _conv_bytes(ps_mod) -> str:
    """Всё, что реально легло на диск в каталоге состояний."""
    return "\n".join(
        p.read_text(encoding="utf-8") for p in ps_mod._CONV_DIR.glob("*.json")
    )


def _noop(*a, **k):
    return None


# ── 1. Ссылка не переживает вызов ───────────────────────────────────────────
def test_source_step_stores_file_id_not_url(ps):
    ps.save_conv(CHAT, {"step": "faceswap_source", "data": {}})
    ps.handle_faceswap_photo_step(
        CHAT, "https://api.telegram.org/file/bot123456789:SECRET/photos/a.jpg",
        _noop, _noop, file_id="FILEID_SRC",
    )
    data = ps.load_conv(CHAT)["data"]
    assert data.get("source_file_id") == "FILEID_SRC"
    assert "source_url" not in data, "готовая ссылка снова легла на диск"
    assert not TOKEN_RE.search(_conv_bytes(ps)), "токен попал в состояние диалога"


def test_target_step_stores_file_id_not_url(ps):
    ps.save_conv(CHAT, {"step": "faceswap_target", "data": {"source_file_id": "S"}})
    ps.handle_faceswap_photo_step(
        CHAT, "https://api.telegram.org/file/bot123456789:SECRET/photos/b.jpg",
        _noop, _noop, file_id="FILEID_TGT",
    )
    data = ps.load_conv(CHAT)["data"]
    assert data.get("target_file_id") == "FILEID_TGT"
    assert "target_url" not in data
    assert not TOKEN_RE.search(_conv_bytes(ps))


def test_lora_collecting_stores_file_ids_not_urls(ps):
    ps.save_conv(CHAT, {"step": "lora_collecting", "data": {"photo_file_ids": []}})
    for i in range(3):
        ps.save_conv(CHAT, {**ps.load_conv(CHAT), "step": "lora_collecting"})
        ps.handle_faceswap_photo_step(
            CHAT, f"https://api.telegram.org/file/bot123456789:SECRET/photos/{i}.jpg",
            _noop, _noop, file_id=f"FID{i}",
        )
    data = ps.load_conv(CHAT)["data"]
    assert data.get("photo_file_ids") == ["FID0", "FID1", "FID2"]
    assert "photos" not in data
    assert not TOKEN_RE.search(_conv_bytes(ps))


# ── 2. Fail-closed: нет file_id — не храним НИЧЕГО ──────────────────────────
def test_missing_file_id_persists_nothing_and_says_so(ps):
    """Молчаливый откат к хранению ссылки вернул бы дефект целиком.

    Поэтому отсутствие file_id — это громкий отказ, а не «сохраним как раньше».
    """
    ps.save_conv(CHAT, {"step": "faceswap_source", "data": {}})
    said = []
    ps.handle_faceswap_photo_step(
        CHAT, "https://api.telegram.org/file/bot123456789:SECRET/photos/a.jpg",
        lambda cid, t, **k: said.append(t), _noop, file_id=None,
    )
    data = ps.load_conv(CHAT)["data"]
    assert "source_file_id" not in data and "source_url" not in data
    assert said, "отказ произошёл молча"
    assert not TOKEN_RE.search(_conv_bytes(ps))


# ── 3. Ссылка разменивается В МОМЕНТ использования ──────────────────────────
def test_exec_resolves_file_ids_at_use_time(ps, monkeypatch):
    ps.save_conv(CHAT, {"step": "faceswap_confirm",
                        "data": {"source_file_id": "S", "target_file_id": "T"}})
    resolved = {"S": "https://api.telegram.org/file/bot9:X/s.jpg",
                "T": "https://api.telegram.org/file/bot9:X/t.jpg"}
    monkeypatch.setattr(ps, "_resolve_photo_url", lambda fid: resolved.get(fid))
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (do(), None))
    got = {}
    monkeypatch.setattr("app.services.face_swap.face_swap_basic",
                        lambda s, t: got.update(src=s, tgt=t) or "http://res")
    ps.handle_faceswap_callback(CHAT, "fs:exec:basic", _noop, _noop)
    assert got == {"src": resolved["S"], "tgt": resolved["T"]}


def test_exec_fails_loudly_when_link_cannot_be_resolved(ps, monkeypatch):
    """Протухший file_path — обычное дело. Отказ обязан быть виден."""
    ps.save_conv(CHAT, {"step": "faceswap_confirm",
                        "data": {"source_file_id": "S", "target_file_id": "T"}})
    monkeypatch.setattr(ps, "_resolve_photo_url", lambda fid: None)
    calls = {"n": 0}
    monkeypatch.setattr("app.services.face_swap.face_swap_basic",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    said = []
    ps.handle_faceswap_callback(CHAT, "fs:exec:basic", lambda cid, t, **k: said.append(t), _noop)
    assert calls["n"] == 0, "пошли платить, не имея ссылок"
    assert said


# ── 4. «Моё лицо» тоже file_id ──────────────────────────────────────────────
def test_my_face_is_stored_as_file_id(ps, monkeypatch):
    ps.save_conv(CHAT, {"step": "faceswap_confirm",
                        "data": {"source_file_id": "SRCID", "target_file_id": "T"}})
    monkeypatch.setattr(ps, "_resolve_photo_url",
                        lambda fid: f"https://api.telegram.org/file/bot9:X/{fid}.jpg")
    monkeypatch.setattr(ps, "guard_spend", lambda uid, un, est, do: (do(), None))
    monkeypatch.setattr("app.services.face_swap.face_swap_basic", lambda s, t: "http://res")
    ps.handle_faceswap_callback(CHAT, "fs:exec:basic", _noop, _noop)
    face = ps._ROOT / "state" / "my_face_file_id.txt"
    assert face.exists(), "лицо не сохранено"
    assert face.read_text(encoding="utf-8").strip() == "SRCID"
    assert not TOKEN_RE.search(face.read_text(encoding="utf-8"))


# ── 5. DEV-75: брошенные состояния не живут вечно ───────────────────────────
def test_prune_removes_abandoned_state(ps):
    old = ps._CONV_DIR / "999.json"
    old.write_text('{"step": "faceswap_confirm"}', encoding="utf-8")
    import os
    stale = time.time() - 60 * 60 * 48
    os.utime(old, (stale, stale))
    ps.prune_abandoned_convs(max_age_hours=24)
    assert not old.exists(), "брошенное состояние пережило TTL"


def test_prune_keeps_fresh_state(ps):
    fresh = ps._CONV_DIR / "888.json"
    fresh.write_text('{"step": "faceswap_source"}', encoding="utf-8")
    ps.prune_abandoned_convs(max_age_hours=24)
    assert fresh.exists(), "живой диалог убит уборкой"


def test_prune_never_touches_assistant_history(ps):
    """🔴 В ОДНОМ каталоге лежат `.json` (это состояние) и `.jsonl` (история
    разговоров ассистента). Уборка по `*` вместо `*.json` стёрла бы переписку.

    Это не гипотеза: соседство двух механик в одном каталоге уже дало ошибку
    счёта при инвентаризации ПДн (скелет говорил «4 файла», их пять).
    """
    import os
    history = ps._CONV_DIR / "777.jsonl"
    history.write_text('{"role": "user", "content": "живая переписка"}\n', encoding="utf-8")
    stale = time.time() - 60 * 60 * 24 * 365
    os.utime(history, (stale, stale))
    ps.prune_abandoned_convs(max_age_hours=24)
    assert history.exists(), "уборка состояний съела историю ассистента"


def test_saving_prunes_but_keeps_the_file_being_written(ps):
    """Уборка на записи не должна съесть то, что прямо сейчас пишут."""
    import os
    old = ps._CONV_DIR / "111.json"
    old.write_text("{}", encoding="utf-8")
    stale = time.time() - 60 * 60 * 48
    os.utime(old, (stale, stale))
    ps.save_conv(CHAT, {"step": "faceswap_source", "data": {}})
    assert not old.exists()
    assert ps.load_conv(CHAT)["step"] == "faceswap_source"


# ── 6. Сторож по ФАКТУ — ПЕРЕЕХАЛ В ops_watchdog ───────────────
# Проверка «под живым state/ нет bot<цифры>:» была здесь и смотрела на
# `state/` относительно себя. В worktree мерж-гейта `state/` гитигнорен и
# отсутствует → тест скипался, то есть молчал ПО ПОСТРОЕНИЮ ровно там, где его
# и гоняли. Скип читается как «всё чисто» — это лампа, а не сторож.
#
# Теперь это проба `probe_token_at_rest` в `scripts/ops_watchdog.py`: она смотрит
# на LIVE_TREE и видит боевой диск. Сторожа на неё —
# `tests/test_ops_watchdog_token_at_rest.py`.
