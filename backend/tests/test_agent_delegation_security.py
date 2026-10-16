from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import tools as tools_router
from app.schemas import AgentRunRequest
from app.services import tool_service
from app.services.tools import delegation


class AgentDelegationSecurityTests(unittest.TestCase):
    def _config(self, command: str, root: Path) -> SimpleNamespace:
        return SimpleNamespace(
            agent_enabled=True,
            agent_command=command,
            agent_permission="write",
            agent_allowlist=json.dumps([str(root)]),
            agent_timeout_seconds=60,
            agent_max_output_chars=12000,
            agent_max_budget_usd=5.0,
        )

    def test_manual_endpoint_forces_readonly(self) -> None:
        payload = AgentRunRequest(prompt="inspect", cwd="")
        config = object()
        session = object()
        expected = {
            "ok": True,
            "command": "codex",
            "cwd": "/tmp/project",
            "output": "done",
            "duration_ms": 1,
            "message": "子代理任务完成。",
        }

        with (
            patch.object(tools_router, "get_tool_config", return_value=config),
            patch.object(tools_router, "run_delegate_agent", return_value=expected) as run,
        ):
            result = tools_router.run_agent(payload, session=session)

        self.assertEqual(result, expected)
        run.assert_called_once_with(
            session,
            "inspect",
            "",
            config,
            permission_override="readonly",
        )

    def test_permission_override_builds_readonly_cli_args(self) -> None:
        for command, expected_flag in (("claude", "plan"), ("codex", "read-only")):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                config = self._config(command, Path(tmp))
                stdout = '{"type": "result", "result": "done", "session_id": "s1"}' if command == "claude" else (
                    '{"type": "thread.started", "thread_id": "t1"}\n'
                    '{"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}'
                )
                # 打桩到进程层：测试绝不能真的拉起本机的 claude / codex
                with (
                    patch.object(delegation, "agent_executable", return_value=f"/usr/local/bin/{command}"),
                    patch.object(delegation, "_run_process", return_value=(0, stdout, "")) as run,
                    patch.object(delegation, "_record_tool_call") as record,
                ):
                    result = tool_service.run_delegate_agent(
                        object(),
                        "inspect",
                        tmp,
                        config,
                        permission_override="readonly",
                    )

                self.assertTrue(result["ok"])
                self.assertIn(expected_flag, run.call_args.args[0])
                self.assertEqual(record.call_args.args[3], "readonly")

    def test_default_permission_still_uses_write_mode(self) -> None:
        claude_args = tool_service._build_agent_args(
            "claude", "/usr/local/bin/claude", "edit", "/tmp/project", "write", "5.00"
        )
        codex_args = tool_service._build_agent_args(
            "codex", "/usr/local/bin/codex", "edit", "/tmp/project", "write", "5.00"
        )

        self.assertIn("acceptEdits", claude_args)
        self.assertIn("workspace-write", codex_args)


if __name__ == "__main__":
    unittest.main()
