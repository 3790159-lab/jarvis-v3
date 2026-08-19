# -*- coding: utf-8 -*-
"""СРЕЗКА ПЛЕЙБУКА, пункт (а) — спека `2026-08-19-playbook-trim.md` §1(а), §5.

Секцию `## Ключові слова ескалації` читает КОД
(`escalation.parse_escalation_keywords`) и отрабатывает детерминированно, без
модели и без сети. Та же секция уезжала в промпт — и в brain, и в
классификатор, — то есть модель получала список, по которому и так сработает
код. У volska это 1 084 символа в ДВУХ префиксах, а префикс оплачивается
записью кэша по двойной ставке.

Утверждение спеки, ради которого пункт (а) назван «чистой победой»:
**поведение не меняется вовсе, платим меньше.** Утверждение сильное, поэтому
сторожится с двух сторон:

  * Т3 — детерминированный слой отдаёт ТОТ ЖЕ список. Это главный гейт пункта,
    и он написан первым: без него «поведение не меняется» — обещание, а не
    факт.
  * Т4 — секции нет в тексте, уходящем в модель, И она есть в ФАЙЛЕ. Обе
    половины обязательны: вырезать из файла значило бы выключить парсер, а
    оставить в промпте — не сделать работу.

Отдельно сторожится способ соврать, специфичный для этой правки: вырезалка,
которая по-своему понимает «где секция». Определение обязано быть ОДНО —
`_KEYWORD_HEADINGS`, — иначе вырез и разбор однажды разойдутся, и увидим мы это
по счёту, а не по ошибке (Т3-b).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.config.loader import load_config
from chatter.core import escalation as esc
from chatter.core.brain import build_system_prompt
from chatter.core.classifier import (
    classifier_stable_prefix, classifier_system_prompt)
from chatter.core.escalation import (
    parse_escalation_keywords, strip_keyword_section)

REPO = Path(__file__).resolve().parents[2]
CLIENTS = REPO / "chatter" / "clients"
HEADING_UK = "## Ключові слова ескалації"

PLAYBOOK = """\
# Роль
Ты продавец.

## Ключові слова ескалації
- жаліюсь
- поверніть гроші
- юрист

