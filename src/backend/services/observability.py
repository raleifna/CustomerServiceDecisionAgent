"""agent_runs / tool_calls logging for the Agent Tool layer.

Scope today: query_agent_node is the one live Postgres-backed Tool path
that exists, so `agent_run(...)` is opened per node invocation, not per
whole-LangGraph-turn -- when order_agent.py/logistics_agent.py etc. migrate
onto this same pattern, each will open its own agent_run the same way.
Unifying multiple nodes under one shared run_id per LangGraph turn is a
later step (needs a run_id threaded through CustomerServiceState), not
assumed here.

Hard rules, both load-bearing for the rest of the system:

  1. This module must never cause a customer-facing failure. Every DB write
     is wrapped in its own try/except that logs and swallows -- a broken
     observability table is a monitoring problem, not an outage.
  2. Only active when APP_DATA_BACKEND=postgres. The Fixture-backed path
     (27-order fixture, all 34 deterministic eval cases) never touches a
     database at all, so this is a silent no-op there -- unit tests and the
     eval harness must not need a live Postgres just to run query_agent.

Redaction: tool_calls.arguments_redacted/result_redacted store business
identifiers already treated as non-sensitive everywhere else in this
schema (order_id, customer_id -- both plain TEXT columns elsewhere, not
hashed), plus a hash + top-level key list of the full result rather than
the result itself. Nothing that looks like a name, address, phone number,
or payment instrument ever reaches this module because query_agent's Tool
results don't carry any -- see query_tool_provider.py's own docstring for
what those results contain.
"""

import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from src.backend.services.tool_errors import INTERNAL_TOOL_ERROR, ToolError

logger = logging.getLogger(__name__)

# Bumped alongside meaningful changes to the graph's node shape/routing --
# independent of PIPELINE_VERSION (the ETL's own versioning), which tracks
# the imported data batch, not the code that reads it.
WORKFLOW_VERSION = "v1.0"


def _enabled() -> bool:
    return os.getenv("APP_DATA_BACKEND", "fixture").strip().lower() == "postgres"


def _model_version() -> str:
    return os.getenv("LLM_MODEL_VERSION", "unspecified").strip() or "unspecified"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def _summarize(value: Any) -> dict[str, Any]:
    """A hash + top-level key list, never the value itself -- see module
    docstring's redaction rule."""
    try:
        encoded = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"unserializable": True}
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    keys = sorted(value.keys()) if isinstance(value, dict) else None
    return {"result_sha256": digest, "top_level_keys": keys}


@contextmanager
def _connection():
    import psycopg
    from src.backend.repositories.postgres_repository import get_database_url

    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    with psycopg.connect(database_url) as conn:
        yield conn


def _safe_write(label: str, fn) -> None:
    """Run fn() (a DB write) and swallow + log any failure -- see module
    docstring rule 1. Never called for anything that isn't purely an
    observability write."""
    try:
        fn()
    except Exception:
        logger.exception("observability write failed (%s) -- continuing without it", label)


def _upsert_conversation(conn, conversation_id: str, customer_id: str | None) -> None:
    """customer_id is optional, best-effort metadata -- conversations.
    customer_id has no NOT NULL constraint, only an FK to customers. If the
    caller's customer_id doesn't resolve (an unauthenticated turn, a stale
    value, test data), the conversation row itself must still be written --
    silently losing observability for the whole turn over one optional
    field would be worse than recording it with customer_id left NULL."""
    import psycopg

    with conn.cursor() as cur:
        try:
            with conn.transaction():
                cur.execute(
                    """
                    INSERT INTO conversations (conversation_id, customer_id, last_message_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (conversation_id) DO UPDATE
                        SET last_message_at = CURRENT_TIMESTAMP,
                            customer_id = COALESCE(EXCLUDED.customer_id, conversations.customer_id)
                    """,
                    (conversation_id, customer_id),
                )
        except psycopg.errors.ForeignKeyViolation:
            cur.execute(
                """
                INSERT INTO conversations (conversation_id, last_message_at)
                VALUES (%s, CURRENT_TIMESTAMP)
                ON CONFLICT (conversation_id) DO UPDATE SET last_message_at = CURRENT_TIMESTAMP
                """,
                (conversation_id,),
            )
    conn.commit()


def _insert_agent_run_started(
    conn, run_id: str, conversation_id: str, agent_name: str, started_at: datetime,
) -> None:
    """Written eagerly, before any tool_calls row can exist -- tool_calls
    has an FK to agent_runs, so the row must exist (run_status='Running')
    the moment the first Tool call inside this run tries to log itself, not
    only once the whole `with agent_run(...)` block finishes."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_runs
                (agent_run_id, conversation_id, agent_name, graph_node_path,
                 started_at, run_status, model_version, workflow_version)
            VALUES (%s, %s, %s, %s, %s, 'Running', %s, %s)
            """,
            (
                run_id, conversation_id, agent_name, json.dumps([agent_name]),
                started_at, _model_version(), WORKFLOW_VERSION,
            ),
        )
    conn.commit()


