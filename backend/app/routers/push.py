from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session

from ..database import get_session
from ..services import push_service

router = APIRouter(prefix="/api/push", tags=["push"])


class SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionRequest(BaseModel):
    endpoint: str
    keys: SubscriptionKeys


class EndpointRequest(BaseModel):
    endpoint: str


@router.get("/key")
def push_key() -> dict:
    return {"public_key": push_service.public_key()}


@router.post("/subscribe")
def push_subscribe(request: SubscriptionRequest, raw: Request, session: Session = Depends(get_session)) -> dict:
    try:
        push_service.subscribe(session, request.model_dump(), raw.headers.get("user-agent", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/unsubscribe")
def push_unsubscribe(request: EndpointRequest, session: Session = Depends(get_session)) -> dict:
    return {"ok": push_service.unsubscribe(session, request.endpoint)}


@router.post("/test")
async def push_test(session: Session = Depends(get_session)) -> dict:
    sent = await push_service.notify(session, title="桑多涅", body="……这样就能找到你了。", tag="sandrone-test")
    return {"ok": sent > 0, "sent": sent}
