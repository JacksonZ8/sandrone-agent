from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import database, logging_setup
from app.services import backup_service


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "live.db"
        # 模拟运行中的后端：WAL 模式、连接一直开着，新写入还在 -wal 里没写回主文件
        self.live = sqlite3.connect(self.db)
        self.live.execute("PRAGMA journal_mode=WAL")
        self.live.execute("PRAGMA wal_autocheckpoint=0")
        self.live.execute("CREATE TABLE memories (text TEXT)")
        self.live.execute("INSERT INTO memories VALUES ('他喜欢猫')")
        self.live.commit()

    def tearDown(self) -> None:
        self.live.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rows(self, path: Path) -> list[str]:
        connection = sqlite3.connect(path)
        try:
            return [row[0] for row in connection.execute("SELECT text FROM memories")]
        finally:
            connection.close()

    def test_backup_includes_writes_still_in_the_wal(self) -> None:
        copied = self.dir / "copied.db"
        shutil.copy2(self.db, copied)
        with self.assertRaises(sqlite3.OperationalError):
            self._rows(copied)  # 直接拷文件：表还在 -wal 里，副本里没有
        target = database.backup_database(self.dir / "backup.db", self.db)
        self.assertEqual(self._rows(target), ["他喜欢猫"])

    def test_daily_backup_once_per_day_and_keeps_thirty(self) -> None:
        daily = self.dir / "daily"
        start = datetime(2026, 9, 1, 9)
        first = backup_service.run_daily_backup(start, directory=daily, source=self.db)
        self.assertEqual(self._rows(first), ["他喜欢猫"])
        self.assertIsNone(backup_service.run_daily_backup(start + timedelta(hours=5), directory=daily, source=self.db))
        for day in range(1, 35):
            backup_service.run_daily_backup(start + timedelta(days=day), directory=daily, source=self.db)
        kept = sorted(path.name for path in daily.glob("sandrone_agent_*.db"))
        self.assertEqual(len(kept), backup_service.KEEP_DAILY)
        self.assertEqual(kept[-1], "sandrone_agent_20261005.db")
        self.assertEqual(list(daily.glob("*.partial")), [])

    def test_engine_connections_use_wal(self) -> None:
        connection = sqlite3.connect(self.dir / "fresh.db")
        try:
            with patch.object(database, "sqlite_file", return_value=self.dir / "fresh.db"):
                database._sqlite_pragmas(connection, None)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            connection.close()


class LoggingTests(unittest.TestCase):
    def test_background_thread_errors_reach_the_log_file(self) -> None:
        root = logging.getLogger()
        before_handlers, before_level, before_hook = list(root.handlers), root.level, threading.excepthook
        path = Path(tempfile.mkdtemp()) / "backend.log"
        try:
            logging_setup.setup_logging(path)
            logging_setup.setup_logging(path)  # 重复调用不重复加 handler（--reload 时会再跑 lifespan）
            self.assertEqual(sum(getattr(h, "sandrone_file", False) for h in root.handlers), 1)

            def boom() -> None:
                raise ValueError("记账炸了")

            with patch.object(threading, "__excepthook__", lambda args: None):
                worker = threading.Thread(target=boom, name="bookkeeping")
                with patch("sys.stderr"):
                    worker.start()
                    worker.join()
            logging.getLogger("httpx").info("HTTP Request: GET https://example.com/?token=x")
            for handler in root.handlers:
                handler.flush()
            text = path.read_text(encoding="utf-8")
            self.assertIn("后台线程 bookkeeping 异常退出", text)
            self.assertIn("ValueError: 记账炸了", text)
            self.assertNotIn("HTTP Request", text)
        finally:
            for handler in list(root.handlers):
                if handler not in before_handlers:
                    root.removeHandler(handler)
                    logging.getLogger("uvicorn").removeHandler(handler)
                    handler.close()
            root.setLevel(before_level)
            threading.excepthook = before_hook
            shutil.rmtree(path.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
