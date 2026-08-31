"""Locks in the invariant the whole "fixture read, live write" hybrid
repository design (service_request_repository.py, order_item_repository.py)
depends on: fixture ids and live/V2 ids never collide. Previously this was
true by coincidence (a shared 'SR' + YYYYMM + 4-digit shape, safe only
because the Postgres sequence hadn't climbed high enough); now it's true by
construction (the 'SRL' prefix, see create_service_request()'s own
comment). These tests fail loudly if that construction ever regresses --
e.g. someone "simplifies" the id generator back to a bare 'SR' prefix, or a
new fixture request is hand-authored with an 'SRL' id by mistake.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_DB_DIR = REPO_ROOT / "data" / "mock_db"

FIXTURE_REQUEST_ID_SHAPE = re.compile(r"^SR\d{10}$")
LIVE_REQUEST_ID_SHAPE = re.compile(r"^SRL\d{10}$")

FIXTURE_ORDER_ID_SHAPE = re.compile(r"^ORD\d{10}$")
V2_ORDER_ID_SHAPE = re.compile(r"^ORD-RW-\d{5}$")


def test_every_fixture_service_request_id_matches_the_fixture_shape_only():
    requests = json.loads((MOCK_DB_DIR / "service_requests.json").read_text(encoding="utf-8"))
    assert requests, "fixture service_requests.json must not be empty for this test to mean anything"
    for record in requests:
        request_id = record["request_id"]
        assert FIXTURE_REQUEST_ID_SHAPE.match(request_id), (
            f"{request_id!r} does not match the fixture shape SR\\d{{10}}"
        )
        assert not LIVE_REQUEST_ID_SHAPE.match(request_id), (
            f"{request_id!r} collides with the live-request shape SRL\\d{{10}} -- "
            f"the whole point of the 'SRL' prefix is that this can never happen"
        )


def test_every_fixture_order_id_matches_the_fixture_shape_only():
    orders = json.loads((MOCK_DB_DIR / "orders.json").read_text(encoding="utf-8"))
    assert orders
    for record in orders:
        order_id = record["order_id"]
        assert FIXTURE_ORDER_ID_SHAPE.match(order_id), (
            f"{order_id!r} does not match the fixture shape ORD\\d{{10}}"
        )
        assert not V2_ORDER_ID_SHAPE.match(order_id), (
            f"{order_id!r} collides with the V2 order-id shape ORD-RW-\\d{{5}}"
        )


def test_live_request_id_generation_sql_uses_the_srl_prefix():
    # A static, textual guard: create_service_request()'s INSERT must keep
    # generating 'SRL' + ... , not silently regress to the bare 'SR' shape
    # that used to collide with the fixture namespace only by coincidence.
    source = (REPO_ROOT / "src" / "backend" / "repositories" / "service_request_repository.py").read_text(
        encoding="utf-8"
    )
    assert "'SRL' || to_char(" in source, (
        "create_service_request()'s id-generation SQL no longer uses the "
        "'SRL' prefix -- this would make live request ids structurally "
        "indistinguishable from the fixture's SR\\d{10} shape again."
    )
