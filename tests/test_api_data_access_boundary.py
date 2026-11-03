"""Static check: the HTTP API layer (src/backend/api/) reads order, customer,
product and logistics data only through services/commerce_data.py -- never
the fixture JSON (mock_db_repository) or a business-data repository
directly. Which backend owns an id (fixture vs V2 Postgres) is decided in
that one facade. Same style as tests/test_agent_data_access_boundary.py.

After-sales lifecycle repositories (service requests, evidence, decisions,
return orders, audit, live chat) are the API's own write model and stay
allowed; so is the presentation-only storefront catalog.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "src" / "backend" / "api"

FORBIDDEN = (
    "src.backend.repositories.mock_db_repository",
    "src.backend.repositories.order_repository",
    "src.backend.repositories.customer_repository",
    "src.backend.repositories.product_repository",
    "src.backend.repositories.shipment_repository",
    "src.backend.repositories.payment_repository",
    "src.backend.repositories.order_item_repository",
    "psycopg",
)


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_api_modules_read_business_data_only_through_the_facade():
    violations = {
        path.name: sorted(name for name in _imports(path) if any(name == f or name.startswith(f + ".") for f in FORBIDDEN))
        for path in sorted(API_DIR.glob("*.py"))
    }
    violations = {name: found for name, found in violations.items() if found}
    assert not violations, f"API modules bypass services/commerce_data.py: {violations}"


def test_the_check_actually_scans_the_api_modules():
    assert {"main.py", "staff.py", "live_chat.py", "customer_auth.py"} <= {p.name for p in API_DIR.glob("*.py")}
