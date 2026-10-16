from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..database import get_session
from ..models import ToolConfig, utcnow
from ..schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AgentToolConfig,
    AgentToolConfigUpdate,
    FileReadRequest,
    FileReadResult,
    FileToolConfig,
    FileToolConfigUpdate,
    ScreenAnalyzeRequest,
    ScreenAnalyzeResponse,
    ScreenToolConfig,
    ScreenToolConfigUpdate,
    SearchRequest,
    SearchResponse,
    SearchToolConfig,
    SearchToolConfigUpdate,
)
from ..services.tool_service import (
    agent_tool_public,
    analyze_screen,
    file_tool_public,
    get_tool_config,
    read_file_with_audit,
    run_delegate_agent,
    screen_tool_public,
    search_tool_public,
    search_web_with_audit,
    update_agent_config,
    update_screen_config,
)
from ..services.tools.search import SEARCH_PROVIDERS

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("/files/config", response_model=FileToolConfig)
def get_files_config(session: Session = Depends(get_session)) -> dict:
    return file_tool_public(get_tool_config(session))


@router.put("/files/config", response_model=FileToolConfig)
def update_files_config(payload: FileToolConfigUpdate, session: Session = Depends(get_session)) -> dict:
    config = get_tool_config(session)
    if payload.files_enabled is not None:
        config.files_enabled = payload.files_enabled
    if payload.file_allowlist is not None:
        cleaned = []
        for item in payload.file_allowlist:
            item = str(item).strip()
            if item and not item.startswith("~") and not item.startswith("/"):
                continue
            cleaned.append(item)
        config.file_allowlist = json.dumps(cleaned, ensure_ascii=False)
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return file_tool_public(config)


@router.post("/files/read", response_model=FileReadResult)
def read_file(payload: FileReadRequest, session: Session = Depends(get_session)) -> dict:
    config = get_tool_config(session)
    result = read_file_with_audit(session, payload.path, config)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.get("/search/config", response_model=SearchToolConfig)
def get_search_config(session: Session = Depends(get_session)) -> dict:
    return search_tool_public(get_tool_config(session))


@router.put("/search/config", response_model=SearchToolConfig)
def update_search_config(payload: SearchToolConfigUpdate, session: Session = Depends(get_session)) -> dict:
    config = get_tool_config(session)
    if payload.search_provider is not None:
        if payload.search_provider not in SEARCH_PROVIDERS:
            raise HTTPException(status_code=422, detail="未知搜索 Provider。")
        config.search_provider = payload.search_provider
    if payload.api_key is not None:
        config.search_api_key = payload.api_key.strip()
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return search_tool_public(config)


@router.post("/search", response_model=SearchResponse)
def search(payload: SearchRequest, session: Session = Depends(get_session)) -> dict:
    config = get_tool_config(session)
    try:
        results = search_web_with_audit(session, payload.query, config, limit=payload.max_results)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "query": payload.query, "results": results, "message": ""}


@router.get("/screen/config", response_model=ScreenToolConfig)
def get_screen_config(session: Session = Depends(get_session)) -> dict:
    return screen_tool_public(get_tool_config(session))


@router.put("/screen/config", response_model=ScreenToolConfig)
def put_screen_config(payload: ScreenToolConfigUpdate, session: Session = Depends(get_session)) -> dict:
    return update_screen_config(session, payload.model_dump(exclude_unset=True))


@router.post("/screen/analyze", response_model=ScreenAnalyzeResponse)
def screen_analyze(payload: ScreenAnalyzeRequest, session: Session = Depends(get_session)) -> dict:
    return analyze_screen(session, payload.question)


@router.get("/agent/config", response_model=AgentToolConfig)
def get_agent_config(session: Session = Depends(get_session)) -> dict:
    return agent_tool_public(get_tool_config(session))


@router.put("/agent/config", response_model=AgentToolConfig)
def put_agent_config(payload: AgentToolConfigUpdate, session: Session = Depends(get_session)) -> dict:
    try:
        return update_agent_config(session, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/agent/run", response_model=AgentRunResponse)
def run_agent(payload: AgentRunRequest, session: Session = Depends(get_session)) -> dict:
    return run_delegate_agent(
        session,
        payload.prompt,
        payload.cwd,
        get_tool_config(session),
        permission_override="readonly",
    )
