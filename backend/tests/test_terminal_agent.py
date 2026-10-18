from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import agent_tasks as agent_tasks_router
from app.services import agent_live
from app.services.tools import terminal_agent

FAKE = Path(__file__).parent / "fixtures" / "fake_ohdsh.py"


class TerminalAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.cwd = tempfile.mkdtemp()
        self.env = {**os.environ, "FAKE_OHDSH_ROOT": str(self.root)}

    def run_task(self, task_id: int, prompt: str, **kwargs) -> dict:
        return terminal_agent.run_terminal_task(
            [sys.executable, str(FAKE)], prompt, self.cwd, task_id=task_id, timeout_seconds=20,
            sessions_root=self.root, env=kwargs.pop("env", self.env), ready_quiet=0.4, **kwargs,
        )

    def test_types_the_task_and_reads_the_last_reply_of_that_turn(self) -> None:
        result = self.run_task(201, "看看这个项目\n有哪些文件")
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(result["output"], "第 1 轮：收到「看看这个项目 有哪些文件」，已完成。")  # 多行压成一行
        self.assertTrue(result["agent_session_id"])
        self.assertIsNone(terminal_agent.terminal_for(201))  # 结束后终端关掉

    def test_resume_continues_the_same_session(self) -> None:
        first = self.run_task(202, "第一件事")
        second = self.run_task(203, "接着做", resume_session_id=first["agent_session_id"])
        self.assertEqual(second["agent_session_id"], first["agent_session_id"])
        self.assertEqual(second["output"], "第 2 轮：收到「接着做」，已完成。")

    def test_agent_that_exits_at_startup_fails_clearly(self) -> None:
        result = self.run_task(204, "x", env={**self.env, "FAKE_OHDSH_MODE": "exit"})
        self.assertFalse(result["ok"])
        self.assertIn("退出", result["message"])

    def test_terminal_panel_gets_the_output_so_far(self) -> None:
        app = FastAPI()
        app.include_router(agent_tasks_router.router)
        client = TestClient(app)
        holder: dict = {}
        worker = threading.Thread(target=lambda: holder.setdefault("result", self.run_task(205, "她的任务")), daemon=True)
        worker.start()
        for _ in range(100):
            if terminal_agent.terminal_for(205):
                break
            time.sleep(0.05)
        with client.websocket_connect("/api/agent-tasks/205/terminal") as ws:
            seen = b""
            while "探索未至之境".encode() not in seen:
                seen += ws.receive_bytes()
        worker.join(15)
        self.assertTrue(holder["result"]["ok"])
        self.assertIn("她的任务", holder["result"]["output"])

    def test_unknown_task_terminal_is_closed(self) -> None:
        app = FastAPI()
        app.include_router(agent_tasks_router.router)
        with TestClient(app).websocket_connect("/api/agent-tasks/999/terminal") as ws:
            with self.assertRaises(Exception):
                ws.receive_bytes()

    def test_turn_parsing_ignores_earlier_turns(self) -> None:
        events = [
            {"type": "turn/start", "data": {"turn": 1}},
            {"type": "assistant/message", "data": {"turn": 1, "message": {"content": [{"type": "text", "text": "旧"}]}}},
            {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}},
        ]
        self.assertEqual(terminal_agent.latest_turn(events), 1)
        self.assertEqual(terminal_agent.finished_turn(events, 1), (False, "", ""))
        self.assertEqual(terminal_agent.finished_turn(events, 0), (True, "completed", "旧"))


if __name__ == "__main__":
    unittest.main()
