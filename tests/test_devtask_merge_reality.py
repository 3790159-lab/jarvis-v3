# -*- coding: utf-8 -*-
"""Merge must see git REALITY when a card is merged (Этап 1).

Before attempting a merge the pipeline checks ``git merge-base --is-ancestor
<branch> <prod>``:
  (a) branch already fully in prod  -> close the card as merged WITHOUT running
      regress and WITHOUT the "prod moved" scolding;
  (b) prod moved but branch NOT merged -> instead of the "manual rebase" dead-end,
      offer a [Смерджить merge-коммитом] button (a real non-FF merge) that runs
      the targeted tests on the COMBINED code first.

$0, mocks only, no CC, no network, no real pytest/git.
"""
import importlib

from app.services.devtask.queue import (
    DevTaskQueue, STATUS_AWAITING_REVIEW, STATUS_MERGED,
)

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def _seed_awaiting(monkeypatch, tmp_path, worktree="C:/wt/devtask-X", base="base1"):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, STATUS_AWAITING_REVIEW, worktree=worktree, base_head=base,
                 branch=f"devtask-{tid}", report_path=str(tmp_path / tid / "report.md"),
                 cost=0.42)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    return q, tid


# ── (a) branch already in prod: close as merged, no regress, no scolding ─────
def test_merge_closes_card_when_branch_already_merged(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    ran = {"full": False, "targeted": False}
    monkeypatch.setattr(mod, "_devtask_run_regress",
                        lambda *a, **k: ran.update(full=True) or {"ok": True, "text": ""})
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda *a, **k: ran.update(targeted=True) or {"ok": True, "text": ""})
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "prodnow")
    restarted = {"x": False}
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: restarted.update(x=True))

    mod._devtask_merge(ADMIN, tid, mode="full")

    assert ran == {"full": False, "targeted": False}          # no regress at all
    assert q.get(tid)["status"] == STATUS_MERGED
    joined = " ".join(msgs).lower()
    assert "уже влита" in joined and "закрыт" in joined         # honest closing note
    assert "сдвин" not in joined                                # NOT "prod moved"


def test_already_merged_records_heads_and_mode(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path, base="base1")
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "prodnow")
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)

    mod._devtask_merge(ADMIN, tid, mode="targeted")

    item = q.get(tid)
    assert item["old_head"] == "base1" and item["new_head"] == "prodnow"
    assert item["regress_mode"] == "already_merged"
    assert item["cost"] == 0.42                                 # cost preserved


def test_already_merged_shortcut_applies_even_to_skip_regress(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "prodnow")
    did = {"do_merge": False}
    monkeypatch.setattr(mod, "_devtask_do_merge", lambda *a, **k: did.update(do_merge=True))
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)

    mod._devtask_merge(ADMIN, tid, skip_regress=True)           # [⚠️ force] path

    assert did["do_merge"] is False                            # no FF merge attempted
    assert q.get(tid)["status"] == STATUS_MERGED


# ── (b) prod moved, branch NOT merged: offer merge-commit button ────────────
def test_prod_moved_not_merged_offers_mergecommit_button(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    kb_sent = {}
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, t, kb: kb_sent.update(text=t, kb=kb))
    plain = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: plain.append(t))
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_ff_clean", lambda *a, **k: False)
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)
    restarted = {"x": False}
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: restarted.update(x=True))

    mod._devtask_do_merge(ADMIN, tid, q.get(tid), mode="full")

    datas = [b["callback_data"] for row in kb_sent["kb"] for b in row]
    assert "devtask:mergecommit:%s" % tid in datas
    assert restarted["x"] is False
    assert q.get(tid)["status"] == STATUS_AWAITING_REVIEW       # unchanged, not merged


