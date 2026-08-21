import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


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
    supplied = credentials.credentials if credentials else ""
    for token, principal in configured.items():
        if hmac.compare_digest(
            hashlib.sha256(supplied.encode()).digest(),
            hashlib.sha256(token.encode()).digest(),
        ):
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
