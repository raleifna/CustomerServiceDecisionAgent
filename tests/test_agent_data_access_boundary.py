"""Static check: no module under src/backend/agents/ may reach a database,
a Repository, mock_db_repository, or a raw JSON fixture file directly, and
none may call date.today()/datetime.now() directly -- all data access goes
through a Service in src/backend/services/ (QueryToolProvider,
OrderOwnershipService, OrderContextService, ...), and all "today" reads go
through BusinessClock. Source-level (ast/text) check, not a runtime mock,
so it catches a violation even if no test happens to exercise that path.

This test scans the whole agents/ directory, not one named file -- as of
the conversation_router_agent.py/OrderOwnershipService migration, that
module (the last one importing mock_db_repository directly) is clean, so
the check generalizes instead of staying pinned to query_agent.py. See
KNOWN_VIOLATIONS below for the one documented gap still open.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "src" / "backend" / "agents"

FORBIDDEN_MODULE_PREFIXES = (
    "src.backend.repositories",
    "psycopg",
)

# order_agent.py directly imports service_request_repository for
# get_already_approved_quantity()/get_completed_refund_count() -- both
# already pure real-time Postgres reads with no JSON fallback, so the DATA
# is real, but the IMPORT still crosses this boundary (should go through a
# Service, not a Repository, from agents/). Flagged in the workflow.py/
# order_agent.py migration scan; scheduled for the "load_order/customer/
# product + order_agent.py" step of that migration, not fixed here.
# Listed explicitly so this test documents the known gap precisely instead
# of silently exempting the whole file or the whole check.
KNOWN_VIOLATIONS: dict[str, set[str]] = {
    "order_agent.py": {"src.backend.repositories.service_request_repository"},
    # get_service_request()/get_service_requests_by_order_id() -- the
    # after-sales-request layer's own table (service_requests), already
    # Postgres-backed with a JSON-seed fallback baked in below the Service
    # boundary. Cleanup of that internal fallback is its own scheduled step
    # ("service_request/evidence/order_item 旧混合Repository的静默回退清理"),
    # not part of this migration's scope.
    "request_resolver_agent.py": {"src.backend.repositories.service_request_repository"},
    # Reads static Markdown policy documents (refund/shipping/vip/risk
    # policy text) from data/knowledge_base/ -- reference content, not
    # order/customer/logistics business data, so it isn't part of this
    # migration's scope either. Named explicitly rather than widening
    # FORBIDDEN_MODULE_PREFIXES to carve out an exception for it.
    "policy_agent.py": {"src.backend.repositories.policy_repository"},
}


def _agent_files() -> list[Path]:
    return sorted(p for p in AGENTS_DIR.glob("*.py") if p.name != "__init__.py")


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_agents_directory_does_not_import_data_access_modules_directly():
    offenders: dict[str, list[str]] = {}
    for path in _agent_files():
        imported = _imported_module_names(path.read_text(encoding="utf-8"))
        known = KNOWN_VIOLATIONS.get(path.name, set())
        violations = sorted(
            name for name in imported
            if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_MODULE_PREFIXES)
            and name not in known
        )
        if violations:
            offenders[path.name] = violations

    assert offenders == {}, (
        f"agents/ modules import data-access modules directly: {offenders}. "
        f"All data access must go through a Service in src/backend/services/, "
        f"never a Repository, mock_db_repository, or psycopg directly. If this "
        f"is a newly-discovered, not-yet-migrated case, add it to "
        f"KNOWN_VIOLATIONS with a comment explaining the migration plan -- "
        f"don't silently widen FORBIDDEN_MODULE_PREFIXES to exclude it."
    )


def test_agents_directory_does_not_read_json_fixture_files_directly():
    offenders: dict[str, str] = {}
    for path in _agent_files():
        source = path.read_text(encoding="utf-8")
        if "data/mock_db" in source or "data\\mock_db" in source:
            offenders[path.name] = "references data/mock_db directly"
        elif "json.load(" in source:
            offenders[path.name] = "calls json.load(...) directly"

    assert offenders == {}, (
        f"agents/ modules read a JSON fixture file directly: {offenders}. "
        f"Fixture data must be reached through a Repository's Fixture "
        f"implementation via a Service, never opened by an agent node itself."
    )


def test_no_agents_module_calls_date_today_or_datetime_now_directly():
    # "today" belongs to BusinessClock only -- see business_clock.py's
    # module docstring. A textual check is sufficient: no agents/ module
    # needs the `date`/`datetime` stdlib name bound at all once wall-clock
    # reads go through get_business_clock() instead.
    offenders: dict[str, str] = {}
    for path in _agent_files():
        source = path.read_text(encoding="utf-8")
        if "date.today()" in source:
            offenders[path.name] = "date.today()"
        elif "datetime.now()" in source:
            offenders[path.name] = "datetime.now()"

    assert offenders == {}, (
        f"agents/ modules call the wall clock directly: {offenders}. "
        f"'today' must come from BusinessClock (src.backend.services."
        f"business_clock), injected via get_business_clock(), so tests can "
        f"pin it and the LLM can never smuggle in an arbitrary date."
    )
