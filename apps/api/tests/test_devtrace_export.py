"""The developer view's run export: grouped by run, with a readable summary."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import devtrace


def _record_run(question: str, fail: bool = False) -> str:
    token = devtrace.start_run("chat", "c1", title="Learner asks", data={"question": question})
    run_id = devtrace.current_run().run_id
    devtrace.emit("route", "end", title="Classic RAG pipeline")
    if fail:
        devtrace.emit("llm", "error", title="Model call", data={"error": "Timeout"})
    devtrace.end_run(token, "error" if fail else "end", title="Answer sent", data={"answer": "A"})
    return run_id


def test_export_groups_runs_with_a_summary():
    devtrace.clear()
    ok = _record_run("what is a gradient")
    bad = _record_run("why did this fail", fail=True)
    out = devtrace.export_runs()
    by_id = {r["run_id"]: r for r in out["runs"]}
    assert set(by_id) == {ok, bad}
    assert by_id[ok]["summary"]["question"] == "what is a gradient"
    assert by_id[ok]["summary"]["status"] == "done"
    assert by_id[ok]["summary"]["path"] == ["Classic RAG pipeline"]
    assert by_id[bad]["summary"]["status"] == "error"
    assert by_id[bad]["summary"]["errors"][0]["step"] == "llm"


def test_export_one_run_over_http():
    from app.main import app

    devtrace.clear()
    rid = _record_run("one")
    _record_run("two")
    res = TestClient(app).get("/dev/trace/export", params={"run_id": rid})
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    body = res.json()
    assert body["run_count"] == 1 and body["runs"][0]["run_id"] == rid
