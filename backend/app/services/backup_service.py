"""每日自动备份：记忆、关系与时间线都只在这一个 SQLite 文件里，迁移前备份不够。"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from ..database import BACKUP_DIR, backup_database, prune_backups

logger = logging.getLogger(__name__)

DAILY_DIR = BACKUP_DIR / "daily"
KEEP_DAILY = 30
CHECK_SECONDS = 3600


def run_daily_backup(
    now: datetime | None = None, *, directory: Path = DAILY_DIR, source: Path | None = None
) -> Path | None:
    """今天还没有备份就备份一次，并检查副本完整；保留最近 30 份。返回新备份路径。"""
    now = now or datetime.now()
    target = directory / f"sandrone_agent_{now:%Y%m%d}.db"
    if target.exists():
        return None
    made = backup_database(target, source)
    if made is None:
        return None
    check = sqlite3.connect(made)
    try:
        result = check.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        made.unlink(missing_ok=True)
        raise RuntimeError(f"备份副本校验失败：{result}")
    prune_backups(directory, KEEP_DAILY)
    logger.info("每日备份完成：%s", made.name)
    return made


class DailyBackup:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(run_daily_backup)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("每日备份失败")
            await asyncio.sleep(CHECK_SECONDS)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None


DAILY_BACKUP = DailyBackup()
