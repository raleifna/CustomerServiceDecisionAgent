"""Live human chat: customer and staff endpoints plus Server-Sent Events.

Streams poll PostgreSQL once a second from a worker thread (no in-process
pub/sub), so they keep working with several backend instances behind a load
balancer; at this project's volume the extra queries are negligible. Each
stream sends `message` and `handoff` events, the staff stream also a `queue`
event whenever the pending-work counts change, plus a comment heartbeat so
proxies do not drop an idle connection.
"""

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Callable

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.backend.api.customer_auth import current_customer_id
from src.backend.api.staff_auth import StaffPrincipal, require_roles
from src.backend.repositories import live_chat_repository as live
from src.backend.repositories.audit_repository import write_audit_log
from src.backend.repositories.evidence_repository import list_pending_evidence_for_staff_queue
from src.backend.repositories.service_request_repository import get_service_request, list_staff_service_request_queue
from src.backend.services import commerce_data

logger = logging.getLogger(__name__)

customer_router = APIRouter(prefix="/me/live", tags=["live-chat"])
staff_router = APIRouter(prefix="/staff/live", tags=["live-chat"])

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0


class LiveMessageRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=2000)


class StartLiveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    message: str | None = Field(default=None, max_length=2000)


def _unavailable() -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="人工在线会话需要数据库支持，当前环境不可用。")


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# ---------- SSE plumbing ----------

