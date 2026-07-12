# -*- coding: utf-8 -*-
"""Клиентский бренд-конфиг (clients/<name>/brand.md). $0, чтение с диска, без сети.

load_brand_config читает ТОЛЬКО YAML-frontmatter файла brand.md (человекочитаемое
markdown-тело после него не парсится). parse_client_arg вытаскивает необязательный
префикс ``client=<name>`` из текста команды.
"""
from pathlib import Path

from app.services import brand_config as bc


def test_load_brand_config_reads_real_vera_ai_ua_frontmatter():
    brand = bc.load_brand_config("vera_ai_ua")
    assert brand is not None
    assert brand["lang"] == "uk"
    assert "молодий" in brand["tone"]
    assert "політика" in brand["forbidden"]
    assert brand["hashtags_count"] == 7


def test_load_brand_config_returns_none_for_unknown_client():
    assert bc.load_brand_config("no_such_client_xyz") is None


def test_load_brand_config_returns_none_for_empty_client():
    assert bc.load_brand_config(None) is None
    assert bc.load_brand_config("") is None


def test_load_brand_config_returns_none_when_frontmatter_missing(tmp_path, monkeypatch):
    client_dir = tmp_path / "no_frontmatter_client"
    client_dir.mkdir()
    (client_dir / "brand.md").write_text("# Просто markdown, без frontmatter\n", encoding="utf-8")
    monkeypatch.setattr(bc, "CLIENTS_DIR", tmp_path)
    assert bc.load_brand_config("no_frontmatter_client") is None


def test_load_brand_config_returns_none_on_malformed_yaml(tmp_path, monkeypatch):
    client_dir = tmp_path / "bad_yaml_client"
    client_dir.mkdir()
    (client_dir / "brand.md").write_text(
        "---\ntone: [молодий, живий\n---\nтіло\n", encoding="utf-8",
    )
    monkeypatch.setattr(bc, "CLIENTS_DIR", tmp_path)
    assert bc.load_brand_config("bad_yaml_client") is None


def test_parse_client_arg_extracts_client_prefix():
    client, rest = bc.parse_client_arg("client=vera_ai_ua новий сезонний напій")
    assert client == "vera_ai_ua"
    assert rest == "новий сезонний напій"


def test_parse_client_arg_case_insensitive_prefix():
    client, rest = bc.parse_client_arg("CLIENT=vera_ai_ua тема")
    assert client == "vera_ai_ua"
    assert rest == "тема"


def test_parse_client_arg_no_prefix_returns_none_and_full_query():
    client, rest = bc.parse_client_arg("звичайна тема без клієнта")
    assert client is None
    assert rest == "звичайна тема без клієнта"


def test_parse_client_arg_empty_query():
    client, rest = bc.parse_client_arg("")
    assert client is None
    assert rest == ""


def test_parse_client_arg_client_only_no_topic():
    client, rest = bc.parse_client_arg("client=vera_ai_ua")
    assert client == "vera_ai_ua"
    assert rest == ""
