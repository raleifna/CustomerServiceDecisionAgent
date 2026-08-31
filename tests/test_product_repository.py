from decimal import Decimal

import pytest

from src.backend.repositories.product_repository import (
    FixtureProductRepository,
    Product,
    ProductNotFoundError,
    get_product_repository,
)


@pytest.fixture
def repo():
    return FixtureProductRepository()


def test_get_by_id_returns_known_fixture_product(repo):
    product = repo.get_by_id("PRO0001")
    assert isinstance(product, Product)
    assert product.category == "数码"
    assert product.price == Decimal("699.0")
    assert product.is_fragile is False


def test_get_by_id_raises_not_found(repo):
    with pytest.raises(ProductNotFoundError):
        repo.get_by_id("PRO9999-DOES-NOT-EXIST")


def test_get_by_source_id_always_not_found_on_fixture(repo):
    # Fixture products were hand-authored, never sourced from Amazon --
    # there is no source_product_id to match, by design.
    with pytest.raises(ProductNotFoundError):
        repo.get_by_source_id("B000ANY0000")


def test_list_by_ids_batches_correctly(repo):
    products = repo.list_by_ids(["PRO0001", "PRO0002", "PRO9999-DOES-NOT-EXIST"])
    ids = {p.product_id for p in products}
    assert ids == {"PRO0001", "PRO0002"}  # unknown id silently omitted, not an error
    assert all(isinstance(p.price, Decimal) for p in products)


def test_list_by_ids_empty_input_returns_empty_list(repo):
    assert repo.list_by_ids([]) == []


def test_get_product_facts_has_no_refund_eligibility_field(repo):
    facts = repo.get_product_facts("PRO0002")
    assert facts.category == "家电"
    assert facts.is_fragile is True
    assert not hasattr(facts, "refund_eligible")
    assert not hasattr(facts, "is_refundable")


def test_get_product_provenance_is_empty_for_fixture_data(repo):
    provenance = repo.get_product_provenance("PRO0001")
    assert provenance.product_id == "PRO0001"
    assert provenance.records == []


def test_get_product_provenance_raises_not_found_for_unknown_product(repo):
    with pytest.raises(ProductNotFoundError):
        repo.get_product_provenance("PRO9999-DOES-NOT-EXIST")


def test_get_product_repository_defaults_to_fixture(monkeypatch):
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)
    assert isinstance(get_product_repository(), FixtureProductRepository)


def test_get_product_repository_db_backend_without_database_url_raises(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_product_repository()
