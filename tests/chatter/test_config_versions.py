from __future__ import annotations

from pathlib import Path

from chatter.core.config_versions import (
    CONFIG_FILES, latest_version, previous_version, restore, snapshot,
)


def _client(tmp_path, knowledge="цена 5000") -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    (d / "persona.md").write_text("Аня", encoding="utf-8")
    (d / "knowledge.md").write_text(knowledge, encoding="utf-8")
    (d / "playbook.md").write_text("воронка", encoding="utf-8")
    (d / "settings.yaml").write_text("model: x", encoding="utf-8")
    return d


def test_snapshot_copies_all_config_files(tmp_path):
    d = _client(tmp_path)
    v = snapshot(d, now=1000.0)
    assert v is not None
    for f in CONFIG_FILES:
        assert (v / f).exists()
    assert (v / "knowledge.md").read_text(encoding="utf-8") == "цена 5000"


def test_snapshot_skips_if_identical_to_latest(tmp_path):
    d = _client(tmp_path)
    v1 = snapshot(d, now=1000.0)
    v2 = snapshot(d, now=2000.0)   # ничего не менялось
    assert v1 is not None
    assert v2 is None              # дубликат не создаём
    assert len(list((d / ".versions").iterdir())) == 1


def test_snapshot_new_version_when_content_changes(tmp_path):
    d = _client(tmp_path)
    snapshot(d, now=1000.0)
    (d / "knowledge.md").write_text("цена 7777", encoding="utf-8")
    v2 = snapshot(d, now=2000.0)
    assert v2 is not None
    assert len(list((d / ".versions").iterdir())) == 2


def test_latest_and_previous(tmp_path):
    d = _client(tmp_path)
    snapshot(d, now=1000.0)                                   # v0: 5000
    (d / "knowledge.md").write_text("цена 7777", encoding="utf-8")
    snapshot(d, now=2000.0)                                   # v1: 7777
    assert (latest_version(d) / "knowledge.md").read_text(encoding="utf-8") == "цена 7777"
    assert (previous_version(d) / "knowledge.md").read_text(encoding="utf-8") == "цена 5000"


def test_previous_is_none_with_one_version(tmp_path):
    d = _client(tmp_path)
    snapshot(d, now=1000.0)
    assert previous_version(d) is None


def test_restore_copies_files_back(tmp_path):
    d = _client(tmp_path)
    snapshot(d, now=1000.0)                                   # good: 5000
    (d / "knowledge.md").write_text("СЛОМАНО 7777", encoding="utf-8")
    restore(d, latest_version(d))
    assert (d / "knowledge.md").read_text(encoding="utf-8") == "цена 5000"


def test_snapshot_prunes_to_keep(tmp_path):
    d = _client(tmp_path)
    for i in range(5):
        (d / "knowledge.md").write_text(f"цена {i}", encoding="utf-8")
        snapshot(d, now=1000.0 + i, keep=3)
    assert len(list((d / ".versions").iterdir())) == 3       # старые подрезаны
    # остались самые свежие (2,3,4)
    assert (latest_version(d) / "knowledge.md").read_text(encoding="utf-8") == "цена 4"
