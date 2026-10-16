"""只读列出用户在 Codex App / CLI 里开过的线程，供任务卡选择“接着哪个线程做”。

数据来自 Codex 自己的 ~/.codex/state_5.sqlite，只读打开、从不写入；表结构变了就当作没有线程。
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

RECENTLY_ACTIVE_SECONDS = 120  # 这么久以内更新过的线程，可能还在 App 里跑


def codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser()


def _title(name: str | None, title: str | None) -> str:
    text = (name or "").strip()
    if not text:
        lines = (title or "").strip().splitlines()
        text = lines[0].strip() if lines else ""
    return (text or "未命名线程")[:80]


def list_codex_threads(query: str = "", limit: int = 30) -> list[dict[str, Any]]:
    """用户自己开的、没归档的线程，最近更新的在前；子代理、审查之类的内部线程不算。"""
    path = codex_home() / "state_5.sqlite"
    if not path.exists():
        return []
    query = (query or "").strip()[:200]
    limit = max(1, min(int(limit), 200))
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    try:
        rows = db.execute(
            "SELECT id, name, title, cwd, updated_at_ms FROM threads "
            "WHERE archived = 0 AND source IN ('vscode', 'cli', 'exec') "
            "AND (thread_source = 'user' OR thread_source IS NULL) "
            "AND (agent_path IS NULL OR agent_path = '') AND (agent_nickname IS NULL OR agent_nickname = '') "
            "AND (? = '' OR id = ? OR instr(lower(coalesce(name, '')), lower(?)) > 0 "
            "OR instr(lower(coalesce(title, '')), lower(?)) > 0 OR instr(lower(cwd), lower(?)) > 0) "
            "ORDER BY updated_at_ms DESC, id LIMIT ?",
            (query, query, query, query, query, limit),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    now_ms = time.time() * 1000
    return [
        {
            "id": row[0],
            "title": _title(row[1], row[2]),
            "cwd": row[3] or "",
            "updated_at_ms": int(row[4] or 0),
            "recently_active": bool(row[4]) and now_ms - float(row[4]) < RECENTLY_ACTIVE_SECONDS * 1000,
        }
        for row in rows
    ]


def get_codex_thread(thread_id: str) -> dict[str, Any] | None:
    for thread in list_codex_threads(thread_id, limit=5):
        if thread["id"] == thread_id:
            return thread
    return None


def match_codex_thread(hint: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """按线程 id 或标题、目录里的关键词找线程；正好一个时返回它，否则返回候选让用户选。"""
    hint = (hint or "").strip()
    if not hint:
        return None, []
    exact = get_codex_thread(hint)
    if exact:
        return exact, [exact]
    candidates = list_codex_threads(hint, limit=6)
    titled = [thread for thread in candidates if thread["title"].lower() == hint.lower()]
    if len(titled) == 1:
        return titled[0], candidates
    return (candidates[0] if len(candidates) == 1 else None), candidates
