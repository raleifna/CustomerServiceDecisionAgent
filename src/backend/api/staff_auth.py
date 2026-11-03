"""Staff authentication.

Staff identities are configured in STAFF_API_KEYS (access key -> staff_id +
roles). The workbench exchanges an access key for a short-lived signed
session token at POST /staff/auth/login, so the long-lived key is not kept in
the browser; API requests then carry the session token. A raw access key is
still accepted as a bearer credential for scripts and automation.
"""
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.backend.api.auth_tokens import STAFF_TOKEN_TTL_SECONDS, issue_token, verify_token


@dataclass(frozen=True)
class StaffPrincipal:
    staff_id: str
    roles: frozenset[str]


_bearer = HTTPBearer(auto_error=False)


def _configured_credentials() -> dict[str, StaffPrincipal]:
    raw = os.getenv("STAFF_API_KEYS", "").strip()
    if not raw:
        return {}
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("STAFF_API_KEYS 不是有效 JSON") from exc
    if not isinstance(document, dict):
        raise RuntimeError("STAFF_API_KEYS 必须是 token 到员工身份的 JSON 对象")
    credentials = {}
    for token, identity in document.items():
        if not isinstance(token, str) or not isinstance(identity, dict):
            raise RuntimeError("STAFF_API_KEYS 条目格式错误")
        staff_id = identity.get("staff_id")
        roles = identity.get("roles", [])
        if not isinstance(staff_id, str) or not isinstance(roles, list):
            raise RuntimeError("Staff 身份必须包含 staff_id 和 roles")
        credentials[token] = StaffPrincipal(staff_id, frozenset(map(str, roles)))
    return credentials


def _principal_for_key(configured: dict[str, StaffPrincipal], supplied: str) -> StaffPrincipal | None:
    for token, principal in configured.items():
        if hmac.compare_digest(
            hashlib.sha256(supplied.encode()).digest(),
            hashlib.sha256(token.encode()).digest(),
        ):
            return principal
    return None


def _principal_for_staff_id(configured: dict[str, StaffPrincipal], staff_id: str) -> StaffPrincipal | None:
    return next((principal for principal in configured.values() if principal.staff_id == staff_id), None)


def authenticate_staff(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> StaffPrincipal:
    configured = _configured_credentials()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Staff API 尚未配置身份凭证。",
        )
    supplied = credentials.credentials if credentials else request.query_params.get("access_token", "")
    session = verify_token(supplied, kind="staff")
    if session is not None:
        # Re-resolve against the current configuration so a removed or
        # re-roled staff member loses access without waiting for expiry.
        principal = _principal_for_staff_id(configured, session["sub"])
        if principal is not None:
            request.state.staff_principal = principal
            return principal
    principal = _principal_for_key(configured, supplied)
    if principal is not None:
        request.state.staff_principal = principal
        return principal
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Staff 身份凭证无效。",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_roles(*allowed: str) -> Callable:
    def dependency(principal: StaffPrincipal = Depends(authenticate_staff)) -> StaffPrincipal:
        if not principal.roles.intersection(allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前 Staff 角色没有执行该操作的权限。",
            )
        return principal
    return dependency


router = APIRouter(prefix="/staff/auth", tags=["staff"])


class StaffLoginRequest(BaseModel):
    access_key: str = Field(min_length=1, max_length=512)


class StaffLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    staff_id: str
    roles: list[str]


@router.post("/login", response_model=StaffLoginResponse)
def staff_login(payload: StaffLoginRequest) -> StaffLoginResponse:
    from src.backend.repositories.audit_repository import write_audit_log

    configured = _configured_credentials()
    if not configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Staff API 尚未配置身份凭证。")
    principal = _principal_for_key(configured, payload.access_key.strip())
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效。")
    token, expires = issue_token(
        "staff", principal.staff_id, ttl_seconds=STAFF_TOKEN_TTL_SECONDS,
        claims={"roles": sorted(principal.roles)},
    )
    write_audit_log(
        actor_id=principal.staff_id,
        actor_role=sorted(principal.roles)[0] if principal.roles else "none",
        action="StaffLogin",
        resource_type="StaffSession",
        resource_id=principal.staff_id,
        outcome="Succeeded",
        details={},
    )
    return StaffLoginResponse(
        access_token=token,
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        staff_id=principal.staff_id,
        roles=sorted(principal.roles),
    )
