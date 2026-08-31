import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
database_name = urlparse(TEST_DATABASE_URL).path.rsplit("/", 1)[-1] if TEST_DATABASE_URL else ""
SAFE_TEST_DATABASE = bool(TEST_DATABASE_URL and "test" in database_name.lower())


@unittest.skipUnless(
    SAFE_TEST_DATABASE,
    "Set TEST_DATABASE_URL to a dedicated database whose name contains 'test'",
)
class ObservabilityIntegrationTests(unittest.TestCase):
    """agent_run()/run.call() must actually land rows in agent_runs/
    tool_calls against a real database -- the unit tests in
    tests/test_observability.py only prove the no-op/fixture path and the
    write-failure-is-swallowed contract, neither of which exercises a real
    INSERT."""

    @classmethod
    def setUpClass(cls) -> None:
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from src.backend.repositories.postgres_repository import setup_application_schema
        from db.migrations.apply import apply_all

        setup_application_schema(TEST_DATABASE_URL)
        apply_all(TEST_DATABASE_URL)

    def setUp(self) -> None:
        import psycopg

        self._env_patch = {"APP_DATA_BACKEND": "postgres", "DATABASE_URL": TEST_DATABASE_URL}
        self._old_env = {k: os.environ.get(k) for k in self._env_patch}
        os.environ.update(self._env_patch)

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "TRUNCATE tool_calls, agent_runs, conversations, customers, import_batches "
                    "RESTART IDENTITY CASCADE"
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('obs-itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO customers (customer_id, vip_level, canceled_order_count, "
                    "import_batch_id) VALUES ('CUS-OBS-1', 'Gold', 0, 'obs-itest-batch')"
                )
            connection.commit()

    def tearDown(self) -> None:
        for key, old_value in self._old_env.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value

    def _fetch_one(self, query, params):
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()

    def test_successful_run_and_call_are_recorded(self):
        from src.backend.services import observability

        with observability.agent_run("CONV-OBS-1", "query_agent", customer_id="CUS-OBS-1") as run:
            result = run.call(
                "get_order_status", {"order_id": "ORD-OBS-1"},
                lambda: {"order_id": "ORD-OBS-1", "payment_status": "Paid"},
            )
        self.assertEqual(result["order_id"], "ORD-OBS-1")

        conversation = self._fetch_one(
            "SELECT customer_id FROM conversations WHERE conversation_id = %s", ("CONV-OBS-1",)
        )
        self.assertEqual(conversation["customer_id"], "CUS-OBS-1")

        run_row = self._fetch_one(
            "SELECT agent_name, run_status, conversation_id, workflow_version, model_version, "
            "duration_ms FROM agent_runs WHERE conversation_id = %s", ("CONV-OBS-1",)
        )
        self.assertEqual(run_row["agent_name"], "query_agent")
        self.assertEqual(run_row["run_status"], "Succeeded")
        self.assertIsNotNone(run_row["duration_ms"])
        self.assertIsNotNone(run_row["workflow_version"])

        call_row = self._fetch_one(
            "SELECT tc.tool_name, tc.call_status, tc.error_code, tc.attempt_number, "
            "tc.arguments_redacted, tc.result_redacted "
            "FROM tool_calls tc JOIN agent_runs ar ON tc.agent_run_id = ar.agent_run_id "
            "WHERE ar.conversation_id = %s", ("CONV-OBS-1",)
        )
        self.assertEqual(call_row["tool_name"], "get_order_status")
        self.assertEqual(call_row["call_status"], "Succeeded")
        self.assertIsNone(call_row["error_code"])
        self.assertEqual(call_row["attempt_number"], 1)
        # psycopg auto-decodes JSONB columns to dict -- no json.loads needed
        self.assertEqual(call_row["arguments_redacted"], {"order_id": "ORD-OBS-1"})
        # never the raw result -- only a hash + key list, see _summarize
        result_redacted = call_row["result_redacted"]
        self.assertIn("result_sha256", result_redacted)
        self.assertNotIn("Paid", json.dumps(result_redacted))

    def test_unknown_customer_id_falls_back_to_null_instead_of_losing_the_row(self):
        from src.backend.services import observability

        with observability.agent_run("CONV-OBS-3", "query_agent", customer_id="CUS-DOES-NOT-EXIST") as run:
            run.call("get_order_status", {}, lambda: {"ok": True})

        conversation = self._fetch_one(
            "SELECT customer_id FROM conversations WHERE conversation_id = %s", ("CONV-OBS-3",)
        )
        self.assertIsNotNone(conversation)  # the row exists...
        self.assertIsNone(conversation["customer_id"])  # ...just without the unresolvable customer_id

        run_row = self._fetch_one(
            "SELECT run_status FROM agent_runs WHERE conversation_id = %s", ("CONV-OBS-3",)
        )
        self.assertEqual(run_row["run_status"], "Succeeded")

    def test_tool_error_is_recorded_with_its_code_and_run_marked_failed(self):
        from src.backend.services import observability
        from src.backend.services.tool_errors import DATA_INTEGRITY_ERROR, ToolError

        with self.assertRaises(ToolError):
            with observability.agent_run("CONV-OBS-2", "query_agent") as run:
                def _boom():
                    raise ToolError(DATA_INTEGRITY_ERROR, "order data failed a consistency check")
                run.call("get_order_status", {"order_id": "ORD-OBS-2"}, _boom)

        run_row = self._fetch_one(
            "SELECT run_status, error_summary FROM agent_runs WHERE conversation_id = %s",
            ("CONV-OBS-2",),
        )
        self.assertEqual(run_row["run_status"], "Failed")
        self.assertIn("ToolError", run_row["error_summary"])

        call_row = self._fetch_one(
            "SELECT call_status, error_code FROM tool_calls tc "
            "JOIN agent_runs ar ON tc.agent_run_id = ar.agent_run_id "
            "WHERE ar.conversation_id = %s", ("CONV-OBS-2",),
        )
        self.assertEqual(call_row["call_status"], "Failed")
        self.assertEqual(call_row["error_code"], DATA_INTEGRITY_ERROR)


if __name__ == "__main__":
    unittest.main()
