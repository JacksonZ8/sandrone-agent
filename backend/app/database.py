from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import DATA_DIR, DATABASE_URL

connect_args = (
    {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
)
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)
BACKUP_DIR = DATA_DIR / "backups"


def sqlite_file() -> Path | None:
    """当前数据库文件路径；不是 SQLite 文件库（如内存库）时返回 None。"""
    if not DATABASE_URL.startswith("sqlite"):
        return None
    name = engine.url.database
    if not name or name == ":memory:":
        return None
    return Path(name)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    # WAL：读写互不阻塞，后台的记忆提取、看屏、主动消息与通话同时写库时不再互相等锁
    if sqlite_file() is None:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def backup_database(target: Path, source: Path | None = None) -> Path | None:
    """用 SQLite 在线备份接口复制数据库。WAL 模式下直接拷文件会漏掉 -wal 里还没写回的内容。"""
    source = source or sqlite_file()
    if source is None or not source.exists() or source.stat().st_size == 0:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    src = sqlite3.connect(source, timeout=30)
    try:
        dst = sqlite3.connect(partial)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    partial.replace(target)
    return target


def prune_backups(directory: Path, keep: int) -> None:
    for old in sorted(directory.glob("sandrone_agent_*.db"))[:-keep]:
        old.unlink(missing_ok=True)


def _backup_sqlite_database() -> None:
    """迁移前备份旧数据库，避免结构升级时丢失用户数据；只保留最近 20 份。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if backup_database(BACKUP_DIR / f"sandrone_agent_{stamp}.db"):
        prune_backups(BACKUP_DIR, 20)


def _migrate_schema() -> None:
    """SQLite 的轻量迁移：为新版本新增列，保留用户已有数据。"""
    migrations = {
        "llm_config": [
            ("reply_thinking", "TEXT NOT NULL DEFAULT 'auto'"),
        ],
        "messages": [
            ("valence", "REAL NOT NULL DEFAULT 0"),
            ("inner_state", "TEXT NOT NULL DEFAULT '{}'"),
            ("channel", "TEXT NOT NULL DEFAULT 'web'"),
            ("sender_id", "TEXT NOT NULL DEFAULT 'owner'"),
            ("chat_id", "TEXT NOT NULL DEFAULT 'web'"),
            ("tool_calls_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("memory_traces_json", "TEXT NOT NULL DEFAULT '[]'"),
        ],
        "semantic_memories": [
            ("perspective", "TEXT NOT NULL DEFAULT 'user'"),
            ("valence", "REAL NOT NULL DEFAULT 0"),
            ("scope", "TEXT NOT NULL DEFAULT ''"),
        ],
        "game_profiles": [
            ("knowledge_file", "TEXT NOT NULL DEFAULT ''"),
            ("auto_detected", "BOOLEAN NOT NULL DEFAULT 0"),
        ],
        "screen_companion_config": [
            ("watch_until", "DATETIME"),
            ("watch_apps", "TEXT NOT NULL DEFAULT '[]'"),
        ],
        "screen_companion_config": [
            ("hidden_apps", "TEXT NOT NULL DEFAULT ''"),
        ],
        "screen_observations": [
            ("game_id", "INTEGER"),
            ("session_id", "INTEGER"),
            ("scene", "TEXT NOT NULL DEFAULT ''"),
            ("event", "TEXT NOT NULL DEFAULT ''"),
            ("progress", "TEXT NOT NULL DEFAULT ''"),
        ],
        "relationship_state": [
            ("affection", "REAL NOT NULL DEFAULT 0.5"),
        ],
        "agent_state": [
            ("last_forgetting_at", "DATETIME"),
            ("last_consolidation_at", "DATETIME"),
        ],
        "tool_config": [
            ("vision_base_url", "TEXT NOT NULL DEFAULT ''"),
            ("vision_model", "TEXT NOT NULL DEFAULT ''"),
            ("vision_api_key", "TEXT NOT NULL DEFAULT ''"),
            ("agent_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("agent_command", "TEXT NOT NULL DEFAULT 'claude'"),
            ("agent_permission", "TEXT NOT NULL DEFAULT 'readonly'"),
            ("agent_allowlist", "TEXT NOT NULL DEFAULT '[]'"),
            ("agent_timeout_seconds", "INTEGER NOT NULL DEFAULT 600"),
            ("agent_max_output_chars", "INTEGER NOT NULL DEFAULT 12000"),
            ("agent_max_budget_usd", "REAL NOT NULL DEFAULT 5.0"),
        ],
    }

    needs_migration = False
    table_columns: dict[str, set[str]] = {}
    with engine.connect() as connection:
        for table, columns in migrations.items():
            try:
                existing = {
                    row[1]
                    for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
                }
            except Exception:
                existing = set()
            table_columns[table] = existing
            if any(name not in existing for name, _ in columns):
                needs_migration = True

    if not needs_migration:
        return

    _backup_sqlite_database()

    with engine.begin() as connection:
        for table, columns in migrations.items():
            existing = table_columns[table]
            for name, ddl in columns:
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_semantic_memories_perspective "
            "ON semantic_memories (perspective)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_semantic_memories_scope ON semantic_memories (scope)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_messages_channel ON messages (channel)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_messages_sender_id ON messages (sender_id)"
        )


def _encrypt_plaintext_secrets() -> None:
    """旧版本把 API Key 明文存在库里：改写成密文（改写前先备份，备份里仍是旧明文）。"""
    from .secret_store import encrypt_existing_secrets, plaintext_secret_count

    with engine.connect() as connection:
        if not plaintext_secret_count(connection):
            return
    _backup_sqlite_database()
    with engine.begin() as connection:
        changed = encrypt_existing_secrets(connection)
    logging.getLogger(__name__).info("已把 %s 项明文密钥改为加密存放", changed)


def init_db() -> None:
    from . import models  # noqa: F401
    from .services.provider_service import (
        seed_agent_state,
        seed_default_config,
        seed_default_state,
    )

    SQLModel.metadata.create_all(engine)
    if DATABASE_URL.startswith("sqlite"):
        _migrate_schema()
        _encrypt_plaintext_secrets()

    with Session(engine) as session:
        seed_default_config(session)
        seed_default_state(session)
        seed_agent_state(session)


def get_session():
    with Session(engine) as session:
        yield session
