"""Customer authentication for the storefront API.

Every customer-facing endpoint derives "who is calling" from a signed bearer
token (see auth_tokens.py) via `current_customer_id` -- never from a
customer_id in the URL, query string or body, which any caller could forge.

This is a demo system with no customer passwords, so tokens are issued by a
*demo login*: pick one of the mock accounts. It is only available when
DEMO_LOGIN_ENABLED is true (docker-compose turns it on for the demo); a real
deployment would replace /auth/demo-login with its identity provider and
keep everything downstream of `current_customer_id` unchanged.
"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.backend.api.auth_tokens import CUSTOMER_TOKEN_TTL_SECONDS, issue_token, verify_token
from src.backend.repositories.mock_db_repository import get_customer, load_table, load_test_account_guide

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


def demo_login_enabled() -> bool:
    return os.getenv("DEMO_LOGIN_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _token_from(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials and credentials.credentials:
        return credentials.credentials
    # EventSource (SSE) cannot send headers; it passes the token as a query parameter.
    return request.query_params.get("access_token", "")


def current_customer_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    payload = verify_token(_token_from(request, credentials), kind="customer")
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录。",
            headers={"WWW-Authenticate": "Bearer"},
        )
    customer_id = payload["sub"]
    if get_customer(customer_id) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在，请重新登录。")
    return customer_id


class CustomerProfile(BaseModel):
    customer_id: str
    vip_level: str
    register_date: str
    refund_history_count: int
    test_scenario: str | None = None
    test_hints: list[str] = Field(default_factory=list)


class DemoLoginRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=32)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    customer: CustomerProfile


def _profile(record: dict) -> CustomerProfile:
    guide = load_test_account_guide().get(record["customer_id"], {})
    return CustomerProfile(**record, test_scenario=guide.get("title"), test_hints=guide.get("hints", []))


def _require_demo_login() -> None:
    if not demo_login_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="演示登录未开启。")


@router.get("/demo-accounts", response_model=list[CustomerProfile])
def list_demo_accounts() -> list[CustomerProfile]:
    """The account picker for demo login (only while demo login is enabled)."""
    _require_demo_login()
    return [_profile(record) for record in load_table("customers.json")]


@router.post("/demo-login", response_model=LoginResponse)
def demo_login(payload: DemoLoginRequest) -> LoginResponse:
    _require_demo_login()
    record = get_customer(payload.customer_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在。")
    token, expires = issue_token("customer", record["customer_id"], ttl_seconds=CUSTOMER_TOKEN_TTL_SECONDS)
    return LoginResponse(
        access_token=token,
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        customer=_profile(record),
    )


@router.get("/me", response_model=CustomerProfile)
def get_me(customer_id: str = Depends(current_customer_id)) -> CustomerProfile:
    return _profile(get_customer(customer_id))

