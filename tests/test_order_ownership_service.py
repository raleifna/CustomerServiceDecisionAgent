import pytest

from src.backend.repositories.order_repository import (
    FixtureOrderRepository,
    OrderNotFoundError,
)
from src.backend.services.order_ownership_service import (
    AUTHORIZED,
    DENIED,
    OrderOwnershipService,
    get_order_ownership_service,
)
from src.backend.services.tool_errors import (
    DATABASE_UNAVAILABLE,
    DATA_INTEGRITY_ERROR,
    ORDER_NOT_FOUND,
    ToolError,
)


@pytest.fixture
def service():
    return OrderOwnershipService(FixtureOrderRepository())


def test_matching_customer_is_authorized(service):
    # ORD2026080021 belongs to CUS0021 in the fixture data.
    result = service.verify_ownership("ORD2026080021", "CUS0021")
    assert result.status == AUTHORIZED


def test_mismatched_customer_is_denied(service):
    result = service.verify_ownership("ORD2026080021", "CUS-SOMEONE-ELSE")
    assert result.status == DENIED


def test_unknown_order_raises_order_not_found(service):
    with pytest.raises(ToolError) as exc_info:
        service.verify_ownership("ORD9999-DOES-NOT-EXIST", "CUS0021")
    assert exc_info.value.code == ORDER_NOT_FOUND


class _RepoRaising:
    def __init__(self, exc):
        self._exc = exc

    def get_by_id(self, order_id):
        raise self._exc


def test_database_unavailable_is_translated():
    import psycopg

    service = OrderOwnershipService(_RepoRaising(psycopg.OperationalError("connection refused")))
    with pytest.raises(ToolError) as exc_info:
        service.verify_ownership("ORD1", "CUS1")
    assert exc_info.value.code == DATABASE_UNAVAILABLE
    assert exc_info.value.retryable is True


class _StubOrder:
    def __init__(self, customer_id):
        self.customer_id = customer_id


class _RepoReturning:
    def __init__(self, order):
        self._order = order

    def get_by_id(self, order_id):
        return self._order


def test_missing_customer_id_on_the_order_itself_is_data_integrity_error():
    service = OrderOwnershipService(_RepoReturning(_StubOrder(customer_id=None)))
    with pytest.raises(ToolError) as exc_info:
        service.verify_ownership("ORD1", "CUS1")
    assert exc_info.value.code == DATA_INTEGRITY_ERROR


def test_get_order_ownership_service_defaults_to_fixture(monkeypatch):
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)
    service = get_order_ownership_service()
    result = service.verify_ownership("ORD2026080021", "CUS0021")
    assert result.status == AUTHORIZED
