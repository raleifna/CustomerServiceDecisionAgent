import pytest

from src.backend.repositories.payment_repository import (
    FixturePaymentRepository,
    NoPaymentsForOrderError,
    PaymentFacts,
    PaymentNotFoundError,
    get_payment_repository,
)


@pytest.fixture
def repo():
    return FixturePaymentRepository()


def test_get_by_id_always_not_found_on_fixture(repo):
    # The legacy fixture schema has no payments table/concept at all --
    # this is a documented backend gap, not a bug.
    with pytest.raises(PaymentNotFoundError):
        repo.get_by_id("PAY-ANY-ID")


def test_list_by_order_id_always_no_payments_on_fixture(repo):
    with pytest.raises(NoPaymentsForOrderError):
        repo.list_by_order_id("ORD2026080001")  # a real fixture order, still no payment rows


def test_get_payment_facts_always_no_payments_on_fixture(repo):
    with pytest.raises(NoPaymentsForOrderError):
        repo.get_payment_facts("ORD2026080001")


def test_payment_facts_has_no_refund_status_field():
    # PaymentRepository never judges refund status -- that's refunds.refund_status,
    # a different table entirely. Assert the dataclass shape enforces this.
    field_names = {f for f in PaymentFacts.__dataclass_fields__}
    assert "refund_status" not in field_names
    assert "is_refunded" not in field_names


def test_get_payment_repository_defaults_to_fixture(monkeypatch):
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)
    assert isinstance(get_payment_repository(), FixturePaymentRepository)


def test_get_payment_repository_db_backend_without_database_url_raises(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_payment_repository()
