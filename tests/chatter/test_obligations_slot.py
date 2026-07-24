"""Слой обязательств (спека 2026-07-24-chatter-obligations-slot-design.md §3/§5).

Чистые функции: merge/переходы статусов + рендер блока для brain. Ноль I/O.
"""
from __future__ import annotations

from chatter.core.obligations_slot import (
    Obligation,
    merge_obligations,
    render_slot_block,
    render_current_for_classifier,
    okey_for,
)

DAY = 86400.0


def _open(kind, detail, owed_by="bot", slug=None):
    d = {"kind": kind, "owed_by": owed_by, "status": "open", "detail": detail}
    if slug:
        d["slug"] = slug
    return d


# --- merge: создание -------------------------------------------------------
def test_new_open_obligation_is_created_and_stamped():
    out = merge_obligations([], [_open("brief", "обіцяний бриф")],
                            now=1000.0, current_msg_id=42)
    assert len(out) == 1
    o = out[0]
    assert isinstance(o, Obligation)
    assert o.kind == "brief" and o.okey == "brief" and o.owed_by == "bot"
    assert o.status == "open"
    assert o.created_msg_id == 42 and o.created_ts == 1000.0
    assert o.closed_msg_id is None and o.closed_ts is None


def test_other_kind_keyed_by_slug():
    out = merge_obligations([], [_open("other", "надіслати договір", slug="contract")],
                            now=1.0, current_msg_id=1)
    assert out[0].okey == "other:contract"
    assert okey_for("other", "contract") == "other:contract"
    assert okey_for("brief") == "brief"


def test_detail_truncated_to_80():
    out = merge_obligations([], [_open("brief", "x" * 200)], now=1.0, current_msg_id=1)
    assert len(out[0].detail) <= 80


# --- merge: переходы -------------------------------------------------------
def test_open_to_delivered_stamps_closure_not_recreate():
    existing = merge_obligations([], [_open("brief", "бриф")], now=1000.0, current_msg_id=42)
    out = merge_obligations(
        existing,
        [{"kind": "brief", "owed_by": "bot", "status": "delivered", "detail": "квал питання задані"}],
        now=2000.0, current_msg_id=50)
    assert len(out) == 1
    o = out[0]
    assert o.status == "delivered"
    assert o.created_msg_id == 42 and o.created_ts == 1000.0  # НЕ пересоздано
    assert o.closed_msg_id == 50 and o.closed_ts == 2000.0


def test_unmentioned_obligation_is_preserved():
    existing = merge_obligations(
        [], [_open("brief", "бриф"), _open("recalc", "пересчёт")],
        now=1000.0, current_msg_id=42)
    # классификатор упомянул только brief→delivered; recalc не трогаем
    out = merge_obligations(
        existing,
        [{"kind": "brief", "owed_by": "bot", "status": "delivered", "detail": "готово"}],
        now=2000.0, current_msg_id=50)
    byk = {o.okey: o for o in out}
    assert byk["recalc"].status == "open"      # сохранено, не выброшено
    assert byk["brief"].status == "delivered"


def test_delivered_reopened_clears_closure():
    existing = merge_obligations([], [_open("brief", "бриф")], now=1000.0, current_msg_id=1)
    existing = merge_obligations(
        existing, [{"kind": "brief", "owed_by": "bot", "status": "delivered", "detail": "done"}],
        now=2000.0, current_msg_id=2)
    out = merge_obligations(existing, [_open("brief", "знову обіцяний")],
                            now=3000.0, current_msg_id=3)
    o = out[0]
    assert o.status == "open"
    assert o.closed_msg_id is None and o.closed_ts is None


# --- рендер блока brain ----------------------------------------------------
def test_render_open_and_recent_delivered_sections():
    obs = merge_obligations(
        [], [_open("brief", "задати квал питання"), _open("examples", "скинути портфоліо")],
        now=1000.0, current_msg_id=1)
    obs = merge_obligations(
        obs, [{"kind": "examples", "owed_by": "bot", "status": "delivered", "detail": "портфоліо надіслано"}],
        now=1000.0 + 2 * DAY, current_msg_id=2)
    block = render_slot_block(obs, now=1000.0 + 2 * DAY)
    assert "ВІДКРИТІ ЗОБОВ'ЯЗАННЯ" in block
    assert "задати квал питання" in block          # открытый brief
    assert "ЗАКРИТО НЕДАВНО" in block
    assert "портфоліо надіслано" in block           # свежий delivered
    assert "✓" in block


