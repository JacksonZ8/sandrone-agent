"""写入类任务的独立工作区：任务目录在 git 仓库里时，智能体不直接改你的目录，而是在一个 git worktree 里改。
做完你在任务卡上看改动，点“合并”才进你当前的分支，点“丢弃”就当没发生。

- 位置：~/.sandrone/worktrees/task-<编号>（SANDRONE_WORKTREE_DIR 可改），分支 sandrone/task-<编号>，从你当前的提交开始（你还没提交的改动不在里面）。
- 续接同一个任务时接着用同一个工作区（编号取最早那个任务的）。
- 不在 git 仓库里的目录没法这样隔离，改动直接写在原目录（任务卡上会写明）。
- 合并：在工作区里提交全部改动，再在你的仓库里 git merge --no-ff；有冲突或你那边有会被覆盖的改动就放弃合并，工作区留着。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("SANDRONE_WORKTREE_DIR", str(Path.home() / ".sandrone" / "worktrees"))).expanduser()
DIFF_LIMIT = 60_000


class WorkspaceError(RuntimeError):
    pass


def _git(cwd: str | Path, *args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise WorkspaceError((result.stderr or result.stdout).strip()[:500] or f"git {' '.join(args)} 失败")
    return result


def repo_root(path: str) -> Path | None:
    result = _git(path, "rev-parse", "--show-toplevel", check=False)
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 and result.stdout.strip() else None


def location(root_task_id: int) -> tuple[Path, str]:
    return ROOT / f"task-{root_task_id}", f"sandrone/task-{root_task_id}"


def exists(root_task_id: int) -> bool:
    return (location(root_task_id)[0] / ".git").exists()


def prepare(root_task_id: int, cwd: str) -> tuple[str, str]:
    """返回 (智能体实际干活的目录, 工作区 git 元数据目录)；不在 git 仓库里返回 (cwd, "")。"""
    repo = repo_root(cwd)
    if repo is None:
        return cwd, ""
    path, branch = location(root_task_id)
    if not exists(root_task_id):
        ROOT.mkdir(parents=True, exist_ok=True)
        if _git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
            raise WorkspaceError("这个仓库还没有任何提交，没法开独立工作区。")
        _git(repo, "worktree", "add", "-b", branch, str(path), "HEAD")
    relative = Path(cwd).resolve().relative_to(repo)
    workdir = path / relative
    workdir.mkdir(parents=True, exist_ok=True)
    gitdir = _git(path, "rev-parse", "--absolute-git-dir").stdout.strip()
    return str(workdir), gitdir


def _source_repo(root_task_id: int) -> Path:
    """工作区属于哪个仓库（主仓库的工作目录）。"""
    path, _ = location(root_task_id)
    common = Path(_git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    return common.parent


def changes(root_task_id: int) -> dict[str, Any] | None:
    """工作区里相对起点的改动（含新文件）；没有工作区返回 None。"""
    if not exists(root_task_id):
        return None
    path, branch = location(root_task_id)
    repo = _source_repo(root_task_id)
    _git(path, "add", "-A")
    # 起点：分出来的那个提交（智能体自己提交过也算进改动）
    base = _git(repo, "merge-base", "HEAD", branch, check=False).stdout.strip() or "HEAD"
    stat = _git(path, "diff", "--cached", "--stat", base).stdout.strip()
    files = [line for line in _git(path, "diff", "--cached", "--name-status", base).stdout.splitlines() if line.strip()]
    diff = _git(path, "diff", "--cached", base).stdout
    return {
        "path": str(path),
        "branch": branch,
        "files": files[:200],
        "stat": stat[-4000:],
        "diff": diff[:DIFF_LIMIT] + ("\n……[改动太长，完整内容请在工作区里看]" if len(diff) > DIFF_LIMIT else ""),
        "target_branch": _git(repo, "branch", "--show-current", check=False).stdout.strip(),
    }


def merge(root_task_id: int, title: str) -> str:
    """把工作区的改动合并进你仓库当前的分支，成功后删掉工作区和分支。"""
    if not exists(root_task_id):
        raise WorkspaceError("这个任务的工作区已经不在了。")
    path, branch = location(root_task_id)
    repo = _source_repo(root_task_id)
    _git(path, "add", "-A")
    if _git(path, "diff", "--cached", "--quiet", check=False).returncode != 0:
        _git(path, "-c", "user.name=Sandrone", "-c", "user.email=sandrone@localhost",
             "commit", "--no-verify", "-m", f"Sandrone task #{root_task_id}: {title}"[:200])
    if _git(repo, "rev-list", "--count", f"HEAD..{branch}").stdout.strip() == "0":
        discard(root_task_id)
        return "没有需要合并的改动，工作区已清理。"
    result = _git(repo, "merge", "--no-ff", "--no-edit", branch, check=False)
    if result.returncode != 0:
        _git(repo, "merge", "--abort", check=False)
        detail = (result.stderr or result.stdout).strip()[:300]
        raise WorkspaceError(f"没能合并（工作区还留着，可以自己处理）：{detail}")
    target = _git(repo, "branch", "--show-current", check=False).stdout.strip() or "当前分支"
    discard(root_task_id)
    return f"已合并进 {target}。"


def discard(root_task_id: int) -> None:
    path, branch = location(root_task_id)
    if exists(root_task_id):
        repo = _source_repo(root_task_id)
        _git(repo, "worktree", "remove", "--force", str(path))
        _git(repo, "branch", "-D", branch, check=False)
    elif path.exists() and not any(path.iterdir()):
        os.rmdir(path)
