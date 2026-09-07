# -*- coding: utf-8 -*-
"""Проводка цепочки в жизненный цикл dev-задачи. $0, моки, без CC и без сети.

Спека 2026-09-07-devtask-chain.md §4 (автопродвижение) и §2 (база шага).
Сторожа написаны ДО проводки.
"""
import importlib

import pytest

from app.services.devtask import chain as ch
from app.services.devtask.queue import DevTaskQueue

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Очередь, план цепочки и «телеграм» — всё в tmp, наружу ничего не уходит."""
    q = DevTaskQueue(base_dir=tmp_path)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(mod, "_devtask_state_dir", lambda: tmp_path, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda *a, **k: sent.append(a[1] if len(a) > 1 else ""))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: sent.append(a[1] if len(a) > 1 else ""))
    started = []
    monkeypatch.setattr(mod, "_devtask_confirm", lambda cid, tid: started.append(tid), raising=False)
    return {"q": q, "store": ch.ChainStore(base_dir=tmp_path), "sent": sent, "started": started}


# ── §2 база шага ───────────────────────────────────────────────────────────

def test_base_of_a_plain_task_is_prod_head(wired, monkeypatch):
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "prod_head", lambda **k: "prodhead1")
    tid = wired["q"].add("обычная")
    assert mod._devtask_base_for(tid) == "prodhead1"


def test_base_of_the_second_chain_step_is_the_previous_branch(wired, monkeypatch):
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "prod_head", lambda **k: "prodhead1")
    chain = wired["store"].create(title="две", descs=["раз", "два"])
    t1 = wired["q"].add("раз", chain_id=chain["id"], step_no=1)
    wired["store"].attach_task(chain["id"], 1, t1)
    t2 = wired["q"].add("два", chain_id=chain["id"], step_no=2, after=t1)
    wired["store"].attach_task(chain["id"], 2, t2)
    assert mod._devtask_base_for(t2) == ch.branch_for_task(t1)


def test_base_refuses_when_the_chain_plan_is_broken(wired, monkeypatch):
    """Пропавший план — отказ, а не молчаливый откат к prod_head."""
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "prod_head", lambda **k: "prodhead1")
    tid = wired["q"].add("шаг", chain_id="chain_missing", step_no=2)
    with pytest.raises(ch.ChainError):
        mod._devtask_base_for(tid)


# ── §4 автопродвижение ─────────────────────────────────────────────────────

def _green(**over):
    v = dict(gate_ok=True, merge_conflict=False, step_elapsed_s=1,
             chain_elapsed_s=1, has_deletions=False, rate_limited=False,
             preflight_ok=True, worktree_ok=True, process_alive=True)
    v.update(over)
    return v


def test_plain_task_is_not_touched_by_the_chain_advance(wired):
    tid = wired["q"].add("обычная")
    mod._devtask_chain_advance(tid, _green())
    assert wired["started"] == []


def test_green_step_starts_the_next_one_from_its_branch(wired):
    chain = wired["store"].create(title="две", descs=["раз", "два"])
    t1 = wired["q"].add("раз", chain_id=chain["id"], step_no=1)
    wired["store"].attach_task(chain["id"], 1, t1)
    mod._devtask_chain_advance(t1, _green())
    assert len(wired["started"]) == 1
    started = wired["q"].get(wired["started"][0])
    assert started["chain_id"] == chain["id"]
    assert started["step_no"] == 2
    assert started["after"] == t1


def test_red_gate_stops_the_chain_and_starts_nothing(wired):
    chain = wired["store"].create(title="две", descs=["раз", "два"])
    t1 = wired["q"].add("раз", chain_id=chain["id"], step_no=1)
    wired["store"].attach_task(chain["id"], 1, t1)
    mod._devtask_chain_advance(t1, _green(gate_ok=False))
    assert wired["started"] == []
    saved = wired["store"].get(chain["id"])
    assert saved["status"] == ch.CHAIN_STOPPED
    assert saved["stopped_reason"] == ch.STOP_GATE_RED


def test_deletion_stops_the_chain_even_on_a_green_gate(wired):
    chain = wired["store"].create(title="две", descs=["раз", "два"])
    t1 = wired["q"].add("раз", chain_id=chain["id"], step_no=1)
    wired["store"].attach_task(chain["id"], 1, t1)
    mod._devtask_chain_advance(t1, _green(has_deletions=True))
    assert wired["started"] == []
    assert wired["store"].get(chain["id"])["stopped_reason"] == ch.STOP_DELETION


def test_exhausted_plan_closes_the_chain_without_starting_anything(wired):
    chain = wired["store"].create(title="одна", descs=["раз"])
    t1 = wired["q"].add("раз", chain_id=chain["id"], step_no=1)
    wired["store"].attach_task(chain["id"], 1, t1)
    mod._devtask_chain_advance(t1, _green())
    assert wired["started"] == []
    assert wired["store"].get(chain["id"])["status"] == ch.CHAIN_DONE


def test_advance_never_merges(wired, monkeypatch):
    """Поведенческий сторож: автопродвижение не смеет трогать транк."""
    from app.services.devtask import git_ops as g

    def boom(*a, **k):
        raise AssertionError("цепочка попыталась смержить — это запрещено §4")

    monkeypatch.setattr(g, "ff_merge", boom)
    monkeypatch.setattr(g, "merge_no_ff", boom)
    chain = wired["store"].create(title="две", descs=["раз", "два"])
    t1 = wired["q"].add("раз", chain_id=chain["id"], step_no=1)
    wired["store"].attach_task(chain["id"], 1, t1)
    mod._devtask_chain_advance(t1, _green())
    assert len(wired["started"]) == 1


def test_stopped_chain_is_reported_to_the_owner(wired):
    chain = wired["store"].create(title="две", descs=["раз", "два"])
    t1 = wired["q"].add("раз", chain_id=chain["id"], step_no=1)
    wired["store"].attach_task(chain["id"], 1, t1)
    mod._devtask_chain_advance(t1, _green(rate_limited=True))
    blob = " ".join(wired["sent"])
    assert ch.STOP_RATE_LIMIT in blob or "лимит" in blob.lower()


# ── §5.4 удаление в дифе шага ──────────────────────────────────────────────

def test_deleted_paths_reports_removals(monkeypatch):
    """Признак удаления берётся из git, а не из текста отчёта CC."""
    from app.services.devtask import git_ops as g
    seen = {}

    class R:
        returncode = 0
        stdout = "app/agents/qa_agent.py\napp/agents/base_agent.py\n"
        stderr = ""

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return R()

    assert g.deleted_paths("base1", "devtask-t1", run=fake_run) == [
        "app/agents/qa_agent.py", "app/agents/base_agent.py"]
    assert "--diff-filter=D" in seen["argv"]


def test_deleted_paths_empty_when_nothing_removed(monkeypatch):
    from app.services.devtask import git_ops as g

    class R:
        returncode = 0
        stdout = "\n"
        stderr = ""

    assert g.deleted_paths("base1", "devtask-t1", run=lambda *a, **k: R()) == []


def test_verdict_marks_deletions(wired, monkeypatch):
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "deleted_paths", lambda *a, **k: ["app/x.py"])
    monkeypatch.setattr(mod, "_devtask_run_targeted_combined",
                        lambda *a, **k: {"ok": True, "text": ""}, raising=False)
    tid = wired["q"].add("шаг", chain_id="c1", step_no=1)
    wired["q"].set_status(tid, "awaiting_review", worktree="wt", base_head="b1")
    v = mod._devtask_chain_verdict(wired["q"].get(tid), {})
    assert v["has_deletions"] is True


def test_verdict_marks_a_red_gate(wired, monkeypatch):
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "deleted_paths", lambda *a, **k: [])
    monkeypatch.setattr(mod, "_devtask_run_targeted_combined",
                        lambda *a, **k: {"ok": False, "text": "упали таргет-тесты"},
                        raising=False)
    tid = wired["q"].add("шаг", chain_id="c1", step_no=1)
    wired["q"].set_status(tid, "awaiting_review", worktree="wt", base_head="b1")
    v = mod._devtask_chain_verdict(wired["q"].get(tid), {})
    assert v["gate_ok"] is False


def test_verdict_marks_a_merge_conflict(wired, monkeypatch):
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "deleted_paths", lambda *a, **k: [])
    monkeypatch.setattr(mod, "_devtask_run_targeted_combined",
                        lambda *a, **k: {"ok": False, "mode": "merge_commit",
                                         "text": "конфликт слияния"},
                        raising=False)
    tid = wired["q"].add("шаг", chain_id="c1", step_no=1)
    wired["q"].set_status(tid, "awaiting_review", worktree="wt", base_head="b1")
    v = mod._devtask_chain_verdict(wired["q"].get(tid), {})
    assert v["merge_conflict"] is True


def test_verdict_marks_rate_limit(wired, monkeypatch):
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "deleted_paths", lambda *a, **k: [])
    monkeypatch.setattr(mod, "_devtask_run_targeted_combined",
                        lambda *a, **k: {"ok": True, "text": ""}, raising=False)
    tid = wired["q"].add("шаг", chain_id="c1", step_no=1)
    wired["q"].set_status(tid, "awaiting_review", worktree="wt", base_head="b1")
    v = mod._devtask_chain_verdict(wired["q"].get(tid), {"rate_limited": True})
    assert v["rate_limited"] is True


def test_poller_actually_calls_the_chain_advance():
    """Без этого крючка вся цепочка инертна, а суита остаётся зелёной.

    Сторож литеральный и намеренно грубый: он краснеет от ИСЧЕЗНОВЕНИЯ вызова
    в поллере — ровно от той правки, после которой автопродвижение перестало бы
    существовать, ничего при этом не сломав.
    """
    import inspect
    src = inspect.getsource(mod._devtask_poll_active)
    assert "_devtask_chain_step_done" in src
    assert "chain_id" in src


def test_chain_step_done_is_loud_when_it_fails(wired, monkeypatch):
    """Сорвавшееся продвижение обязано СКАЗАТЬ, а не молчать."""
    def boom(*a, **k):
        raise RuntimeError("гейт умер")

    monkeypatch.setattr(mod, "_devtask_chain_verdict", boom, raising=False)
    tid = wired["q"].add("шаг", chain_id="c1", step_no=1)
    mod._devtask_chain_step_done(tid, {})
    blob = " ".join(wired["sent"])
    assert "гейт умер" in blob
    assert "НЕ запущен" in blob
