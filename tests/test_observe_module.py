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
