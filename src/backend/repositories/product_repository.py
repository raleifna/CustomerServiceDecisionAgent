"""Product catalog read repository: same Protocol + two-implementation +
explicit-APP_DATA_BACKEND pattern as customer_repository.py.

Boundary this module exists to enforce -- do not blur it when wiring
callers: `products.price` is this system's CATALOG reference price for a
product (Amazon-sourced, DERIVED via a fixed USD->CNY rate, for the V2
import batch). It is NEVER a specific order's transaction price. The price
actually paid for a given order line lives on `order_items.unit_price` /
`order_items.paid_amount` (Olist's real, REAL-tagged transaction price) and
is read through OrderRepository, not here. A caller that needs "what did
the customer pay" must go to order_items; a caller that needs "what does
this product currently list for" comes here.
"""

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from src.backend.repositories import mock_db_repository
from src.backend.repositories.postgres_repository import get_database_url


class ProductNotFoundError(LookupError):
    """No product matches the given id in the selected backend."""


@dataclass(frozen=True)
class Product:
    product_id: str
    category: str
    price: Decimal   # catalog reference price -- see module docstring
    is_fragile: bool
    is_customized: bool


@dataclass(frozen=True)
class ProductFacts:
    """Facts and business-enrichment attributes only -- no refund
    eligibility judgment. Whether a product's is_fragile/is_customized
    flags make a given request eligible for something is Policy Agent's
    call, not this repository's."""
    product_id: str
    category: str
    price: Decimal
    is_fragile: bool
    is_customized: bool


@dataclass(frozen=True)
class ProvenanceRecord:
    field_name: str | None   # None = the claim is about the whole product row
    source_type: str
    source_dataset: str | None
    source_record_id: str | None
    transformation: str | None
    pipeline_version: str | None
    metadata: dict | None   # already a structured object, never a JSON string


@dataclass(frozen=True)
class ProductProvenance:
    """For admin/debug/eval use only -- do NOT wire this into any
    customer-facing API response. It exists so a reviewer or an evaluation
    script can answer "where did this product's data come from and how was
    it mapped", not so a customer can see it."""
    product_id: str
    records: list[ProvenanceRecord]


class ProductRepository(Protocol):
    def get_by_id(self, product_id: str) -> Product: ...
    def get_by_source_id(self, source_product_id: str) -> Product: ...
    def list_by_ids(self, product_ids: list[str]) -> list[Product]: ...
    def get_product_facts(self, product_id: str) -> ProductFacts: ...
    def get_product_provenance(self, product_id: str) -> ProductProvenance: ...


class PostgresProductRepository:
    """Reads the real `products` + `data_provenance` tables. Raises
    ProductNotFoundError for a missing id; lets any psycopg connection/query
    error propagate unmodified -- a database outage must be visible, not
    masked by a fallback to fixture data."""

    def __init__(self, database_url: str):
        self._database_url = database_url

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def get_by_id(self, product_id: str) -> Product:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT product_id, category, price, is_fragile, is_customized "
                "FROM products WHERE product_id = %s",
                (product_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise ProductNotFoundError(product_id)
        return _product_from_row(row)

    def get_by_source_id(self, source_product_id: str) -> Product:
        # The Amazon source id (parent_asin) is only recorded in
        # data_provenance.source_record_id -- products has no column of its
        # own for it, so this resolves through a join rather than a direct
        # lookup.
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT entity_id FROM data_provenance "
                "WHERE entity_type = 'product' AND field_name IS NULL AND source_record_id = %s "
                "LIMIT 1",
                (source_product_id,),
            )
            match = cur.fetchone()
            if match is None:
                raise ProductNotFoundError(source_product_id)
            cur.execute(
                "SELECT product_id, category, price, is_fragile, is_customized "
                "FROM products WHERE product_id = %s",
                (match["entity_id"],),
            )
            row = cur.fetchone()
        if row is None:
            raise ProductNotFoundError(source_product_id)
        return _product_from_row(row)

    def list_by_ids(self, product_ids: list[str]) -> list[Product]:
        # A single ANY(%s) query -- callers building an order detail view
        # must batch through this, never loop get_by_id per line item.
        if not product_ids:
            return []
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT product_id, category, price, is_fragile, is_customized "
                "FROM products WHERE product_id = ANY(%s)",
                (list(product_ids),),
            )
            rows = cur.fetchall()
        return [_product_from_row(r) for r in rows]

    def get_product_facts(self, product_id: str) -> ProductFacts:
        p = self.get_by_id(product_id)
        return ProductFacts(
            product_id=p.product_id, category=p.category, price=p.price,
            is_fragile=p.is_fragile, is_customized=p.is_customized,
        )

    def get_product_provenance(self, product_id: str) -> ProductProvenance:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM products WHERE product_id = %s", (product_id,)
            )
            if cur.fetchone() is None:
                raise ProductNotFoundError(product_id)
            cur.execute(
                "SELECT field_name, source_type, source_dataset, source_record_id, "
                "transformation, pipeline_version, metadata "
                "FROM data_provenance WHERE entity_type = 'product' AND entity_id = %s "
                "ORDER BY field_name NULLS FIRST",
                (product_id,),
            )
            rows = cur.fetchall()
        return ProductProvenance(
            product_id=product_id,
            records=[
                ProvenanceRecord(
                    field_name=r["field_name"], source_type=r["source_type"],
                    source_dataset=r["source_dataset"], source_record_id=r["source_record_id"],
                    transformation=r["transformation"], pipeline_version=r["pipeline_version"],
                    metadata=r["metadata"],  # psycopg decodes JSONB to dict already
                )
                for r in rows
            ],
        )


