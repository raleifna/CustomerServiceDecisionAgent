"""Static check: no module under src/backend/graph/ may import
mock_db_repository or psycopg directly, or call date.today()/datetime.now()
directly. Mirrors tests/test_agent_data_access_boundary.py's rules for
src/backend/agents/ -- this is the loader layer those agent nodes sit on
top of, and as of the order/customer/product/logistics loader migrations,
workflow.py (previously the last module here reading
mock_db_repository.get_order/get_customer/get_product/get_logistics
directly) is clean too, so the check generalizes to the whole directory.

All data access from graph/*.py now goes through a Repository factory
(get_order_repository() etc.) or a Service (get_logistics_provider(),
get_business_clock(), ...), selected by APP_DATA_BACKEND the same way as
everywhere else in this project -- never a direct mock_db_repository read
or a raw psycopg connection opened inline in a loader/gate/finalization
node.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = REPO_ROOT / "src" / "backend" / "graph"

FORBIDDEN_MODULE_PREFIXES = (
    "src.backend.repositories.mock_db_repository",
    "psycopg",
)

# No known exceptions as of this writing -- see the module docstring. Kept
# as an explicit (currently empty) allowlist, same pattern as
# test_agent_data_access_boundary.py's KNOWN_VIOLATIONS, so a future
# regression has to be added here deliberately rather than the check being
# silently loosened.
KNOWN_VIOLATIONS: dict[str, set[str]] = {}


def _graph_files() -> list[Path]:
    return sorted(p for p in GRAPH_DIR.glob("*.py") if p.name != "__init__.py")


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


def test_graph_directory_does_not_import_mock_db_or_psycopg_directly():
    offenders: dict[str, list[str]] = {}
    for path in _graph_files():
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
        f"graph/ modules import data-access modules directly: {offenders}. "
        f"All data access must go through a Repository factory or a Service, "
        f"never mock_db_repository or psycopg directly."
    )


def test_no_graph_module_calls_date_today_or_datetime_now_directly():
    offenders: dict[str, str] = {}
    for path in _graph_files():
        source = path.read_text(encoding="utf-8")
        if "date.today()" in source:
            offenders[path.name] = "date.today()"
        elif "datetime.now()" in source:
            offenders[path.name] = "datetime.now()"

    assert offenders == {}, (
        f"graph/ modules call the wall clock directly: {offenders}. "
        f"'today' must come from BusinessClock (src.backend.services."
        f"business_clock), injected via get_business_clock()."
    )