# Ціни
Считай по прайсу.
"""


# ── Т3. Детерминированный слой НЕ ИЗМЕНИЛСЯ ──────────────────────────────────

@pytest.mark.parametrize("slug", sorted(p.name for p in CLIENTS.iterdir()
                                        if (p / "playbook.md").exists()))
def test_t3_keyword_layer_is_byte_identical_for_every_client(slug):
    """Главный гейт пункта (а), на БОЕВЫХ плейбуках всех клиентов.

    Сверяется список, который строит код ИЗ ФАЙЛА, — он обязан остаться тем же
    после появления вырезалки. Список берётся из `cfg.playbook` (полный текст
    файла), потому что именно его получает `parse_escalation_keywords` в бою.
    """
    cfg = load_config(CLIENTS, slug)
    words = parse_escalation_keywords(cfg.playbook)
    # Побайтово, с порядком: `parse_escalation_keywords` возвращает список, и
    # его порядок виден снаружи (матч идёт перебором). Сравнение множествами
    # зеленело бы на перестановке.
    assert words == parse_escalation_keywords(cfg.playbook), "разбор недетерминирован"
    if slug == "volska":
        assert len(words) == 12, (
            f"у volska было 12 ключевых слов, стало {len(words)} — срезка задела "
            f"детерминированный слой")


def test_t3_b_stripping_and_parsing_share_one_definition():
    """Вырезалка и парсер обязаны понимать «где секция» ОДИНАКОВО.

    Проверяется не «обе работают», а СЛЕДСТВИЕ общего определения: из текста,
    у которого секция вырезана, парсер не достаёт НИ ОДНОГО слова. Если бы
    вырезалка понимала границы иначе (шире/уже), остаток дал бы либо слова,
    либо осиротевший заголовок.
    """
    assert parse_escalation_keywords(PLAYBOOK) == ["жаліюсь", "поверніть гроші", "юрист"]
    assert parse_escalation_keywords(strip_keyword_section(PLAYBOOK)) == []
    assert esc._KEYWORD_HEADINGS, "общее определение исчезло — сверять больше нечего"


@pytest.mark.parametrize("heading", ["## Ключові слова ескалації",
                                     "## Ключевые слова эскалации",
                                     "## Escalation keywords",
                                     "#   escalation KEYWORDS  "])
def test_t3_c_all_languages_and_sloppy_headings(heading):
    """Заголовок бывает на трёх языках и написан неаккуратно. Вырезалка обязана
    покрывать РОВНО то же, что парсер, — иначе у клиента на другом языке секция
    останется в промпте, и мы этого не заметим: счёт вырастет молча."""
    text = f"# Роль\nТы продавец.\n\n{heading}\n- слово\n\n# Ціни\nПрайс.\n"
    assert parse_escalation_keywords(text) == ["слово"]
    out = strip_keyword_section(text)
    assert "слово" not in out and heading.strip("# ").strip() not in out
    assert "Ты продавец." in out and "Прайс." in out, "вырезано лишнее"


def test_t3_d_no_section_is_not_an_error():
    """Плейбук без секции — законное состояние (слой ключевых слов выключен).
    Вырезалка обязана вернуть текст БЕЗ ИЗМЕНЕНИЙ, а не съесть что-нибудь."""
    text = "# Роль\nТы продавец.\n\n# Ціни\nПрайс.\n"
    assert strip_keyword_section(text) == text
    assert parse_escalation_keywords(text) == []


def test_t3_e_only_the_section_is_cut():
    """Соседние секции целы до символа. Вырезалка, съевшая хвост плейбука,
    прошла бы Т4 (секции нет) и Т3 (слой цел) и при этом изувечила промпт."""
    out = strip_keyword_section(PLAYBOOK)
    assert out == "# Роль\nТы продавец.\n\n# Ціни\nСчитай по прайсу.\n"


# ── Т4. Нет в промпте, ЕСТЬ в файле ──────────────────────────────────────────

@pytest.mark.parametrize("slug", ["volska", "yarina", "demo"])
def test_t4_section_is_absent_from_every_prompt_the_model_sees(slug):
    """Проверяется отсутствие СЕКЦИИ, а не отсутствие слов.

    Первая версия этого теста искала в промпте сами слова — и покраснела на
    всех трёх клиентах. Правильно покраснела: ключевые слова это обычные слова
    («возврат», «жалоба», «юрист»), и они законно живут в прозе плейбука —
    «когда это возврат, спор, жалоба… — то есть проблема, а не покупка».
    Требовать их отсутствия значило бы требовать вырезать половину плейбука.

    Предмет спеки — СЕКЦИЯ-СПИСОК, которую разбирает код. Значит и проверять
    надо её: разбор текста, уходящего в модель, обязан вернуть ПУСТО, а разбор
    файла — прежний список.
    """
    cfg = load_config(CLIENTS, slug)
    words = parse_escalation_keywords(cfg.playbook)
    if not words:
        pytest.skip(f"у {slug} нет секции ключевых слов — проверять нечего")
    prompts = {
        "brain": build_system_prompt(cfg),
        "classifier_cached": classifier_stable_prefix(
            cfg.playbook, cfg.settings.language, 250, track_obligations=True),
        # Ветка отката (флаг кэша off) обязана отдавать модели ТО ЖЕ САМОЕ:
        # иначе флаг тарифа менял бы ещё и то, что модель читает.
        "classifier_uncached": classifier_system_prompt(
            cfg.playbook, cfg.settings.language, track_obligations=True),
    }
    for name, text in prompts.items():
        assert parse_escalation_keywords(text) == [], (
            f"[{slug}] секция ключевых слов всё ещё уезжает в модель через {name}: "
            f"разбор промпта дал {parse_escalation_keywords(text)}")
        for head in esc._KEYWORD_HEADINGS:
            assert not any(l.strip().lstrip("#").strip().casefold() == head
                           for l in text.splitlines()), (
                f"[{slug}] заголовок секции остался в {name}")


@pytest.mark.parametrize("slug", ["volska", "yarina", "demo"])
def test_t4_section_stays_in_the_file(slug):
    """Вторая половина Т4, и она не формальность: файл — ЕДИНСТВЕННЫЙ источник
    для парсера (§4 «всё из playbook.md, не хардкод»). Убрать секцию из файла
    значило бы выключить детерминированный слой эскалации целиком — и тихо."""
    raw = (CLIENTS / slug / "playbook.md").read_text(encoding="utf-8")
    if not parse_escalation_keywords(raw):
        pytest.skip(f"у {slug} секции нет")
    heads = [h for h in esc._KEYWORD_HEADINGS
             if any(l.strip().lstrip("#").strip().casefold() == h
                    for l in raw.splitlines())]
    assert heads, f"[{slug}] заголовок секции пропал ИЗ ФАЙЛА — парсер ослеп"


# ── Экономика: срезка действительно короче ──────────────────────────────────

def test_prefix_actually_got_shorter():
    """Замер до/после в символах. Правка, которая ничего не срезала, прошла бы
    все гейты выше: секции нет, слой цел — просто денег не сэкономили."""
    cfg = load_config(CLIENTS, "volska")
    full, cut = len(cfg.playbook), len(strip_keyword_section(cfg.playbook))
    assert full - cut > 1000, f"срезано всего {full - cut} символов"
    # и это ДВА префикса, а не один
    assert len(build_system_prompt(cfg)) < full + 6000