def test_render_omits_old_delivered_and_cancelled():
    obs = merge_obligations([], [_open("brief", "бриф")], now=0.0, current_msg_id=1)
    obs = merge_obligations(
        obs, [{"kind": "brief", "owed_by": "bot", "status": "delivered", "detail": "done"}],
        now=10 * DAY, current_msg_id=2)
    # смотрим на 8 дней ПОЗЖЕ закрытия (>7д окна «недавно»)
    block = render_slot_block(obs, now=10 * DAY + 8 * DAY, recent_days=7)
    assert block == ""


def test_render_empty_when_no_bot_obligations():
    assert render_slot_block([], now=1.0) == ""


def test_render_only_bot_owed_open_shown():
    obs = merge_obligations(
        [], [_open("brief", "бот винен бриф", owed_by="bot"),
             _open("other", "лід обіцяв подумати", owed_by="client", slug="think")],
        now=1.0, current_msg_id=1)
    block = render_slot_block(obs, now=2.0)
    assert "бот винен бриф" in block
    assert "лід обіцяв подумати" not in block       # client-обязательства brain не грузим


# --- рендер для классификатора (только открытые) ---------------------------
def test_render_for_classifier_lists_open_only():
    obs = merge_obligations([], [_open("brief", "бриф"), _open("recalc", "пересчёт")],
                            now=1.0, current_msg_id=1)
    obs = merge_obligations(
        obs, [{"kind": "recalc", "owed_by": "bot", "status": "delivered", "detail": "done"}],
        now=2.0, current_msg_id=2)
    s = render_current_for_classifier(obs)
    assert "brief" in s and "бриф" in s
    assert "recalc" not in s        # закрытое классификатору не показываем


def test_render_for_classifier_empty():
    assert render_current_for_classifier([]) == ""


# --- политика владения owner_write (спека §3) ------------------------------
def test_filter_model_updates_drops_owner_write_closures():
    from chatter.core.obligations_slot import filter_model_updates
    out = filter_model_updates([
        {"kind": "owner_write", "status": "delivered", "detail": "x"},
        {"kind": "owner_write", "status": "cancelled", "detail": "x"},
        {"kind": "owner_write", "status": "open", "detail": "обіцяно напише"},
        {"kind": "brief", "status": "delivered", "detail": "y"},
    ])
    pairs = [(u["kind"], u["status"]) for u in out]
    assert ("owner_write", "delivered") not in pairs   # закрытие — не модель
    assert ("owner_write", "cancelled") not in pairs
    assert ("owner_write", "open") in pairs            # создание — можно
    assert ("brief", "delivered") in pairs             # другие kind — как есть


# --- owed_by brief/examples/recalc = ИНВАРИАНТ КОДА, не выбор модели --------
# render_slot_block показывает ТОЛЬКО owed_by=bot; client-owed brief не доедет до
# brain (баг дрила Д-10 2026-07-24: классификатор прислал owed_by=client → obl=n=1,
# но пустой рендер). Гарантия — в коде, не в промпте.
def test_filter_pins_owed_by_bot_for_brief_even_if_model_says_client():
    from chatter.core.obligations_slot import filter_model_updates
    out = filter_model_updates([
        {"kind": "brief", "owed_by": "client", "status": "open", "detail": "бриф"},
    ])
    assert out[0]["owed_by"] == "bot"


def test_filter_pins_owed_by_bot_for_examples_and_recalc_when_client():
    from chatter.core.obligations_slot import filter_model_updates
    out = filter_model_updates([
        {"kind": "examples", "owed_by": "client", "status": "open", "detail": "e"},
        {"kind": "recalc", "owed_by": "client", "status": "open", "detail": "r"},
    ])
    assert all(u["owed_by"] == "bot" for u in out)


