# -*- coding: utf-8 -*-
"""Money hole (b): the block_m global $10/day budget is RETIRED.

It read the stale state/personas/expenses.jsonl, never fired in practice, and
duplicated the real per-user audit friend-limit (access_control.check_limit).
Retiring it = CostTracker.check_limit always allows, so neither the explicit
trainer/generator checks nor log_expense's internal check block anything.
$0, mocks only, self-contained (no `replicate` import).
"""
import asyncio
import datetime
import json

from app.services.block_m_common.cost_tracker import CostTracker, DailyLimitExceeded


def _prefill_today(path, usd):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path.write_text(json.dumps({"ts": ts, "operation": "x", "cost_usd": usd}) + "\n",
                    encoding="utf-8")


def test_check_limit_always_allows_even_over_old_cap(tmp_path):
    f = tmp_path / "expenses.jsonl"
    _prefill_today(f, 50.0)                     # far over the old $10 cap
    ct = CostTracker(expenses_file=f)
    can_proceed, _remaining = asyncio.run(ct.check_limit())
    assert can_proceed is True                 # retired global budget never blocks


def test_log_expense_does_not_raise_when_over_old_cap(tmp_path):
    f = tmp_path / "expenses.jsonl"
    _prefill_today(f, 50.0)
    ct = CostTracker(expenses_file=f)
    # Must NOT raise DailyLimitExceeded — budget retired.
    asyncio.run(ct.log_expense("test_op", 5.0))
    assert "test_op" in f.read_text(encoding="utf-8")   # still appends (harmless trail)
