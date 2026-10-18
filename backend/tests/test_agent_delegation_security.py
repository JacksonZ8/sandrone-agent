from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.config import DATA_DIR
from app.routers import tools as tools_router
from app.schemas import AgentRunRequest
from app.services import tool_service
from app.services.tools import acp, agent_sandbox, delegation

DONE = {"ok": True, "output": "done", "message": "", "duration_ms": 1, "agent_session_id": "s1"}


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

    def _run(self, command: str, permission: str | None, *, srt: str | None = "/usr/local/bin/srt"):
        """跑一次派活，拦在 ACP 这一层：测试绝不拉起本机的智能体。返回 (结果, run_acp_task 的调用, 当时的沙盒设置)。"""
        seen: dict = {}

        def fake_run(args, *rest, **kwargs):
            if args[0] == srt:
                seen["settings"] = json.loads(Path(args[args.index("-s") + 1]).read_text(encoding="utf-8"))
            return DONE

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(delegation, "agent_executable", return_value=f"/usr/local/bin/{command}-acp"),
                patch.object(agent_sandbox, "srt_executable", return_value=srt),
                patch.object(acp, "run_acp_task", side_effect=fake_run) as run,
                patch.object(delegation, "_record_tool_call") as record,
            ):
                result = tool_service.run_delegate_agent(
                    object(), "inspect", tmp, self._config(command, Path(tmp)), permission_override=permission
                )
            seen["cwd"] = str(Path(tmp).resolve())
        return result, run, record, seen

    def test_manual_endpoint_forces_readonly(self) -> None:
        payload = AgentRunRequest(prompt="inspect", cwd="")
        config = object()
        session = object()
        with (
            patch.object(tools_router, "get_tool_config", return_value=config),
            patch.object(tools_router, "run_delegate_agent", return_value=DONE) as run,
        ):
            result = tools_router.run_agent(payload, session=session)
        self.assertEqual(result, DONE)
        run.assert_called_once_with(session, "inspect", "", config, permission_override="readonly")

    def test_each_agent_runs_in_its_own_readonly_or_write_mode(self) -> None:
        expected = {
            "claude": ("plan", "default"),
            "codex": ("read-only", "workspace-write"),
            "opencode": ("plan", "build"),
        }
        for command, (readonly_mode, write_mode) in expected.items():
            with self.subTest(command=command):
                result, run, record, _ = self._run(command, "readonly")
                self.assertTrue(result["ok"])
                self.assertEqual(run.call_args.kwargs["mode"], readonly_mode)
                self.assertTrue(run.call_args.kwargs["readonly"])
                self.assertEqual(record.call_args.args[3], "readonly")
                _, run, _, _ = self._run(command, None)  # 配置里是写入
                self.assertEqual(run.call_args.kwargs["mode"], write_mode)
                self.assertFalse(run.call_args.kwargs["readonly"])

    def test_claude_and_opencode_run_inside_srt_but_codex_keeps_its_own_sandbox(self) -> None:
        _, run, _, seen = self._run("claude", "readonly")
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["/usr/local/bin/srt", "-s"])
        self.assertEqual(args[3:], ["/usr/local/bin/claude-acp"])
        settings = seen["settings"]
        self.assertIn("~/.ssh", settings["filesystem"]["denyRead"])
        self.assertIn(str(DATA_DIR), settings["filesystem"]["denyRead"])  # 读不到桑多涅自己的数据和 Key
        self.assertIn(seen["cwd"], settings["filesystem"]["allowWrite"])
        self.assertFalse(settings["network"]["allowLocalBinding"])  # 连不到本机的桑多涅后端
        self.assertIn("*.anthropic.com", settings["network"]["allowedDomains"])

        _, run, _, seen = self._run("opencode", "readonly")
        self.assertEqual(run.call_args.args[0][3:], ["/usr/local/bin/opencode-acp", "acp"])
        self.assertTrue(seen["settings"]["network"]["allowLocalBinding"])  # 它的 ACP 模式要在本机起服务

        _, run, _, _ = self._run("codex", "readonly")
        self.assertEqual(run.call_args.args[0], ["/usr/local/bin/codex-acp"])  # Seatbelt 不能嵌套

    def test_missing_sandbox_refuses_instead_of_running_unsandboxed(self) -> None:
        result, run, _, _ = self._run("claude", "write", srt=None)
        self.assertFalse(result["ok"])
        self.assertIn("沙盒", result["message"])
        run.assert_not_called()

    def test_codex_uses_the_chatgpt_binary_and_a_profile_that_denies_secrets(self) -> None:
        with patch.object(delegation.os, "access", return_value=True), patch.dict(delegation.os.environ, {}, clear=False):
            delegation.os.environ.pop("SANDRONE_CODEX_BIN", None)
            readonly = delegation.acp_env("codex", "readonly")
            write = delegation.acp_env("codex", "write")
        self.assertEqual(readonly["CODEX_PATH"], str(delegation.BUNDLED_CODEX))
        self.assertTrue(readonly["PATH"].startswith(agent_sandbox.SEARCH_PATH))
        profile = json.loads(readonly["CODEX_CONFIG"])["permissions"]["sandrone"]
        self.assertEqual(profile["extends"], ":read-only")
        self.assertEqual(profile["filesystem"]["~/.ssh"], "deny")
        self.assertEqual(profile["filesystem"][str(DATA_DIR)], "deny")
        self.assertEqual(json.loads(write["CODEX_CONFIG"])["permissions"]["sandrone"]["extends"], ":workspace")
        self.assertNotIn("CODEX_CONFIG", delegation.acp_env("claude", "readonly"))


if __name__ == "__main__":
    unittest.main()
