from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AgentTask, Message, utcnow
from app.services import agent_tasks
from app.services.tools import delegation
from app.services.tools.codex_threads import list_codex_threads, match_codex_thread
from app.services.tools.config import get_tool_config

CODEX_OK = (
    '{"type":"thread.started","thread_id":"01a1-thread"}\n'
    '{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"pong"}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":1}}\n'
)
CODEX_FAILED = (
    '{"type":"thread.started","thread_id":"01a1-thread"}\n'
    '{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Model metadata not found."}}\n'
    '{"type":"turn.failed","error":{"message":"{\\"type\\":\\"error\\",\\"status\\":400,'
    '\\"error\\":{\\"type\\":\\"invalid_request_error\\",\\"message\\":\\"The model is not supported.\\"}}"}}\n'
)


class CliOutputTests(unittest.TestCase):
    def test_codex_success_and_failure(self) -> None:
        self.assertEqual(delegation.parse_codex_output(CODEX_OK), ("pong", "01a1-thread", ""))
        # -o 文件里的最终回复优先
        self.assertEqual(delegation.parse_codex_output(CODEX_OK, "pong!\n")[0], "pong!")
        reply, thread_id, error = delegation.parse_codex_output(CODEX_FAILED)
        self.assertEqual((reply, thread_id), ("", "01a1-thread"))
        self.assertEqual(error, "The model is not supported.")

    def test_claude_json(self) -> None:
        ok = json.dumps({"type": "result", "is_error": False, "result": "pong", "session_id": "s-1"})
        self.assertEqual(delegation.parse_claude_output(ok), ("pong", "s-1", ""))
        failed = json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "session_id": "s-1"})
        self.assertEqual(delegation.parse_claude_output(failed), ("", "s-1", "error_max_budget_usd"))
        self.assertEqual(delegation.parse_claude_output("plain text"), ("plain text", "", ""))

    def test_args_keep_sessions_and_resume(self) -> None:
        build = delegation._build_agent_args
        codex = build("codex", "/bin/codex", "do it", "/p", "readonly", "5.00", output_file="/tmp/o")
        self.assertEqual(codex[:3], ["/bin/codex", "exec", "--json"])
        self.assertNotIn("--ephemeral", codex)
        self.assertIn("read-only", codex)
        self.assertEqual(codex[-3:], ["-o", "/tmp/o", "do it"])
        resumed = build("codex", "/bin/codex", "go on", "/p", "write", "5.00", resume_session_id="t-1")
        self.assertEqual(resumed[:4], ["/bin/codex", "exec", "resume", "t-1"])
        self.assertIn('sandbox_mode="workspace-write"', resumed)
        self.assertNotIn("--cd", resumed)
        claude = build("claude", "/bin/claude", "do it", "/p", "readonly", "5.00", new_session_id="s-1")
        self.assertNotIn("--no-session-persistence", claude)
        self.assertEqual(claude[claude.index("--session-id") + 1], "s-1")
        claude_resume = build("claude", "/bin/claude", "go on", "/p", "readonly", "5.00", resume_session_id="s-1")
        self.assertEqual(claude_resume[claude_resume.index("--resume") + 1], "s-1")
        self.assertNotIn("--session-id", claude_resume)

    def test_execute_agent_resumes_and_reports_session(self) -> None:
        config = type("C", (), {"agent_max_budget_usd": 5.0, "agent_timeout_seconds": 60, "agent_max_output_chars": 100})()
        target = {"command": "codex", "exe": "/bin/codex", "cwd": "/tmp", "permission": "readonly"}
        with patch.object(delegation, "_run_process", return_value=(0, CODEX_OK, "")) as run:
            result = delegation.execute_agent(target, "go on", config, resume_session_id="01a1-thread")
        self.assertEqual(run.call_args.args[0][:4], ["/bin/codex", "exec", "resume", "01a1-thread"])
        self.assertTrue(result["ok"])
        self.assertEqual((result["output"], result["agent_session_id"]), ("pong", "01a1-thread"))
        with patch.object(delegation, "_run_process", return_value=(1, CODEX_FAILED, "")):
            failed = delegation.execute_agent(target, "go", config)
        self.assertFalse(failed["ok"])
        self.assertIn("The model is not supported.", failed["output"])


class AgentTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        with Session(self.engine) as session:
            config = get_tool_config(session)
            config.agent_enabled = True
            config.agent_command = "claude"
            config.agent_allowlist = json.dumps([str(self.root)])
            session.add(config)
            session.commit()
        self.patches = [
            patch.object(agent_tasks, "engine", self.engine),
            # 绝不拉起本机 CLI
            patch.object(delegation, "agent_executable", side_effect=lambda command: f"/usr/local/bin/{command}"),
            patch.object(delegation, "_run_process", side_effect=AssertionError("tests must not spawn CLIs")),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()
        self.engine.dispose()

    def _propose(self, session: Session, **extra) -> dict:
        arguments = {"prompt": "整理 README 的安装步骤", "title": "整理 README", "spoken_summary": "让 Claude 整理 README 的安装步骤", **extra}
        return agent_tasks.propose_task(session, arguments, channel="web")

    def _user_message(self, session: Session, text: str) -> Message:
        message = Message(role="user", content=text, turn_index=1)
        session.add(message)
        session.commit()
        session.refresh(message)
        return message

    def test_propose_makes_a_card_and_supersedes_the_old_one(self) -> None:
        with Session(self.engine) as session:
            first = self._propose(session)
            self.assertTrue(first["ok"])
            self.assertEqual(first["status"], "awaiting_confirmation")
            self.assertEqual(first["card"]["cwd"], str(self.root))
            second = self._propose(session, title="另一个")
            old = session.get(AgentTask, first["task_id"])
            self.assertEqual(old.status, "cancelled")
            self.assertEqual(session.get(AgentTask, second["task_id"]).status, "proposed")

    def test_only_the_first_confirmation_starts_the_task(self) -> None:
        with Session(self.engine) as session:
            task_id = self._propose(session)["task_id"]
            with patch.object(agent_tasks, "_spawn") as spawn:
                self.assertTrue(agent_tasks.confirm_task(session, task_id)[0])
                ok, message = agent_tasks.confirm_task(session, task_id)
            self.assertFalse(ok)
            self.assertIn("执行中", message)
            spawn.assert_called_once_with(task_id)

    def test_expired_card_cannot_be_confirmed(self) -> None:
        with Session(self.engine) as session:
            task = session.get(AgentTask, self._propose(session)["task_id"])
            task.created_at = utcnow() - timedelta(minutes=agent_tasks.CARD_EXPIRE_MINUTES + 1)
            session.add(task)
            session.commit()
            with patch.object(agent_tasks, "_spawn") as spawn:
                ok, _ = agent_tasks.confirm_task(session, task.id)
            self.assertFalse(ok)
            spawn.assert_not_called()
            session.refresh(task)
            self.assertEqual(task.status, "expired")

    def test_background_run_records_result_and_session(self) -> None:
        with Session(self.engine) as session:
            task_id = self._propose(session)["task_id"]
            with patch.object(agent_tasks, "_spawn"):
                agent_tasks.confirm_task(session, task_id)
        result = {"ok": True, "output": "改好了", "duration_ms": 1200, "agent_session_id": "s-1", "message": "子代理任务完成。"}
        with patch.object(agent_tasks, "run_delegate_agent", return_value=result) as run:
            agent_tasks._execute_task(task_id)
        self.assertEqual(run.call_args.kwargs["resume_session_id"], "")
        with Session(self.engine) as session:
            task = session.get(AgentTask, task_id)
            self.assertEqual((task.status, task.output, task.agent_session_id), ("completed", "改好了", "s-1"))

            # 续接：同一个会话、同一个目录与执行者
            follow = agent_tasks.propose_task(session, {"prompt": "再补上卸载步骤", "title": "补卸载", "spoken_summary": "接着补卸载步骤", "continue_task_id": task_id}, channel="web")
            child = session.get(AgentTask, follow["task_id"])
            self.assertEqual((child.parent_id, child.agent_session_id, child.command, child.cwd), (task_id, "s-1", "claude", str(self.root)))

    def test_stopped_task_stays_cancelled(self) -> None:
        with Session(self.engine) as session:
            task_id = self._propose(session)["task_id"]
            with patch.object(agent_tasks, "_spawn"):
                agent_tasks.confirm_task(session, task_id)
            self.assertTrue(agent_tasks.cancel_task(session, task_id)[0])
        with patch.object(agent_tasks, "run_delegate_agent") as run:
            agent_tasks._execute_task(task_id)
        run.assert_not_called()
        with Session(self.engine) as session:
            self.assertEqual(session.get(AgentTask, task_id).status, "cancelled")

    def test_continue_requires_a_session(self) -> None:
        with Session(self.engine) as session:
            task = AgentTask(title="旧", prompt="旧", command="claude", cwd=str(self.root), status="failed")
            session.add(task)
            session.commit()
            result = agent_tasks.propose_task(session, {"prompt": "接着", "title": "接着", "spoken_summary": "接着", "continue_task_id": task.id}, channel="web")
            self.assertFalse(result["ok"])
            self.assertIn("续接", result["error"])

    def test_verbal_confirmation_right_after_the_card(self) -> None:
        with Session(self.engine) as session:
            task_id = self._propose(session)["task_id"]
            current = self._user_message(session, "嗯，确认。")
            with patch.object(agent_tasks, "_spawn") as spawn:
                context, card = agent_tasks.resolve_card_by_utterance(session, current.content, channel="web", exclude_message_id=current.id)
            spawn.assert_called_once_with(task_id)
            self.assertEqual(card["status"], "running")
            self.assertIn("口头确认", context)

    def test_verbal_reply_after_other_chat_is_ignored(self) -> None:
        with Session(self.engine) as session:
            self._propose(session)
            self._user_message(session, "对了，晚饭吃什么")
            with patch.object(agent_tasks, "_spawn") as spawn:
                context, card = agent_tasks.resolve_card_by_utterance(session, "好", channel="voice")
            spawn.assert_not_called()
            self.assertEqual((context, card), ("", None))
            # 不是整句确认也不算
            self.assertEqual(agent_tasks.resolve_card_by_utterance(session, "好的，不过先等等", channel="voice"), ("", None))

    def test_verbal_cancel_and_wechat_write_guard(self) -> None:
        with Session(self.engine) as session:
            task_id = self._propose(session)["task_id"]
            context, card = agent_tasks.resolve_card_by_utterance(session, "算了吧", channel="voice")
            self.assertEqual(card["status"], "cancelled")

            config = get_tool_config(session)
            config.agent_permission = "write"
            session.add(config)
            session.commit()
            write_id = self._propose(session)["task_id"]
            with patch.object(agent_tasks, "_spawn") as spawn:
                context, card = agent_tasks.resolve_card_by_utterance(session, "确认", channel="wechat_dm")
            spawn.assert_not_called()
            self.assertEqual(session.get(AgentTask, write_id).status, "proposed")
            self.assertIn("网页", context)
            self.assertNotEqual(task_id, write_id)

    def test_restart_marks_running_tasks_interrupted(self) -> None:
        with Session(self.engine) as session:
            session.add(AgentTask(title="跑着", prompt="x", command="codex", cwd=str(self.root), status="running"))
            session.commit()
        self.assertEqual(agent_tasks.recover_interrupted_tasks(), 1)
        with Session(self.engine) as session:
            task = session.exec(select(AgentTask)).one()
            self.assertEqual(task.status, "interrupted")
            self.assertIn("不会自动重跑", task.error)

    def test_prompt_block_and_completion_notice(self) -> None:
        with Session(self.engine) as session:
            self.assertEqual(agent_tasks.agent_tasks_block(session), "")
            task = AgentTask(title="查依赖", prompt="x", command="codex", cwd=str(self.root), status="completed", output="三个依赖过期")
            session.add(task)
            session.commit()
            block = agent_tasks.agent_tasks_block(session)
            self.assertIn(f"#{task.id}「查依赖」Codex：已完成", block)
            self.assertIn("三个依赖过期", block)

            async def no_push(*args, **kwargs) -> int:
                return 0

            async def heard(*args, **kwargs) -> int:
                return 1

            with (
                patch("app.services.push_service.notify", side_effect=no_push) as push,
                patch("app.services.voice.call.announce_to_calls", side_effect=heard) as announce,
            ):
                asyncio.run(agent_tasks._notify(task.id))
            message = session.exec(select(Message)).one()
            self.assertEqual(message.role, "assistant")
            self.assertIn("「查依赖」那边做完了", message.content)
            self.assertEqual(json.loads(message.inner_state)["agent_task_id"], task.id)
            push.assert_called_once()
            # 正在通话时念口语版，并把这条时间线消息同步给通话界面
            spoken, synced = announce.call_args.args
            self.assertEqual(spoken, "对了，「查依赖」那边做完了，要听结果吗？")
            self.assertEqual(synced["id"], message.id)


class CodexThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.project = home / "project"
        self.project.mkdir()
        db = sqlite3.connect(home / "state_5.sqlite")
        db.execute(
            "CREATE TABLE threads (id TEXT, name TEXT, title TEXT, cwd TEXT, updated_at_ms INTEGER, archived INTEGER, "
            "source TEXT, thread_source TEXT, agent_path TEXT, agent_nickname TEXT)"
        )
        now = int(time.time() * 1000)
        rows = [
            ("t-app", "桌宠动画", "first message", str(self.project), now - 10_000, 0, "vscode", "user", None, None),
            ("t-old", None, "整理论文笔记\n更多", str(self.project), now - 3_600_000, 0, "vscode", "user", None, None),
            ("t-sub", None, "子代理", str(self.project), now, 0, '{"subagent":{}}', "subagent", "/root/x", "Kant"),
            ("t-arch", "桌宠旧线程", "x", str(self.project), now, 1, "vscode", "user", None, None),
        ]
        db.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        db.close()
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(home)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_lists_only_user_threads(self) -> None:
        threads = list_codex_threads()
        self.assertEqual([t["id"] for t in threads], ["t-app", "t-old"])
        self.assertTrue(threads[0]["recently_active"])
        self.assertEqual(threads[1]["title"], "整理论文笔记")

    def test_match_by_title(self) -> None:
        self.assertEqual(match_codex_thread("桌宠")[0]["id"], "t-app")
        self.assertIsNone(match_codex_thread("不存在")[0])

    def test_card_can_target_an_app_thread_with_risks(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        with (
            patch.object(delegation, "agent_executable", side_effect=lambda command: f"/usr/local/bin/{command}"),
            Session(engine) as session,
        ):
            config = get_tool_config(session)
            config.agent_enabled = True
            config.agent_allowlist = json.dumps([str(self.project.resolve())])
            session.add(config)
            session.commit()
            result = agent_tasks.propose_task(
                session, {"prompt": "继续调动画", "title": "调动画", "spoken_summary": "接着桌宠动画线程调", "codex_thread": "桌宠"}, channel="web"
            )
            self.assertTrue(result["ok"], result)
            task = session.get(AgentTask, result["task_id"])
            self.assertEqual((task.command, task.agent_session_id, task.target), ("codex", "t-app", "codex_app"))
            risks = " ".join(agent_tasks.task_public(task)["risks"])
            self.assertIn("「桌宠动画」", risks)
            self.assertIn("两分钟内刚在 App 里更新过", risks)

            ok, _ = agent_tasks.set_task_thread(session, task.id, "")
            self.assertTrue(ok)
            session.refresh(task)
            self.assertEqual((task.target, task.agent_session_id), ("", ""))
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
