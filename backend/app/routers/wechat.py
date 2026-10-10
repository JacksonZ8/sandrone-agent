from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..database import get_session
from ..schemas import (
    WeChatConfigUpdate,
    WeChatLoginStart,
    WeChatLoginStatus,
    WeChatPairingStart,
    WeChatSendRequest,
    WeChatSendResponse,
    WeChatStatus,
)
from ..services.wechat_service import (
    apply_wechat_config,
    get_runtime,
    poll_wechat_login,
    start_owner_pairing,
    start_wechat_login,
    wechat_status,
)

router = APIRouter(prefix="/api/wechat", tags=["wechat"])


@router.get("/status", response_model=WeChatStatus)
def read_wechat_status(session: Session = Depends(get_session)) -> dict:
    return wechat_status(session)


@router.put("/config", response_model=WeChatStatus)
async def update_wechat_config(
    payload: WeChatConfigUpdate,
    session: Session = Depends(get_session),
) -> dict:
    account = apply_wechat_config(session, payload.model_dump(exclude_unset=True))
    await get_runtime().restart()
    return wechat_status(session)


@router.post("/login/start", response_model=WeChatLoginStart)
async def wechat_login_start() -> dict:
    return await start_wechat_login()


@router.get("/login/status", response_model=WeChatLoginStatus)
async def wechat_login_status(qrcode: str = Query(min_length=4)) -> dict:
    return await poll_wechat_login(qrcode)


@router.post("/pairing/start", response_model=WeChatPairingStart)
def wechat_pairing_start(session: Session = Depends(get_session)) -> dict:
    return start_owner_pairing(session)


@router.post("/logout")
async def wechat_logout(session: Session = Depends(get_session)) -> dict:
    from ..services.wechat_service import get_wechat_account

    account = get_wechat_account(session)
    account.enabled = False
    account.status = "stopped"
    account.owner_user_id = ""
    account.context_tokens = "{}"
    account.last_sync_buf = ""
    account.last_error = ""
    session.add(account)
    session.commit()
    await get_runtime().stop()
    return {"ok": True, "message": "微信账号已断开。"}


@router.post("/send", response_model=WeChatSendResponse)
async def wechat_send(payload: WeChatSendRequest) -> dict:
    try:
        await get_runtime().send_text(payload.chat_id, payload.text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": "已发送。"}
