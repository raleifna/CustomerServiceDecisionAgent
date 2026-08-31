"""Static check: query_agent.py (and any future Agent Tool module under
src/backend/agents/) must never import mock_db_repository, a Repository
module, or raw JSON loading directly -- all data access goes through
QueryToolProvider / OrderContextService. This is a source-level check, not
a runtime mock, so it catches the violation even if no test happens to
exercise the offending import path.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "src" / "backend" / "agents"

FORBIDDEN_MODULE_PREFIXES = (
    "src.backend.repositories.mock_db_repository",
    "src.backend.repositories.order_repository",
    "src.backend.repositories.product_repository",
    "src.backend.repositories.shipment_repository",
    "src.backend.repositories.payment_repository",
    "src.backend.repositories.postgres_repository",
)

# query_agent.py is the one module this check is actually about right now
# (the rest of the Graph's after-sales nodes still read via
# mock_db_repository directly -- that migration is later, separate work,
# see the OrderContextService/Repository rollout notes). Listed explicitly
# rather than scanning every agents/*.py file so this test's failure
# message stays precise about what it's actually checking today.
CHECKED_FILES = ["query_agent.py"]


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


def test_query_agent_does_not_import_repositories_or_mock_db_directly():
    path = AGENTS_DIR / "query_agent.py"
    imported = _imported_module_names(path.read_text(encoding="utf-8"))

    violations = [
        name for name in imported
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_MODULE_PREFIXES)
    ]
    assert violations == [], (
        f"query_agent.py imports data-access modules directly: {violations}. "
        f"All order/logistics data must come through "
        f"src.backend.services.query_tool_provider, not a Repository or mock_db_repository."
    )


def test_query_agent_does_not_call_date_today_directly():
    # date.today() belongs to BusinessClock only -- see business_clock.py's
    # module docstring. A textual check is sufficient here since query_agent.py
    # doesn't even import `date` anymore after the provider migration.
    path = AGENTS_DIR / "query_agent.py"
    source = path.read_text(encoding="utf-8")
    assert "date.today()" not in source, (
        "query_agent.py calls date.today() directly -- 'today' must come from "
        "BusinessClock (src.backend.services.business_clock), injected via "
        "get_business_clock(), so tests can pin it and the LLM can never smuggle "
        "in an arbitrary date."
    )