def _event(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _stream(request: Request, poll: Callable[[dict[str, Any]], list[tuple[str, Any]]], state: dict[str, Any]) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    last_beat = time.monotonic()
    while not await request.is_disconnected():
        try:
            events = await anyio.to_thread.run_sync(poll, state)
        except Exception:
            logger.exception("Live stream poll failed")
            events = []
        for name, data in events:
            yield _event(name, data)
        if time.monotonic() - last_beat > HEARTBEAT_SECONDS:
            yield ": ping\n\n"
            last_beat = time.monotonic()
        await asyncio.sleep(POLL_SECONDS)


def _sse(request: Request, poll, state) -> StreamingResponse:
    return StreamingResponse(
        _stream(request, poll, state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _live_poll(customer_id: str | None):
    def poll(state: dict[str, Any]) -> list[tuple[str, Any]]:
        changes = live.changes_since(
            after_message_id=state["after"],
            since_handoff_update=state["since"],
            customer_id=customer_id,
        )
        events: list[tuple[str, Any]] = []
        if state["primed"]:
            events += [("handoff", handoff) for handoff in changes["handoffs"]]
        events += [("message", message) for message in changes["messages"]]
        if changes["messages"]:
            state["after"] = changes["messages"][-1]["message_id"]
        state["since"] = changes["max_updated_at"]
        state["primed"] = True
        return events
    return poll


# ---------- customer ----------

@customer_router.get("")
def get_my_live_session(customer_id: str = Depends(current_customer_id)) -> dict:
    """The customer's open handoff (or null) with its messages."""
    handoff = live.open_handoff_for_customer(customer_id)
    return {"handoff": handoff, "messages": live.list_messages(handoff["handoff_id"]) if handoff else []}


@customer_router.post("/messages", status_code=status.HTTP_201_CREATED)
def send_my_live_message(payload: LiveMessageRequest, customer_id: str = Depends(current_customer_id)) -> dict:
    try:
        handoff = live.open_handoff_for_customer(customer_id)
        if handoff is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="当前没有进行中的人工会话。")
        return live.add_message(handoff["handoff_id"], sender="customer", sender_id=customer_id, content=payload.content)
    except live.LiveChatUnavailable as exc:
        raise _unavailable() from exc
    except live.LiveChatConflict as exc:
        raise _conflict(exc) from exc


@customer_router.post("/end")
def end_my_live_session(customer_id: str = Depends(current_customer_id)) -> dict:
    try:
        handoff = live.open_handoff_for_customer(customer_id)
        if handoff is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="当前没有进行中的人工会话。")
        return live.end_handoff(handoff["handoff_id"], ended_by="customer", reason="顾客结束会话")
    except live.LiveChatUnavailable as exc:
        raise _unavailable() from exc
    except live.LiveChatConflict as exc:
        raise _conflict(exc) from exc


@customer_router.get("/stream")
def stream_my_live_session(request: Request, after: int = 0, customer_id: str = Depends(current_customer_id)) -> StreamingResponse:
    """SSE; authenticate with ?access_token= (EventSource cannot set headers).
    `after` is the last message id the client already has; without it the
    stream starts from now instead of replaying old conversations."""
    return _sse(request, _live_poll(customer_id), {"after": after or live.latest_message_id(), "since": None, "primed": False})


# ---------- staff ----------

def _with_context(handoff: dict[str, Any]) -> dict[str, Any]:
    order = commerce_data.get_order(handoff["order_id"]) if handoff.get("order_id") else None
    return {**handoff, "product": commerce_data.product_card_for_order(handoff["order_id"], order, include_v2=True) if order else None}


@staff_router.get("")
def list_live_sessions(_: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin"))) -> list[dict]:
    return [_with_context(handoff) for handoff in live.list_handoffs()]


@staff_router.get("/stream")
def stream_live_sessions(
    request: Request,
    after: int = 0,
    _: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> StreamingResponse:
    """SSE for the workbench: live messages, handoff changes and a `queue`
    event when the human-queue / evidence / waiting-chat counts change."""
    live_poll = _live_poll(None)

    def poll(state: dict[str, Any]) -> list[tuple[str, Any]]:
        events = live_poll(state)
        now = time.monotonic()
        if now - state.get("queue_checked", 0) >= 5:
            state["queue_checked"] = now
            counts = {
                "service_requests": len(list_staff_service_request_queue()),
                "evidence": len(list_pending_evidence_for_staff_queue()),
                "waiting_chats": sum(1 for h in live.list_handoffs(include_recent_ended=0) if h["workflow_status"] == "Waiting"),
            }
            if counts != state.get("counts"):
                if state.get("counts") is not None:
                    events.append(("queue", counts))
                state["counts"] = counts
        return events

    return _sse(request, poll, {"after": after or live.latest_message_id(), "since": None, "primed": False})


@staff_router.get("/{handoff_id}")
def get_live_session(
    handoff_id: str,
    request: Request,
    _: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> dict:
    handoff = live.get_handoff(handoff_id)
    if handoff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在。")
    return {
        "handoff": _with_context(handoff),
        "messages": live.list_messages(handoff_id),
        "ai_transcript": _ai_transcript(request, handoff.get("conversation_id")),
        "service_request": get_service_request(handoff["request_id"]) if handoff.get("request_id") else None,
    }


def _ai_transcript(request: Request, conversation_id: str | None) -> list[dict[str, str]]:
    """The AI conversation before the handoff, from the graph checkpoint."""
    if not conversation_id:
        return []
    graph = getattr(request.app.state, "conversation_graph", None)
    if graph is None:
        return []
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": conversation_id}})
    except Exception:
        logger.exception("Could not load AI transcript for %s", conversation_id)
        return []
    history = (snapshot.values or {}).get("dialogue_history") or []
    return [item for item in history if isinstance(item, dict) and item.get("content")]


def _audit(principal: StaffPrincipal, action: str, handoff_id: str, details: dict | None = None) -> None:
    write_audit_log(
        actor_id=principal.staff_id, actor_role=sorted(principal.roles)[0], action=action,
        resource_type="HumanHandoff", resource_id=handoff_id, outcome="Succeeded", details=details or {},
    )


@staff_router.post("/{handoff_id}/accept")
def accept_live_session(handoff_id: str, principal: StaffPrincipal = Depends(require_roles("reviewer", "admin"))) -> dict:
    try:
        accepted = live.accept_handoff(handoff_id, principal.staff_id)
    except live.LiveChatUnavailable as exc:
        raise _unavailable() from exc
    except live.LiveChatConflict as exc:
        raise _conflict(exc) from exc
    _audit(principal, "AcceptHandoff", handoff_id)
    return accepted


def _require_owner(handoff_id: str, principal: StaffPrincipal) -> dict[str, Any]:
    handoff = live.get_handoff(handoff_id)
    if handoff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在。")
    if handoff["workflow_status"] != "Active" or handoff["staff_id"] != principal.staff_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请先接入该会话（或该会话由其他客服负责）。")
    return handoff


@staff_router.post("/{handoff_id}/messages", status_code=status.HTTP_201_CREATED)
def send_live_message(
    handoff_id: str,
    payload: LiveMessageRequest,
    principal: StaffPrincipal = Depends(require_roles("reviewer", "admin")),
) -> dict:
    try:
        _require_owner(handoff_id, principal)
        return live.add_message(handoff_id, sender="staff", sender_id=principal.staff_id, content=payload.content)
    except live.LiveChatUnavailable as exc:
        raise _unavailable() from exc
    except live.LiveChatConflict as exc:
        raise _conflict(exc) from exc


@staff_router.post("/{handoff_id}/end")
def end_live_session(handoff_id: str, principal: StaffPrincipal = Depends(require_roles("reviewer", "admin"))) -> dict:
    try:
        _require_owner(handoff_id, principal)
        ended = live.end_handoff(handoff_id, ended_by="staff", reason=f"{principal.staff_id} 结束会话")
    except live.LiveChatUnavailable as exc:
        raise _unavailable() from exc
    except live.LiveChatConflict as exc:
        raise _conflict(exc) from exc
    _audit(principal, "EndHandoff", handoff_id)
    return ended


@staff_router.post("/start-from-request/{request_id}", status_code=status.HTTP_201_CREATED)
def start_live_session_from_request(
    request_id: str,
    payload: StartLiveRequest,
    principal: StaffPrincipal = Depends(require_roles("reviewer", "admin")),
) -> dict:
    """Staff reach out to the customer of a case (e.g. to ask for details)."""
    service_request = get_service_request(request_id)
    if service_request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="售后申请不存在。")
    try:
        handoff = live.create_or_reuse_handoff(
            customer_id=service_request["customer_id"],
            reason=f"客服就售后申请 {request_id} 联系顾客",
            order_id=service_request.get("order_id"),
            request_id=request_id,
            staff_id=principal.staff_id,
        )
        if handoff["workflow_status"] == "Waiting":
            handoff = live.accept_handoff(handoff["handoff_id"], principal.staff_id)
        elif handoff["staff_id"] != principal.staff_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"该顾客正在与 {handoff['staff_id']} 会话中。")
        if payload.message:
            live.add_message(handoff["handoff_id"], sender="staff", sender_id=principal.staff_id, content=payload.message)
    except live.LiveChatUnavailable as exc:
        raise _unavailable() from exc
    except live.LiveChatConflict as exc:
        raise _conflict(exc) from exc
    _audit(principal, "StartHandoff", handoff["handoff_id"], {"request_id": request_id})
    return handoff
