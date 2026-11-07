import json

from frameproof.report import _result_times, render_index, render_run


def _run(tmp_path, name, decision, detail="'X' not visible at 1.2s"):
    d = tmp_path / "b" / name
    (d / "frames").mkdir(parents=True)
    (d / "frames" / "t001.20.png").write_bytes(b"")
    results = [
        {"id": "b1", "kind": "text_visible", "passed": decision == "ACCEPT", "severity": "hard",
         "detail": detail, "hidden": False, "times": [1.2], "source": "brief says X", "params": {}},
        {"id": "g", "kind": "in_frame", "passed": False, "severity": "hard",
         "detail": "text cut by frame edge at [(2.5, 'Y'), (3.0, 'Y')]", "hidden": False, "times": [], "source": "", "params": {}},
    ]
    (d / "verdict.json").write_text(json.dumps({
        "decision": decision, "score": 0.5, "reasons": ["r </script><b>"], "results": results, "judge": None,
        "meta": {"brief": "b", "duration": 6.0, "variant": name}}))
    return d


def test_generic_failures_get_timeline_marks():
    assert _result_times({"passed": False, "times": [], "detail": "at [(2.5, 'Y'), (3.0, 'Y')]"}) == [2.5, 3.0]
    assert _result_times({"passed": True, "times": [], "detail": "fine"}) == []


def test_render_run_and_index(tmp_path):
    orig = _run(tmp_path, "original", "ACCEPT")
    mut = _run(tmp_path, "typo", "BLOCK")
    page = render_run(mut, compare_to=orig, back_link="../../index.html").read_text()
    assert "</script><b>" not in page.split('<script id="data"')[1].split("</script>")[0]  # data is escaped
    data = json.loads(page.split('type="application/json">')[1].split("</script>")[0].replace("<\\/", "</"))
    assert data["compare"]["decision"] == "ACCEPT" and data["frames"][0]["t"] == 1.2
    rows = [{"brief": "b", "variant": "original", "category": "none", "expected": "ACCEPT", "decision": "ACCEPT",
             "score": 1, "reasons": []},
            {"brief": "b", "variant": "typo", "category": "content", "expected": "BLOCK", "decision": "BLOCK",
             "score": 0.5, "reasons": ["x"]}]
    idx = render_index(tmp_path, rows, "summary").read_text()
    assert '"caught": 1' in idx and '"false_blocks": 0' in idx
