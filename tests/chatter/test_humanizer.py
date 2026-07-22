

# --- Типографские тэллы: em-dash (бэклог п.1, поднят владельцем 2026-07-21) ---
from chatter.core.disclosure import HONESTY_MARKERS, honest_disclosure  # noqa: E402
from chatter.core.humanizer import humanize_typography  # noqa: E402


def test_em_dash_becomes_short_dash_with_spaces():
    # Живой тэлл из прогона volska: «— вона узгодить».
    assert humanize_typography(
        "Передам вас керівниці — вона узгодить деталі."
    ) == "Передам вас керівниці - вона узгодить деталі."


def test_em_dash_without_surrounding_spaces_also_normalised():
    assert "—" not in humanize_typography("Логотип—це основа бренду.")


def test_en_dash_ranges_are_left_alone():
    # 300–400 / 5–10 — это ДИАПАЗОНЫ, а не тэлл. Трогать их незачем, и цена
    # обязана дойти до лида ровно в том виде, в каком она есть в knowledge.
    text = "Логотип коштує 300–400 $, це 5–10 робочих днів."
    assert humanize_typography(text) == text


def test_honesty_marker_is_never_rewritten():
    """Ловушка из бэклога: HONESTY_MARKER содержит em-dash ВНУТРИ константы,
    которая сверяется дословно и является формулировкой гарантии честности.
    Слепой фильтр переписал бы раскрытие — маркер обязан пережить очистку
    байт-в-байт, на КАЖДОМ языке."""
    for lang, marker in HONESTY_MARKERS.items():
        disclosure = honest_disclosure(
            owner_id="Керівниця", persona_line="", language=lang)
        assert marker in disclosure          # предпосылка теста
        assert marker in humanize_typography(disclosure), lang


def test_text_around_protected_marker_is_still_cleaned():
    # Защищаем МАРКЕР, а не всё сообщение: остальной текст всё равно чистится.
    text = HONESTY_MARKERS["uk"] + ", а не жива людина — допоможу з питаннями."
    out = humanize_typography(text)
    assert HONESTY_MARKERS["uk"] in out
    assert "людина - допоможу" in out


def test_exclamation_becomes_period_including_greetings():
    # Владелец 2026-07-21: восклицательный знак — тэлл, сносим ВЕЗДЕ, включая
    # приветствие («Доброї ночі!» — ровно тот пример, что задел на живом тесте).
    assert humanize_typography("Доброї ночі!") == "Доброї ночі."
    assert humanize_typography(
        "Раді, що вирішили замовити!") == "Раді, що вирішили замовити."


def test_repeated_exclamations_collapse_to_one_period():
    assert humanize_typography("Супер!!! Дякую!") == "Супер. Дякую."


def test_mixed_interrobang_keeps_the_question():
    # «Справді?!» — вопрос остаётся вопросом, а не «Справді?.».
    assert humanize_typography("Справді?!") == "Справді?"
    assert humanize_typography("Невже!?") == "Невже?"


def test_question_mark_alone_is_untouched():
    text = "А від чого залежить ціна?"
    assert humanize_typography(text) == text
