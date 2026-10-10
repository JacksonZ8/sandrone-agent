from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from .config import DATA_DIR, DATABASE_URL

connect_args = (
    {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
)
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)


def _backup_sqlite_database() -> None:
    """迁移前备份旧数据库，避免结构升级时丢失用户数据。"""
    if not DATABASE_URL.startswith("sqlite"):
        return
    db_path = Path(engine.url.database)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"sandrone_agent_{stamp}.db"
    shutil.copy2(db_path, target)
    # 只保留最近 20 份备份
    backups = sorted(backup_dir.glob("sandrone_agent_*.db"))
    for old in backups[:-20]:
        old.unlink(missing_ok=True)


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

    with Session(engine) as session:
        seed_default_config(session)
        seed_default_state(session)
        seed_agent_state(session)


def get_session():
    with Session(engine) as session:
        yield session
