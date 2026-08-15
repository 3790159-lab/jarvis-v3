"""Бот не знав поточного часу (Ольга привіталася «Добрий день» ввечері).
Блок поточного часу (дата/день тижня/час/частина доби, Europe/Kyiv) додано в
uncached_suffix brain-промпту — ПІСЛЯ cache-breakpoint'а. Головний тест тут —
байт-в-байт стабільність кешованого префікса при РІЗНОМУ підставленому часі
(регресія 23.07: мінливе в префіксі вбиває кеш)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from chatter.core.brain import Brain, KYIV_TZ, _day_part, build_time_block
from chatter.core.llm import FakeLLM
from tests.chatter.test_loader import _make_client
from chatter.config.loader import load_config


def _cfg(tmp_path):
    _make_client(tmp_path)
    return load_config(tmp_path, "demo")


class _RecordingLLM:
    """Пише system і suffix ОКРЕМО (FakeLLM їх склеює для сумісності старих
    тестів) — саме system тут і є кешований префікс, який мусить бути
    байт-в-байт незмінним."""

    def __init__(self, replies=None):
        self.replies = list(replies or ["ок"])
        self.calls: list[dict] = []
        self.last_stop_reason = None

    def complete(self, system, messages, *, max_tokens, no_thinking=False,
                 uncached_suffix=None, tag=""):
        self.calls.append({"system": system, "suffix": uncached_suffix,
                           "tag": tag})
        return self.replies.pop(0) if self.replies else "ок"


# --- границі частин доби -----------------------------------------------------
def test_day_part_boundaries():
    assert _day_part(4) == "ніч"
    assert _day_part(5) == "ранок"
    assert _day_part(10) == "ранок"
    assert _day_part(11) == "день"
    assert _day_part(16) == "день"
    assert _day_part(17) == "вечір"
    assert _day_part(21) == "вечір"
    assert _day_part(22) == "ніч"
    assert _day_part(23) == "ніч"
    assert _day_part(0) == "ніч"


# --- суффикс несёт время -------------------------------------------------------
def test_build_time_block_carries_date_weekday_time_and_daypart():
    dt = datetime(2026, 7, 28, 19, 5, tzinfo=KYIV_TZ)  # вівторок, вечір
    block = build_time_block(dt)
    assert "28.07.2026" in block
    assert "вівторок" in block
    assert "19:05" in block
    assert "вечір" in block
    assert "Europe/Kyiv" in block


def test_build_time_block_converts_from_other_timezone():
    # 23:30 UTC того ж дня = 02:30 ночі 29.07 у Києві (+3 влітку)
    dt_utc = datetime(2026, 7, 28, 23, 30, tzinfo=ZoneInfo("UTC"))
    block = build_time_block(dt_utc)
    assert "29.07.2026" in block
    assert "02:30" in block
    assert "ніч" in block


def test_brain_reply_suffix_carries_time_block(tmp_path):
    cfg = _cfg(tmp_path)
    llm = FakeLLM(scripted=["ок"])
    brain = Brain(llm, cfg)
    dt = datetime(2026, 7, 28, 8, 15, tzinfo=KYIV_TZ)  # ранок

    brain.reply([{"role": "user", "text": "Добрий день"}], now=dt)

    suffix = llm.calls[0]["uncached_suffix"]
    assert suffix and "ПОТОЧНИЙ ЧАС" in suffix
    assert "ранок" in suffix
    assert "08:15" in suffix


# --- ГОЛОВНИЙ СТОРОЖ КЕШУ: стабільний префікс незмінний при різному часі -----
def test_stable_prefix_is_byte_identical_across_different_time(tmp_path):
    cfg = _cfg(tmp_path)
    a, b = _RecordingLLM(), _RecordingLLM()
    brain_a, brain_b = Brain(a, cfg), Brain(b, cfg)

    brain_a.reply([{"role": "user", "text": "hi"}],
                  now=datetime(2026, 7, 28, 6, 0, tzinfo=KYIV_TZ))
    brain_b.reply([{"role": "user", "text": "hi"}],
                  now=datetime(2026, 7, 28, 23, 45, tzinfo=KYIV_TZ))

    assert a.calls[0]["system"] == b.calls[0]["system"], (
        "стабильный префикс не должен шевельнуться при разном времени")
    assert a.calls[0]["suffix"] != b.calls[0]["suffix"], (
        "разница обязана быть в хвосте (uncached_suffix)")


def test_stable_prefix_carries_no_time_text(tmp_path):
    """Негативная проверка: время не просочилось в кэшируемый префикс —
    иначе «байт-в-байт» можно было бы случайно пройти забыв его вычистить."""
    cfg = _cfg(tmp_path)
    llm = _RecordingLLM()
    brain = Brain(llm, cfg)
    brain.reply([{"role": "user", "text": "hi"}],
                now=datetime(2026, 7, 28, 6, 0, tzinfo=KYIV_TZ))
    prefix, suffix = llm.calls[0]["system"], llm.calls[0]["suffix"]
    assert "ПОТОЧНИЙ ЧАС" not in prefix
    assert "ПОТОЧНИЙ ЧАС" in suffix


def test_same_moment_gives_identical_suffix_across_two_brain_instances(tmp_path):
    """Антипод: одинаковое время → одинаковый suffix (детерминизм блока)."""
    cfg = _cfg(tmp_path)
    a, b = _RecordingLLM(), _RecordingLLM()
    dt = datetime(2026, 7, 28, 12, 0, tzinfo=KYIV_TZ)
    Brain(a, cfg).reply([{"role": "user", "text": "hi"}], now=dt)
    Brain(b, cfg).reply([{"role": "user", "text": "hi"}], now=dt)
    assert a.calls[0]["suffix"] == b.calls[0]["suffix"]
