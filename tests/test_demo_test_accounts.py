"""The generated demo test accounts (scripts/demo/seed_test_accounts.py) are
an overlay on top of the hand-built evaluation fixtures. These tests lock in
that the overlay never collides with, or rewrites, the fixtures, and that
mock_db_repository.load_table() merges it."""

import json
import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "demo"))

import seed_test_accounts  # noqa: E402
from src.backend.repositories import mock_db_repository  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "data" / "mock_db"
TODAY = date(2026, 11, 3)


def _fixture(filename: str) -> list[dict]:
    return json.loads((FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def test_generated_ids_are_disjoint_from_fixture_ids():
    tables = seed_test_accounts.build(TODAY)
    for filename, key in (
        ("customers.json", "customer_id"),
        ("orders.json", "order_id"),
        ("order_items.json", "order_item_id"),
    ):
        fixture_ids = {row[key] for row in _fixture(filename)}
        generated_ids = [row[key] for row in tables[filename]]
        assert len(generated_ids) == len(set(generated_ids)), f"duplicate {key} in generated {filename}"
        assert not fixture_ids & set(generated_ids), f"generated {filename} collides with fixture ids"


def test_generated_ids_keep_the_fixture_shapes():
    tables = seed_test_accounts.build(TODAY)
    for row in tables["customers.json"]:
        assert row["customer_id"].startswith("CUS") and len(row["customer_id"]) == 7
    for row in tables["orders.json"]:
        assert row["order_id"].startswith("ORD99") and row["order_id"][3:].isdigit() and len(row["order_id"]) == 13


def test_generated_orders_are_internally_consistent():
    tables = seed_test_accounts.build(TODAY)
    customers = {row["customer_id"] for row in tables["customers.json"]}
    products = {row["product_id"] for row in _fixture("products.json")}
    for order in tables["orders.json"]:
        assert order["customer_id"] in customers
        items = [item for item in tables["order_items.json"] if item["order_id"] == order["order_id"]]
        assert items, f"{order['order_id']} has no order items"
        assert order["product_id"] == items[0]["product_id"]
        assert round(sum(item["paid_amount"] for item in items), 2) == order["amount"]
        assert all(item["product_id"] in products for item in items)
        if order["delivered_date"]:
            assert order["shipped_date"] <= order["delivered_date"] <= TODAY.isoformat()


def test_dates_are_relative_to_the_anchor_day():
    later = date(2027, 1, 10)
    first = {row["order_id"]: row for row in seed_test_accounts.build(TODAY)["orders.json"]}
    second = {row["order_id"]: row for row in seed_test_accounts.build(later)["orders.json"]}
    assert first.keys() == second.keys(), "ids must stay stable across regenerations"
    shift = (later - TODAY).days
    for order_id, row in first.items():
        moved = date.fromisoformat(second[order_id]["order_date"])
        assert (moved - date.fromisoformat(row["order_date"])).days == shift


def test_load_table_appends_the_overlay_after_fixture_rows():
    with TemporaryDirectory() as directory:
        overlay = Path(directory)
        (overlay / "customers.json").write_text(
            json.dumps([{"customer_id": "CUS0999", "vip_level": "Gold", "register_date": "2026-01-01", "refund_history_count": 0}]),
            encoding="utf-8",
        )
        with patch.object(mock_db_repository, "TEST_ACCOUNTS_DIR", overlay):
            rows = mock_db_repository.load_table("customers.json")
            assert rows[: len(_fixture("customers.json"))] == _fixture("customers.json")
            assert rows[-1]["customer_id"] == "CUS0999"
            # Tables without an overlay file are just the fixture.
            assert mock_db_repository.load_table("products.json") == _fixture("products.json")
            assert mock_db_repository.load_test_account_guide() == {}


def test_every_generated_account_has_a_guide_entry():
    tables = seed_test_accounts.build(TODAY)
    guided = {row["customer_id"] for row in tables["accounts_guide.json"]}
    assert guided == {row["customer_id"] for row in tables["customers.json"]}
    assert all(row["title"] and row["hints"] for row in tables["accounts_guide.json"])