def test_mergecommit_callback_runs_merge_commit(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    got = {}
    monkeypatch.setattr(mod, "_devtask_merge_commit",
                        lambda cid, t, **k: got.update(tid=t))

    class _Immediate:
        def __init__(self, *a, **k):
            self._t = k.get("target"); self._args = k.get("args", ()); self._kw = k.get("kwargs", {})
        def start(self):
            self._t(*self._args, **self._kw)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    mod.handle_callback_query({
        "id": "cq1", "from": {"id": int(ADMIN)},
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 1},
        "data": "devtask:mergecommit:%s" % tid,
    }, {})
    assert got["tid"] == tid


# ── merge-commit gate runs targeted tests on COMBINED code, then non-FF merges ─
def test_merge_commit_tests_combined_then_non_ff_merges(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(mod, "_devtask_run_targeted_combined",
                        lambda wt, base: seen.update(wt=wt, base=base) or
                        {"ok": True, "text": "🎯 3 passed", "mode": "merge_commit"})
    from app.services.devtask import git_ops as g, boot_watch as bw
    order = []
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "prodnow")
    monkeypatch.setattr(g, "merge_no_ff", lambda *a, **k: order.append("merge"))
    monkeypatch.setattr(g, "ff_merge", lambda *a, **k: order.append("FF-MERGE"))
    monkeypatch.setattr(bw, "write_pending_restart", lambda *a, **k: order.append("pending"))
    monkeypatch.setattr(bw, "write_boot_watch", lambda *a, **k: order.append("watch"))
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: order.append("restart"))

    mod._devtask_merge_commit(ADMIN, tid)

    assert seen["wt"] == "C:/wt/devtask-X" and seen["base"] == "base1"
    assert order == ["merge", "pending", "watch", "restart"]   # non-FF merge, restart LAST
    assert "FF-MERGE" not in order                             # never a fast-forward here
    item = q.get(tid)
    assert item["status"] == STATUS_MERGED and item["regress_mode"] == "merge_commit"


def test_merge_commit_blocked_when_combined_tests_fail(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "_devtask_run_targeted_combined",
                        lambda wt, base: {"ok": False, "text": "🚫 1 failed", "mode": "merge_commit"})
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)
    merged = {"x": False}
    monkeypatch.setattr(g, "merge_no_ff", lambda *a, **k: merged.update(x=True))
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)

    mod._devtask_merge_commit(ADMIN, tid)

    assert merged["x"] is False
    assert q.get(tid)["status"] == STATUS_AWAITING_REVIEW
    assert any("заблокирован" in s for s in sent)


def test_merge_commit_closes_if_became_merged_meanwhile(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "prodnow")
    gate = {"ran": False}
    monkeypatch.setattr(mod, "_devtask_run_targeted_combined",
                        lambda *a, **k: gate.update(ran=True) or {"ok": True, "text": ""})
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)

    mod._devtask_merge_commit(ADMIN, tid)

    assert gate["ran"] is False                                # short-circuited
    assert q.get(tid)["status"] == STATUS_MERGED
    assert q.get(tid)["regress_mode"] == "already_merged"


# ── _devtask_run_targeted_combined: merges prod into worktree, then targeted ─
def test_run_targeted_combined_merges_prod_then_runs_targeted(monkeypatch):
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "prodnow")
    calls = {}
    monkeypatch.setattr(g, "merge_prod_into_worktree",
                        lambda wt, ph, **k: calls.update(wt=wt, ph=ph) or True)
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda wt, base: {"ok": True, "text": "🎯 ok", "mode": "targeted"})
    res = mod._devtask_run_targeted_combined("C:/wt/devtask-X", "base1")
    assert calls == {"wt": "C:/wt/devtask-X", "ph": "prodnow"}
    assert res["ok"] is True


def test_run_targeted_combined_conflict_blocks(monkeypatch):
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "prodnow")
    monkeypatch.setattr(g, "merge_prod_into_worktree", lambda *a, **k: False)  # conflict
    ran = {"targeted": False}
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda *a, **k: ran.update(targeted=True) or {"ok": True})
    res = mod._devtask_run_targeted_combined("C:/wt/devtask-X", "base1")
    assert res["ok"] is False and ran["targeted"] is False     # conflict = honest block
    assert "конфликт" in res["text"].lower()
