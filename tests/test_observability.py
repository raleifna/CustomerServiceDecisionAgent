import pytest

from src.backend.services import observability
from src.backend.services.tool_errors import DATA_INTEGRITY_ERROR, ToolError


@pytest.fixture(autouse=True)
def _fixture_backend(monkeypatch):
    # Every test here runs with the fixture backend unless a test opts into
    # postgres explicitly -- this is what unit tests and the eval harness
    # run under, and observability must be a true no-op there (no DB touched).
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)


def test_disabled_backend_never_touches_the_database(monkeypatch):
    # If observability tried to connect under the fixture backend, this
    # would raise (no DATABASE_URL / no real DB in this test process).
    with observability.agent_run("CONV-1", "query_agent") as run:
        result = run.call("get_order_status", {"order_id": "ORD1"}, lambda: {"order_id": "ORD1"})
    assert result == {"order_id": "ORD1"}


def test_disabled_backend_still_propagates_tool_error():
    with pytest.raises(ToolError):
        with observability.agent_run("CONV-1", "query_agent") as run:
            def _boom():
                raise ToolError(DATA_INTEGRITY_ERROR, "broken")
            run.call("get_order_status", {}, _boom)


def test_summarize_never_includes_raw_values():
    summary = observability._summarize({"order_id": "ORD1", "payment_status": "Paid"})
    assert "ORD1" not in str(summary)
    assert "Paid" not in str(summary)
    assert summary["top_level_keys"] == ["order_id", "payment_status"]
    assert "result_sha256" in summary


def test_summarize_handles_unserializable_values_without_raising():
    class _NotJSONable:
        def __repr__(self):
            return "<weird>"

    # default=str in json.dumps means this actually serializes fine (via
    # str()) rather than hitting the except branch -- confirms no exception
    # escapes either way, which is the property under test.
    summary = observability._summarize({"weird": _NotJSONable()})
    assert "result_sha256" in summary or summary == {"unserializable": True}


def test_enabled_write_failure_never_propagates_past_agent_run(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "postgres")
    monkeypatch.setattr(
        observability, "_upsert_conversation_standalone",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db exploded")),
    )
    monkeypatch.setattr(
        observability, "_insert_agent_run_started_standalone",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db exploded")),
    )
    monkeypatch.setattr(
        observability, "_finish_agent_run_standalone",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db exploded")),
    )
    monkeypatch.setattr(
        observability, "_insert_tool_call_standalone",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db exploded")),
    )

    with observability.agent_run("CONV-1", "query_agent") as run:
        result = run.call("get_order_status", {"order_id": "ORD1"}, lambda: {"ok": True})
    assert result == {"ok": True}


def test_attempt_number_increments_per_retry_of_the_same_tool(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "postgres")
    monkeypatch.setattr(observability, "_upsert_conversation_standalone", lambda *a, **k: None)
    monkeypatch.setattr(observability, "_insert_agent_run_started_standalone", lambda *a, **k: None)
    monkeypatch.setattr(observability, "_finish_agent_run_standalone", lambda *a, **k: None)

    recorded_attempts = []

    def _capture_tool_call(call_id, run_id, tool_name, attempt_number, *rest):
        recorded_attempts.append(attempt_number)

    monkeypatch.setattr(observability, "_insert_tool_call_standalone", _capture_tool_call)

    with observability.agent_run("CONV-1", "query_agent") as run:
        run.call("get_order_status", {}, lambda: {"ok": True})
        run.call("get_order_status", {}, lambda: {"ok": True})

    assert recorded_attempts == [1, 2]
