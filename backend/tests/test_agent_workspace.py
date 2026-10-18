from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AgentTask
from app.services import agent_tasks
from app.services.tools import acp, agent_sandbox, agent_workspace, delegation


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def make_repo() -> Path:
    repo = Path(tempfile.mkdtemp()).resolve() / "project"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    (repo / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return repo


class WorkspaceTests(unittest.TestCase):
    def test_merge_works_without_any_git_identity(self) -> None:
        # CI 和没配过 git 的电脑上都没有 user.name/email
        no_identity = {"HOME": tempfile.mkdtemp(), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                       # macOS 的 git 会拿主机名猜一个身份，Linux 上不会；让它只认配置，两边一致
                       "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly", "GIT_CONFIG_VALUE_0": "true"}
        with patch.dict(os.environ, no_identity):
            workdir, _ = agent_workspace.prepare(12, str(self.repo))
            (Path(workdir) / "README.md").write_text("by agent\n", encoding="utf-8")
            self.assertIn("main", agent_workspace.merge(12, "改 README"))
        self.assertEqual((self.repo / "README.md").read_text(), "by agent\n")

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve() / "worktrees"
        patcher = patch.object(agent_workspace, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = make_repo()

    def test_agent_works_in_a_worktree_and_merge_brings_changes_back(self) -> None:
        workdir, gitdir = agent_workspace.prepare(7, str(self.repo / "src"))
        self.assertEqual(Path(workdir), self.root / "task-7" / "src")  # 同样的子目录
        self.assertTrue(gitdir.endswith("worktrees/task-7"))
        (Path(workdir) / "app.py").write_text("print(2)\n", encoding="utf-8")
        (Path(workdir) / "new.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual((self.repo / "src" / "app.py").read_text(), "print(1)\n")  # 原目录没动

        changes = agent_workspace.changes(7)
        self.assertEqual(sorted(changes["files"]), ["A\tsrc/new.py", "M\tsrc/app.py"])
        self.assertIn("print(2)", changes["diff"])
        self.assertEqual((changes["branch"], changes["target_branch"]), ("sandrone/task-7", "main"))

        self.assertIn("main", agent_workspace.merge(7, "改 app"))
        self.assertEqual((self.repo / "src" / "app.py").read_text(), "print(2)\n")
        self.assertTrue((self.repo / "src" / "new.py").exists())
        self.assertFalse(agent_workspace.exists(7))
        self.assertNotIn("sandrone/task-7", git(self.repo, "branch"))

    def test_conflicting_local_edit_aborts_the_merge_and_keeps_the_worktree(self) -> None:
        workdir, _ = agent_workspace.prepare(8, str(self.repo))
        (Path(workdir) / "README.md").write_text("from agent\n", encoding="utf-8")
        (self.repo / "README.md").write_text("my own edit\n", encoding="utf-8")  # 用户还没提交的改动
        with self.assertRaises(agent_workspace.WorkspaceError):
            agent_workspace.merge(8, "改 README")
        self.assertEqual((self.repo / "README.md").read_text(), "my own edit\n")
        self.assertTrue(agent_workspace.exists(8))
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "M README.md")  # 没留下合并到一半的状态

    def test_discard_removes_worktree_and_branch(self) -> None:
        workdir, _ = agent_workspace.prepare(9, str(self.repo))
        (Path(workdir) / "README.md").write_text("changed\n", encoding="utf-8")
        agent_workspace.discard(9)
        self.assertFalse(agent_workspace.exists(9))
        self.assertNotIn("sandrone/task-9", git(self.repo, "branch"))
        self.assertEqual((self.repo / "README.md").read_text(), "hello\n")

    def test_no_changes_means_nothing_to_merge(self) -> None:
        agent_workspace.prepare(10, str(self.repo))
        self.assertIn("没有需要合并", agent_workspace.merge(10, "x"))
        self.assertFalse(agent_workspace.exists(10))

    def test_folder_outside_git_is_used_directly(self) -> None:
        plain = tempfile.mkdtemp()
        self.assertEqual(agent_workspace.prepare(11, plain), (plain, ""))
        self.assertFalse(agent_workspace.exists(11))


class DelegationWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve() / "worktrees"
        for item in (patch.object(agent_workspace, "ROOT", self.root),
                     patch.object(agent_sandbox, "srt_executable", return_value="/usr/local/bin/srt"),
                     patch.object(delegation, "agent_executable", side_effect=lambda c: f"/usr/local/bin/{c}-acp"),
                     patch.object(delegation, "_record_tool_call")):
            item.start()
            self.addCleanup(item.stop)
        self.repo = make_repo()

    def run_agent(self, permission: str, workspace_id: int) -> tuple[dict, dict]:
        seen: dict = {}

        def fake_run(args, prompt, cwd, **kwargs):
            seen["cwd"] = cwd
            seen["settings"] = json.loads(Path(args[args.index("-s") + 1]).read_text(encoding="utf-8"))
            return {"ok": True, "output": "done", "message": "", "duration_ms": 1, "agent_session_id": "s"}

        config = SimpleNamespace(agent_enabled=True, agent_command="claude", agent_permission="write",
                                 agent_allowlist=json.dumps([str(self.repo)]), agent_timeout_seconds=60, agent_max_output_chars=1000)
        with patch.object(acp, "run_acp_task", side_effect=fake_run):
            result = delegation.run_delegate_agent(object(), "改", str(self.repo), config,
                                                   permission_override=permission, workspace_id=workspace_id)
        return result, seen

    def test_write_task_runs_in_the_worktree_with_git_metadata_writable(self) -> None:
        result, seen = self.run_agent("write", 21)
        self.assertTrue(result["ok"])
        self.assertEqual(Path(seen["cwd"]), self.root / "task-21")
        self.assertEqual(result["cwd"], str(self.repo))  # 任务卡上仍是原目录
        allowed = seen["settings"]["filesystem"]["allowWrite"]
        self.assertIn(seen["cwd"], allowed)
        self.assertTrue(any(path.endswith("worktrees/task-21") and ".git" in path for path in allowed))
        self.assertNotIn(str(self.repo), allowed)  # 原目录不可写

    def test_readonly_task_without_worktree_reads_the_original(self) -> None:
        _, seen = self.run_agent("readonly", 22)
        self.assertEqual(seen["cwd"], str(self.repo))
        self.assertFalse(agent_workspace.exists(22))

    def test_readonly_follow_up_looks_at_the_existing_worktree(self) -> None:
        self.run_agent("write", 23)
        _, seen = self.run_agent("readonly", 23)
        self.assertEqual(Path(seen["cwd"]), self.root / "task-23")


class TaskCardWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve() / "worktrees"
        patcher = patch.object(agent_workspace, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.repo = make_repo()

    def add(self, session: Session, **fields) -> AgentTask:
        task = AgentTask(title="改 README", prompt="p", command="claude", permission="write", cwd=str(self.repo), **fields)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task

    def test_chain_shares_one_worktree_shown_on_the_latest_card(self) -> None:
        with Session(self.engine) as session:
            first = self.add(session, status="completed")
            second = self.add(session, status="completed", parent_id=first.id)
            self.assertEqual(agent_tasks.workspace_root(session, second), first.id)
            workdir, _ = agent_workspace.prepare(first.id, str(self.repo))
            (Path(workdir) / "README.md").write_text("new\n", encoding="utf-8")
            self.assertIsNone(agent_tasks.task_public(first)["workspace"])  # 交给后面的卡
            card = agent_tasks.task_public(second)["workspace"]
            self.assertEqual(card["files"], ["M\tREADME.md"])

            ok, message = agent_tasks.resolve_workspace(session, second.id, "merge")
            self.assertTrue(ok, message)
            self.assertEqual((self.repo / "README.md").read_text(), "new\n")
            self.assertIsNone(agent_tasks.task_public(second)["workspace"])

    def test_cannot_resolve_while_the_chain_is_running(self) -> None:
        with Session(self.engine) as session:
            first = self.add(session, status="completed")
            self.add(session, status="running", parent_id=first.id)
            agent_workspace.prepare(first.id, str(self.repo))
            ok, message = agent_tasks.resolve_workspace(session, first.id, "discard")
            self.assertFalse(ok)
            self.assertIn("还有任务在跑", message)

    def test_card_says_whether_changes_are_isolated(self) -> None:
        with Session(self.engine) as session:
            in_git = self.add(session, status="proposed")
            outside = self.add(session, status="proposed")
            outside.cwd = tempfile.mkdtemp()
            self.assertTrue(any("独立的工作区" in r for r in agent_tasks.task_risks(in_git)))
            self.assertTrue(any("直接写进原目录" in r for r in agent_tasks.task_risks(outside)))
            self.assertFalse(any(r.startswith("只读模式") for r in agent_tasks.task_risks(in_git)))  # 写入卡上不混进只读的说法


if __name__ == "__main__":
    unittest.main()