def test_filter_pins_owed_by_bot_when_model_omits_it():
    from chatter.core.obligations_slot import filter_model_updates
    out = filter_model_updates([
        {"kind": "brief", "status": "open", "detail": "бриф"},
    ])
    assert out[0]["owed_by"] == "bot"


def test_filter_leaves_other_kind_owed_by_choice_intact():
    # 'other' — выбор владения осмыслен (лід щось винен нам): НЕ форсим.
    from chatter.core.obligations_slot import filter_model_updates
    out = filter_model_updates([
        {"kind": "other", "owed_by": "client", "status": "open",
         "detail": "лід обіцяв подумати", "slug": "think"},
    ])
    assert out[0]["owed_by"] == "client"


# --- анти-фрагментация: не плодим other поверх открытого канонического долга -
# (дрил Д-10 T2 2026-07-24: при открытом brief классификатор создал дублирующее
#  other про те же уточнення → cap ≤5 забивается клонами одного долга).
def test_new_other_dropped_when_canonical_obligation_open():
    existing = merge_obligations(
        [], [_open("brief", "бріф")], now=1.0, current_msg_id=1)
    result = merge_obligations(
        existing, [_open("other", "уточнити референси", slug="ref")],
        now=2.0, current_msg_id=2)
    assert "brief" in {o.okey for o in result}
    assert not any(o.kind == "other" for o in result)   # дубль-other отброшен


def test_new_other_kept_when_no_canonical_open():
    # без открытого канонического долга «other» легитимен — не над-фильтровать.
    result = merge_obligations(
        [], [_open("other", "щось особливе", slug="x")], now=1.0, current_msg_id=1)
    assert any(o.kind == "other" for o in result)


def test_new_other_kept_when_canonical_only_delivered():
    # канонический долг ЗАКРЫТ (delivered) → новый other не дублирует активное.
    existing = merge_obligations(
        [], [{"kind": "brief", "owed_by": "bot", "status": "delivered",
              "detail": "готово"}], now=1.0, current_msg_id=1)
    result = merge_obligations(
        existing, [_open("other", "нове питання", slug="q")],
        now=2.0, current_msg_id=2)
    assert any(o.kind == "other" for o in result)


# --- owner_write: модель НЕ переоткрывает уже существующий (спека §3, дрил T4) --
# Регрессия дрила Д-10 T4 2026-07-24: пока платёжный контекст в окне, классификатор
# на каждом ходу заново шлёт owner_write open → без гарда merge сбрасывал closure
# code-доставленной строки. filter теперь роняет owner_write open, если строка уже
# есть (в любом статусе): её жизненный цикл после создания ведёт ТОЛЬКО код.
def test_filter_drops_owner_write_open_when_row_already_exists():
    from chatter.core.obligations_slot import filter_model_updates
    existing = merge_obligations(
        [], [{"kind": "owner_write", "owed_by": "bot", "status": "delivered",
              "detail": "карточка доставлена"}], now=1.0, current_msg_id=93)
    out = filter_model_updates(
        [{"kind": "owner_write", "owed_by": "bot", "status": "open",
          "detail": "керівниця напише"}],
        existing=existing)
    assert out == []          # переоткрытие code-owned owner_write отброшено


def test_filter_keeps_owner_write_open_for_first_creation():
    # строки owner_write ещё нет → модель ВПРАВЕ её создать (open проходит).
    from chatter.core.obligations_slot import filter_model_updates
    existing = merge_obligations(
        [], [_open("brief", "бриф")], now=1.0, current_msg_id=1)
    out = filter_model_updates(
        [{"kind": "owner_write", "owed_by": "bot", "status": "open",
          "detail": "керівниця напише"}],
        existing=existing)
    assert [(u["kind"], u["status"]) for u in out] == [("owner_write", "open")]


def test_filter_owner_write_backward_compatible_without_existing():
    # старая сигнатура (без existing) = поведение до фикса: open проходит.
    from chatter.core.obligations_slot import filter_model_updates
    out = filter_model_updates(
        [{"kind": "owner_write", "owed_by": "bot", "status": "open", "detail": "x"}])
    assert [(u["kind"], u["status"]) for u in out] == [("owner_write", "open")]
