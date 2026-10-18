from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AgentTask, ToolConfig
from app.services import agent_live, agent_tasks
from app.services.tools import acp, delegation

FAKE = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


class FakeAgent:
    """用测试用的假 ACP 智能体跑一轮，记录它收到的消息。"""

    def __init__(self, mode: str = "stream", kind: str = "execute") -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.log = self.dir / "log.jsonl"
        self.env = {**os.environ, "FAKE_ACP_MODE": mode, "FAKE_ACP_KIND": kind, "FAKE_ACP_LOG": str(self.log)}

    def run(self, task_id: int, *, readonly: bool = False, resume: str = "", mode: str = "") -> dict:
        return acp.run_acp_task(
            [sys.executable, str(FAKE)], "看看这个项目", str(self.dir), task_id=task_id,
            readonly=readonly, resume_session_id=resume, timeout_seconds=20, env=self.env, mode=mode,
        )

    def received(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


def answer_when_asked(task_id: int, option_id: str) -> threading.Thread:
    def worker() -> None:
        for _ in range(200):
            live = agent_live.get(task_id)
            if live and live.pending:
                agent_live.answer_permission(task_id, option_id)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


class AcpClientTests(unittest.TestCase):
    def test_streams_reply_tools_and_plan(self) -> None:
        agent = FakeAgent("stream")
        result = agent.run(101)
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(result["output"], "项目是一个陪伴型 Agent。")
        self.assertEqual(result["agent_session_id"], "s-new")
        live = agent_live.public(101)
        self.assertEqual(live["tools"][0]["title"], "读 README.md")
        self.assertEqual(live["tools"][0]["status"], "completed")
        self.assertEqual(live["plan"][0]["content"], "读代码")
        init = next(m for m in agent.received() if m.get("method") == "initialize")
        self.assertEqual(init["params"]["clientCapabilities"]["terminal"], False)  # 不给智能体我们的终端

    def test_permission_waits_for_the_user(self) -> None:
        agent = FakeAgent("permission", kind="execute")
        answer_when_asked(102, "allow")
        result = agent.run(102, readonly=False)
        self.assertTrue(result["ok"])
        self.assertIn("权限结果：allow", result["output"])

    def test_readonly_rejects_write_kinds_without_asking(self) -> None:
        agent = FakeAgent("permission", kind="edit")
        with patch.object(agent_live, "ask_permission", side_effect=AssertionError("只读不该问用户")):
            result = agent.run(103, readonly=True)
        self.assertIn("权限结果：reject", result["output"])
        self.assertTrue(any(e["kind"] == "denied" for e in agent_live.public(103)["events"]))

    def test_readonly_still_asks_for_harmless_kinds(self) -> None:
        agent = FakeAgent("permission", kind="fetch")
        answer_when_asked(104, "reject")
        result = agent.run(104, readonly=True)
        self.assertIn("权限结果：reject", result["output"])

    def test_unanswered_permission_is_refused_not_allowed(self) -> None:
        agent = FakeAgent("permission", kind="execute")
        with patch.object(agent_live, "PERMISSION_TIMEOUT_SECONDS", 0.3):
            result = agent.run(105)
        self.assertIn("权限结果：cancelled", result["output"])  # 超时按取消处理，从不替用户允许

    def test_resume_loads_the_old_session_without_replaying_it(self) -> None:
        agent = FakeAgent("stream")
        result = agent.run(106, resume="s-old")
        self.assertEqual(result["agent_session_id"], "s-old")
        self.assertNotIn("旧会话的回放", result["output"])
        methods = [m.get("method") for m in agent.received()]
        self.assertIn("session/load", methods)
        self.assertNotIn("session/new", methods)

    def test_switches_to_the_requested_mode(self) -> None:
        agent = FakeAgent("stream")
        self.assertTrue(agent.run(108, mode="plan")["ok"])
        change = next(m for m in agent.received() if m.get("method") == "session/set_config_option")
        self.assertEqual(change["params"], {"sessionId": "s-new", "configId": "mode", "value": "plan"})

    def test_unknown_mode_stops_before_prompting(self) -> None:
        agent = FakeAgent("stream")
        result = agent.run(109, mode="read-only")
        self.assertFalse(result["ok"])
        self.assertIn("read-only", result["message"])
        self.assertNotIn("session/prompt", [m.get("method") for m in agent.received()])  # 不带着默认权限继续跑

    def test_already_in_mode_needs_no_change(self) -> None:
        agent = FakeAgent("stream")
        self.assertTrue(agent.run(110, mode="default")["ok"])
        self.assertNotIn("session/set_config_option", [m.get("method") for m in agent.received()])

    def test_crash_reports_stderr(self) -> None:
        result = FakeAgent("crash").run(107)
        self.assertFalse(result["ok"])
        self.assertIn("model not configured", result["message"])


class HermesTaskFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.project = tempfile.mkdtemp()
        self.script = Path(tempfile.mkdtemp()) / "hermes-acp"
        self.script.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE}\n", encoding="utf-8")
        self.script.chmod(0o755)
        with Session(self.engine) as session:
            session.add(ToolConfig(id=1, agent_enabled=True, agent_command="claude", agent_permission="readonly",
                                   agent_allowlist=json.dumps([self.project])))
            session.commit()
        self.patches = [
            patch.object(agent_tasks, "engine", self.engine),
            patch.object(delegation, "agent_executable",
                         side_effect=lambda command: str(self.script) if command in ("hermes", "claude") else None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.engine.dispose()

    def test_card_names_hermes_and_its_readonly_limits(self) -> None:
        with Session(self.engine) as session:
            result = agent_tasks.propose_task(session, {"prompt": "看看项目", "title": "看项目", "spoken_summary": "让 Hermes 看看项目", "agent": "hermes"}, channel="web")
            self.assertTrue(result["ok"], result)
            task = session.get(AgentTask, result["task_id"])
            card = agent_tasks.task_public(task)
        self.assertEqual((card["command"], card["agent_label"], card["live"]), ("hermes", "Hermes", True))
        self.assertTrue(any("只读模式：Hermes" in risk for risk in card["risks"]))

    def test_unknown_agent_is_refused(self) -> None:
        with Session(self.engine) as session:
            result = agent_tasks.propose_task(session, {"prompt": "x", "title": "x", "spoken_summary": "x", "agent": "cursor"}, channel="web")
        self.assertFalse(result["ok"])

    def test_confirmed_hermes_task_runs_and_keeps_its_session(self) -> None:
        with Session(self.engine) as session:
            task_id = agent_tasks.propose_task(session, {"prompt": "看看项目", "title": "看项目", "spoken_summary": "s", "agent": "hermes"}, channel="web")["task_id"]
            session.get(AgentTask, task_id).status = "running"
            session.commit()
        agent_tasks._execute_task(task_id)
        with Session(self.engine) as session:
            task = session.get(AgentTask, task_id)
            self.assertEqual(task.status, "completed", task.error)
            self.assertEqual(task.output, "项目是一个陪伴型 Agent。")
            self.assertEqual(task.agent_session_id, "s-new")
            # 没有可用的模型时，完成通知退回固定说法
            notice = asyncio.run(agent_tasks.summarize_result(session, task))
        self.assertIn("做完了", notice)
        self.assertIsNotNone(agent_tasks.live_view(task_id))

    def test_permission_answer_goes_through_the_api_helper(self) -> None:
        with Session(self.engine) as session:
            ok, message = agent_tasks.answer_permission(session, 999, "allow")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
