from murmur_sidecar import history as H


def test_append_and_load_newest_first(tmp_path):
    p = tmp_path / "history.jsonl"
    H.append_history(p, {"text": "one", "words": 1})
    H.append_history(p, {"text": "two", "words": 1})
    items = H.load_history(p, limit=10)
    assert [i["text"] for i in items] == ["two", "one"]  # newest first


def test_history_is_capped(tmp_path):
    p = tmp_path / "history.jsonl"
    for i in range(10):
        H.append_history(p, {"text": str(i), "words": 1}, cap=3)
    items = H.load_history(p, limit=100)
    assert [i["text"] for i in items] == ["9", "8", "7"]


def test_corrupt_lines_skipped(tmp_path):
    p = tmp_path / "history.jsonl"
    p.write_text('{"text":"ok","words":1}\nnot json\n{"text":"ok2","words":2}\n')
    items = H.load_history(p, limit=10)
    assert [i["text"] for i in items] == ["ok2", "ok"]


def test_update_stats_accumulates(tmp_path):
    sp = tmp_path / "stats.json"
    s1 = H.update_stats(sp, 5, now=100.0)
    s2 = H.update_stats(sp, 7, now=200.0)
    assert s2["dictations"] == 2
    assert s2["words"] == 12
    assert s2["first_ts"] == 100.0
    assert s2["last_ts"] == 200.0


def test_est_minutes_saved():
    assert H.est_minutes_saved(0) == 0.0
    # 150 words: type ~3.75 min, speak ~1.0 min -> ~2.75 saved
    assert round(H.est_minutes_saved(150), 2) == 2.75


def test_clear_removes_files(tmp_path):
    hp, sp = tmp_path / "h.jsonl", tmp_path / "s.json"
    H.append_history(hp, {"text": "x", "words": 1})
    H.update_stats(sp, 1)
    H.clear(hp, sp)
    assert H.load_history(hp) == []
    assert H.load_stats(sp)["dictations"] == 0
