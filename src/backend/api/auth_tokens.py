"""Signed, expiring bearer tokens shared by the customer and staff APIs.

A token is `base64url(json payload).base64url(HMAC-SHA256(payload))`, signed
with AUTH_SECRET. The payload always carries `kind` ("customer" | "staff"),
`sub` (the customer_id / staff_id) and `exp`; a token of one kind is never
accepted where the other is expected. Stateless on purpose: the project runs
a single backend and needs no revocation list -- logging out discards the
token client-side and it expires on its own.

The same signer produces short-lived URLs for evidence images
(`sign_file_path` / `verify_file_signature`): an <img src> cannot send an
Authorization header, so the URL itself carries a signature instead of the
uploads directory being public.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any

logger = logging.getLogger(__name__)

CUSTOMER_TOKEN_TTL_SECONDS = 12 * 3600
STAFF_TOKEN_TTL_SECONDS = 8 * 3600
FILE_URL_TTL_SECONDS = 3600

_ephemeral_secret: bytes | None = None


def _secret() -> bytes:
    """AUTH_SECRET from the environment. Without one, fall back to a random
    per-process secret (tokens then stop working after a restart) and say so
    loudly -- fine for a quick local run, never for a shared deployment."""
    global _ephemeral_secret
    configured = os.getenv("AUTH_SECRET", "").strip()
    if configured:
        return configured.encode()
    if _ephemeral_secret is None:
        logger.warning("AUTH_SECRET is not set; using a random per-process secret (tokens reset on restart)")
        _ephemeral_secret = secrets.token_bytes(32)
    return _ephemeral_secret


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(message: bytes) -> str:
    return _b64(hmac.new(_secret(), message, hashlib.sha256).digest())


def issue_token(kind: str, subject: str, *, ttl_seconds: int, claims: dict[str, Any] | None = None) -> tuple[str, int]:
    """Return (token, expires_at_epoch_seconds)."""
    now = int(time.time())
    payload = {"kind": kind, "sub": subject, "iat": now, "exp": now + ttl_seconds, **(claims or {})}
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{body}.{_signature(body.encode())}", payload["exp"]


def verify_token(token: str, *, kind: str) -> dict[str, Any] | None:
    """The payload if the token is well-formed, correctly signed, of the
    expected kind and unexpired; otherwise None."""
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _signature(body.encode())):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != kind:
        return None
    if not isinstance(payload.get("exp"), int) or payload["exp"] < time.time():
        return None
    if not isinstance(payload.get("sub"), str) or not payload["sub"]:
        return None
    return payload


def sign_file_path(relative_path: str, *, ttl_seconds: int = FILE_URL_TTL_SECONDS) -> str:
    """Query string (`exp=..&sig=..`) authorizing a GET of one uploaded file."""
    expires = int(time.time()) + ttl_seconds
    return f"exp={expires}&sig={_signature(f'file:{relative_path}:{expires}'.encode())}"


def verify_file_signature(relative_path: str, expires: str | None, signature: str | None) -> bool:
    if not expires or not signature or not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(signature, _signature(f"file:{relative_path}:{expires}".encode()))


UPLOADS_PREFIX = "data/uploads/"


def signed_upload_url(storage_path: str) -> str:
    """API-relative URL for an uploaded evidence file, valid for an hour."""
    relative = storage_path.removeprefix(UPLOADS_PREFIX)
    return f"/uploads/{relative}?{sign_file_path(relative)}"
