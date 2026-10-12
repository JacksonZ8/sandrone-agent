"""微信与主动消息的接口契约（前端 types.ts 手工同步）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WeChatConfigUpdate(BaseModel):
    enabled: bool | None = None
    account_id: str | None = Field(default=None, max_length=128)
    token: str | None = Field(default=None, max_length=1024)
    base_url: str | None = Field(default=None, max_length=512)
    cdn_base_url: str | None = Field(default=None, max_length=512)
    owner_user_id: str | None = Field(default=None, max_length=128)


class WeChatStatus(BaseModel):
    enabled: bool
    name: str
    account_id: str
    account_id_masked: str
    has_token: bool
    token_masked: str
    base_url: str
    owner_user_id: str
    owner_paired: bool
    status: str
    last_error: str
    updated_at: datetime


class WeChatLoginStart(BaseModel):
    ok: bool
    qrcode: str
    qr_url: str
    qr_data_url: str
    message: str


class WeChatLoginStatus(BaseModel):
    status: str
    message: str
    account_id: str = ""
    base_url: str = ""


class WeChatPairingStart(BaseModel):
    ok: bool
    code: str
    expires_in_seconds: int
    message: str


class ProactiveConfigUpdate(BaseModel):
    enabled: bool | None = None
    daily_max: int | None = Field(default=None, ge=1, le=4)
    quiet_start: str | None = Field(default=None, max_length=5)
    quiet_end: str | None = Field(default=None, max_length=5)
    min_idle_minutes: int | None = Field(default=None, ge=0, le=1440)


class ProactiveConfigPublic(BaseModel):
    enabled: bool
    daily_max: int
    quiet_start: str
    quiet_end: str
    min_idle_minutes: int
    updated_at: datetime


class ProactiveEventPublic(BaseModel):
    id: int
    status: str
    reason: str
    message_id: int | None
    created_at: datetime


class ProactivePreview(BaseModel):
    send: bool
    text: str


class WeChatSendRequest(BaseModel):
    chat_id: str
    text: str = Field(min_length=1, max_length=2000)


class WeChatSendResponse(BaseModel):
    ok: bool
    message: str