def _product_from_row(row: dict) -> Product:
    return Product(
        product_id=row["product_id"], category=row["category"],
        price=Decimal(row["price"]), is_fragile=row["is_fragile"],
        is_customized=row["is_customized"],
    )


class FixtureProductRepository:
    """Reads the hand-built JSON fixture (data/mock_db/products.json).

    Known, deliberate gaps versus Postgres:
      - get_by_source_id always raises ProductNotFoundError: fixture
        products were hand-authored, never sourced from Amazon, so there is
        no source_product_id to match.
      - get_product_provenance always returns an empty records list: the
        fixture predates the data_provenance tracking system entirely --
        there is nothing to report, not a REAL/DERIVED/SYNTHETIC claim
        being silently dropped.
    """

    def get_by_id(self, product_id: str) -> Product:
        row = mock_db_repository.get_product(product_id)
        if row is None:
            raise ProductNotFoundError(product_id)
        return _product_from_fixture_row(row)

    def get_by_source_id(self, source_product_id: str) -> Product:
        raise ProductNotFoundError(source_product_id)

    def list_by_ids(self, product_ids: list[str]) -> list[Product]:
        wanted = set(product_ids)
        rows = mock_db_repository.load_table("products.json")
        return [_product_from_fixture_row(r) for r in rows if r.get("product_id") in wanted]

    def get_product_facts(self, product_id: str) -> ProductFacts:
        p = self.get_by_id(product_id)
        return ProductFacts(
            product_id=p.product_id, category=p.category, price=p.price,
            is_fragile=p.is_fragile, is_customized=p.is_customized,
        )

    def get_product_provenance(self, product_id: str) -> ProductProvenance:
        if mock_db_repository.get_product(product_id) is None:
            raise ProductNotFoundError(product_id)
        return ProductProvenance(product_id=product_id, records=[])


def _product_from_fixture_row(row: dict) -> Product:
    return Product(
        product_id=row["product_id"], category=row["category"],
        price=Decimal(str(row["price"])), is_fragile=row["is_fragile"],
        is_customized=row["is_customized"],
    )


def get_product_repository() -> ProductRepository:
    """Same explicit-selection contract as get_customer_repository(): no
    implicit fallback between backends."""
    backend = os.getenv("APP_DATA_BACKEND", "fixture").strip().lower()
    if backend == "postgres":
        database_url = get_database_url()
        if not database_url:
            raise RuntimeError("APP_DATA_BACKEND=postgres requires DATABASE_URL to be set")
        return PostgresProductRepository(database_url)
    if backend == "fixture":
        return FixtureProductRepository()
    raise RuntimeError(f"unknown APP_DATA_BACKEND={backend!r}, expected 'postgres' or 'fixture'")
