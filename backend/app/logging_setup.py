"""后端日志写进 data/logs/backend.log（按大小滚动），后台任务的异常也能事后查到。"""

from __future__ import annotations

import asyncio
import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "backend.log"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# 这些库每个请求都打 INFO（含完整 URL），只留警告
QUIET_LOGGERS = ("httpx", "httpcore", "urllib3", "jieba")


def setup_logging(path: Path = LOG_FILE) -> None:
    root = logging.getLogger()
    if any(getattr(handler, "sandrone_file", False) for handler in root.handlers):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.sandrone_file = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # uvicorn 的日志不向上传给 root，单独接上（不含逐条访问日志）
    logging.getLogger("uvicorn").addHandler(handler)

    previous = threading.excepthook

    def log_thread_exception(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("thread").error(
            "后台线程 %s 异常退出",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        previous(args)

    threading.excepthook = log_thread_exception


def log_asyncio_exceptions(loop: asyncio.AbstractEventLoop) -> None:
    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exception = context.get("exception")
        logging.getLogger("asyncio").error(
            "异步任务异常：%s",
            context.get("message", ""),
            exc_info=(type(exception), exception, exception.__traceback__) if exception else None,
        )
        loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
