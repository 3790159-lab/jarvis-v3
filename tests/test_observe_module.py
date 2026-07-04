import tools.jarvis_observe as o


def test_filter_drops_base64_request_options():
    lines = [
        "2026-07-04 01:02:11 | INFO     | root                | ok",
        "2026-07-04 01:02:11 | DEBUG    | anthropic._base_client | Request options: {'data':'" + "A" * 5000 + "'}",
        "2026-07-04 01:02:12 | INFO     | block_m             | done",
    ]
    out = o.filter_log_noise(lines)
    assert len(out) == 2 and all("Request options" not in x for x in out)


def test_tail_log_reads_last_n_filtered(tmp_path):
    p = tmp_path / "j.log"
    p.write_text("\n".join(f"2026 | INFO | root | line{i}" for i in range(100)), encoding="utf-8")
    txt = o.tail_log(str(p), n=10)
    # last 10 lines = line90..line99: newest present, line90 the boundary, line89/line80 gone
    assert "line99" in txt and "line90" in txt and "line89" not in txt


def _git_verb(args):
    # form is `git -C <repo> <verb> ...`; verb sits at index 3
    return args[3] if len(args) > 3 and args[0] == "git" and args[1] == "-C" else args[1]


def test_git_status_text_shape_and_readonly():
    calls = []

    def fake_run(args, **k):
        calls.append(args)
        import types
        joined = " ".join(args)
        if "rev-parse" in joined and "--short" in joined:
            return types.SimpleNamespace(stdout="da2312e\n", returncode=0)
        return types.SimpleNamespace(
            stdout="## phase-4.0-unified-jarvis...origin/phase-4.0-unified-jarvis [ahead 3]\n M brain_v2_state.json\n",
            returncode=0,
        )

    txt = o.git_status_text(repo="C:/jarvis", run=fake_run)
    assert "phase-4.0-unified-jarvis" in txt and "da2312e" in txt and "ahead 3" in txt
    verbs = [_git_verb(a) for a in calls]
    assert all(v in o._GIT_READONLY_VERBS for v in verbs)


def test_parse_pytest_summary():
    s = "130 failed, 3353 passed, 10 skipped, 101 warnings, 4 errors in 255.38s"
    assert o.parse_pytest_summary(s) == {"failed": 130, "passed": 3353, "errors": 4}


def test_regress_verdict_vs_baseline():
    assert "✅" in o.regress_verdict({"failed": 130, "passed": 3353, "errors": 4}, {"failed": 130})
    assert "⚠️" in o.regress_verdict({"failed": 134, "passed": 3349, "errors": 4}, {"failed": 130})
    assert "baseline не задан" in o.regress_verdict({"failed": 130, "passed": 1, "errors": 0}, None)
