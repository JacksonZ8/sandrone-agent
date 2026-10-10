from __future__ import annotations

import json
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from ...models import ToolConfig
from .search import normalize_provider


def seed_tool_config(session: Session) -> ToolConfig:
    config = session.get(ToolConfig, 1)
    if config is None:
        config = ToolConfig(id=1)
        session.add(config)
        try:
            session.commit()
        except IntegrityError:
            # 设置页并发请求可能同时播种同一行：输的一方读取已创建的那行。
            session.rollback()
            return session.get(ToolConfig, 1)
        session.refresh(config)
    return config


def get_tool_config(session: Session) -> ToolConfig:
    return seed_tool_config(session)


def file_tool_public(config: ToolConfig) -> dict[str, Any]:
    try:
        allowlist = json.loads(config.file_allowlist or "[]")
    except json.JSONDecodeError:
        allowlist = []
    return {
        "files_enabled": config.files_enabled,
        "file_allowlist": [str(x) for x in allowlist],
    }


def search_tool_public(config: ToolConfig) -> dict[str, Any]:
    key = config.search_api_key or ""
    return {
        "search_provider": normalize_provider(config.search_provider),
        "has_api_key": bool(key),
        "api_key_masked": (key[:3] + "••••" + key[-4:]) if len(key) > 8 else ("••••" if key else ""),
    }