def _finish_agent_run(
    conn, run_id: str, status: str, duration_ms: int, error_summary: str | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE agent_runs
                SET finished_at = CURRENT_TIMESTAMP, run_status = %s,
                    duration_ms = %s, error_summary = %s
                WHERE agent_run_id = %s
            """,
            (status, duration_ms, error_summary, run_id),
        )
    conn.commit()


def _insert_tool_call(
    conn, call_id: str, run_id: str, tool_name: str, attempt_number: int,
    arguments: dict[str, Any], result_summary: dict[str, Any] | None,
    status: str, error_summary: str | None, error_code: str | None, duration_ms: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tool_calls
                (tool_call_id, agent_run_id, tool_name, arguments_redacted,
                 result_redacted, finished_at, call_status, error_summary,
                 error_code, duration_ms, attempt_number)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, %s, %s)
            """,
            (
                call_id, run_id, tool_name, json.dumps(arguments, default=str),
                json.dumps(result_summary, default=str) if result_summary else None,
                status, error_summary, error_code, duration_ms, attempt_number,
            ),
        )
    conn.commit()


class _NoopRun:
    """Used when observability is disabled (fixture backend) -- lets call
    sites write `with observability.agent_run(...) as run: run.call(...)`
    unconditionally, without an `if postgres:` branch at every call site."""

    def call(self, tool_name: str, arguments: dict[str, Any], func, *args, **kwargs):
        return func(*args, **kwargs)


class _AgentRunRecorder:
    def __init__(self, run_id: str, conversation_id: str):
        self.run_id = run_id
        self.conversation_id = conversation_id
        self._attempts: dict[str, int] = {}

    def call(self, tool_name: str, arguments: dict[str, Any], func, *args, **kwargs):
        """Execute func(*args, **kwargs), recording one tool_calls row
        whether it succeeds or fails. Re-raises whatever func raised --
        this never changes the caller's control flow, only observes it."""
        self._attempts[tool_name] = self._attempts.get(tool_name, 0) + 1
        attempt_number = self._attempts[tool_name]
        call_id = _new_id("CALL")
        started = time.monotonic()
        status, error_code, error_summary, result_summary = "Succeeded", None, None, None
        try:
            result = func(*args, **kwargs)
            result_summary = _summarize(result)
            return result
        except ToolError as exc:
            status, error_code, error_summary = "Failed", exc.code, exc.message[:500]
            raise
        except Exception as exc:
            status, error_code = "Failed", INTERNAL_TOOL_ERROR
            error_summary = f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            _safe_write(
                f"tool_call:{tool_name}",
                lambda: _insert_tool_call_standalone(
                    call_id, self.run_id, tool_name, attempt_number,
                    arguments, result_summary, status, error_summary, error_code, duration_ms,
                ),
            )


def _insert_tool_call_standalone(*args) -> None:
    with _connection() as conn:
        _insert_tool_call(conn, *args)


@contextmanager
def agent_run(conversation_id: str, agent_name: str, *, customer_id: str | None = None):
    """One agent_runs row per node invocation. Yields a recorder exposing
    `.call(tool_name, arguments, func, *args, **kwargs)` for each Tool/data
    read the node makes; a plain `.call(...)` on the no-op recorder (fixture
    backend) still executes func normally, just without logging anything.

    Failures from `func` propagate unchanged -- this context manager only
    observes, it never swallows or converts an exception from the wrapped
    call. See module docstring rule 1 for why the *logging* itself never
    raises.
    """
    if not _enabled():
        yield _NoopRun()
        return

    run_id = _new_id("RUN")
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    _safe_write("upsert_conversation", lambda: _upsert_conversation_standalone(conversation_id, customer_id))
    _safe_write(
        "agent_run_started",
        lambda: _insert_agent_run_started_standalone(run_id, conversation_id, agent_name, started_at),
    )

    recorder = _AgentRunRecorder(run_id, conversation_id)
    status, error_summary = "Succeeded", None
    try:
        yield recorder
    except BaseException as exc:
        status = "Failed"
        error_summary = f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        _safe_write(
            "agent_run_finished",
            lambda: _finish_agent_run_standalone(run_id, status, duration_ms, error_summary),
        )


def _upsert_conversation_standalone(conversation_id: str, customer_id: str | None) -> None:
    with _connection() as conn:
        _upsert_conversation(conn, conversation_id, customer_id)


def _insert_agent_run_started_standalone(*args) -> None:
    with _connection() as conn:
        _insert_agent_run_started(conn, *args)


def _finish_agent_run_standalone(*args) -> None:
    with _connection() as conn:
        _finish_agent_run(conn, *args)
