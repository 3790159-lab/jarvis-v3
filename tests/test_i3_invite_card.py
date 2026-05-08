"""Phase I.3: Fix /invite_card handler dict parsing."""
from __future__ import annotations

import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _run_invite_card(query, mock_result):
    import tools.photo_studio_telegram as mod

    sent = []
    photos = []

    with patch("app.services.party_mode.generate_invite_card", return_value=mock_result):
        mod.handle_invite_card(
            chat_id="c1",
            query=query,
            send_fn=lambda cid, text, **kw: sent.append(text),
            send_photo_fn=lambda cid, url, **kw: photos.append((url, kw.get("caption", ""))),
        )
    return sent, photos


def test_invite_card_sends_photo_when_card_url_present():
    sent, photos = _run_invite_card(
        'Иван "День рождения" "5 мая"',
        {"card_url": "https://img.example.com/card.jpg", "personal_text": "Дорогой Иван!"},
    )
    assert photos, "Photo should be sent"
    assert photos[0][0] == "https://img.example.com/card.jpg"


def test_invite_card_caption_from_personal_text():
    sent, photos = _run_invite_card(
        'Анна Свадьба Июнь',
        {"card_url": "https://img.example.com/card.jpg", "personal_text": "Поздравляем!"},
    )
    assert photos[0][1] == "Поздравляем!"


def test_invite_card_error_when_no_card_url():
    sent, photos = _run_invite_card(
        'Иван "День рождения" "5 мая"',
        {"card_url": "", "personal_text": "text"},
    )
    assert not photos
    assert any("Не удалось" in s for s in sent)


def test_invite_card_long_text_sent_separately():
    long_text = "x" * 2000
    sent, photos = _run_invite_card(
        'Иван "День рождения" "5 мая"',
        {"card_url": "https://img.example.com/card.jpg", "personal_text": long_text},
    )
    assert photos
    assert any("Полный текст" in s for s in sent), "Long text must be sent as separate message"


def test_invite_card_no_args_shows_usage():
    import tools.photo_studio_telegram as mod

    sent = []
    mod.handle_invite_card(
        chat_id="c1",
        query="",
        send_fn=lambda cid, text, **kw: sent.append(text),
        send_photo_fn=lambda *a, **kw: None,
    )
    assert sent
    assert "invite_card" in sent[0] or "Использование" in sent[0]
