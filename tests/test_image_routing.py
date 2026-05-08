"""Phase 33.2: Tests for strong image generation routing in classify_message."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _classify(text: str) -> dict:
    """Import and call classify_message with empty state."""
    # Patch heavy dependencies to avoid network calls
    with patch.dict("sys.modules", {
        "app.services.task_planner": MagicMock(is_compound_task=lambda x: False),
        "app.services.quick_answer": MagicMock(is_simple_question=lambda x: False),
    }):
        from tools.jarvis_smart_telegram_control import classify_message
        return classify_message(text, {})


class TestStrongImageTriggers:
    """Strong image triggers must route to 'generate' BEFORE brain/compound checks."""

    def test_sdelai_foto_number(self):
        result = _classify("Сделай 4 фото девушки на пляже")
        assert result["intent"] == "generate"

    def test_sdelai_foto_no_number(self):
        result = _classify("Сделай фото кота в шляпе")
        assert result["intent"] == "generate"

    def test_sdelai_kartinku(self):
        result = _classify("Сделай картинку кота")
        assert result["intent"] == "generate"

    def test_sdelai_izobrazhenie(self):
        result = _classify("Сделай 2 изображения заката")
        assert result["intent"] == "generate"

    def test_sozdai_foto(self):
        result = _classify("Создай фото пейзажа")
        assert result["intent"] == "generate"

    def test_sozdai_kartinku(self):
        result = _classify("Создай картинку логотипа стартапа")
        assert result["intent"] == "generate"

    def test_sozdai_izobrazhenie_number(self):
        result = _classify("Создай 3 изображения города ночью")
        assert result["intent"] == "generate"

    def test_narisuy(self):
        result = _classify("Нарисуй логотип")
        assert result["intent"] == "generate"

    def test_narisuy_kota(self):
        result = _classify("нарисуй кота в космосе")
        assert result["intent"] == "generate"

    def test_sgeneriruy_foto(self):
        result = _classify("сгенерируй фото города")
        assert result["intent"] == "generate"

    def test_sgeneriruy_kartinku(self):
        result = _classify("Сгенерируй картинку для поста")
        assert result["intent"] == "generate"

    def test_en_generate_photo(self):
        result = _classify("generate 4 photos of a girl on the beach")
        assert result["intent"] == "generate"

    def test_en_create_image(self):
        result = _classify("create an image of a sunset")
        assert result["intent"] == "generate"

    def test_en_make_photo(self):
        result = _classify("make 4 photos of mountains")
        assert result["intent"] == "generate"

    def test_en_draw(self):
        result = _classify("draw a cat wearing a hat")
        assert result["intent"] == "generate"

    def test_en_generate_picture(self):
        result = _classify("generate a picture of abstract art")
        assert result["intent"] == "generate"


class TestImageRoutingDoesNotMatchOther:
    """Non-image queries must NOT be routed to generate."""

    def test_compare_not_generate(self):
        result = _classify("Сравни iPhone и Samsung")
        assert result["intent"] != "generate"

    def test_chto_takoe_not_generate(self):
        result = _classify("Что такое нейросеть")
        assert result["intent"] != "generate"

    def test_research_not_generate(self):
        result = _classify("найди информацию о ChatGPT")
        assert result["intent"] != "generate"

    def test_health_not_generate(self):
        result = _classify("статус системы")
        assert result["intent"] != "generate"
