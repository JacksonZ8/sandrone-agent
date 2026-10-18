from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session

from ...config import DATA_DIR
from ...models import ToolConfig
from .audit import _record_tool_call
from .path_policy import _expand_path, _inside_allowlist


MAX_FILE_BYTES = 512 * 1024


TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".scss", ".less",
    ".csv", ".tsv", ".log", ".sql", ".sh", ".bash", ".zsh", ".xml", ".rst",
    ".c", ".h", ".cpp", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".swift", ".kt",
}


SENSITIVE_DIR_NAMES = {".ssh", ".gnupg", ".aws", ".kube", ".docker", ".password-store"}
SENSITIVE_FILE_NAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", ".pgpass", "known_hosts", "authorized_keys",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keychain", ".keychain-db", ".kdbx"}
SENSITIVE_NAME_MARKERS = ("secret", "credential", "passwd", "private_key", "apikey", "api_key")


# 主目录默认整个可读，下面这些永远不读：系统与应用数据（钥匙串、浏览器资料、聊天记录都在 Library 里）、
# 主目录下的隐藏文件夹（.ssh、.aws、.config 等常放凭据），以及桑多涅自己的数据库与备份（旧备份里有明文 Key）
NAME_SEARCH_DIRS = ("Desktop", "Documents", "Downloads")
NAME_SEARCH_DEPTH = 4
NAME_SEARCH_SKIP = {"node_modules", "__pycache__", "venv", ".venv", "Library"}


def home_dir() -> Path:
    return Path.home().resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def is_private_area(path: Path) -> bool:
    """主目录里永远不读的区域。"""
    home = home_dir()
    if _is_relative_to(path, DATA_DIR.resolve()):
        return True
    if not _is_relative_to(path, home) or path == home:
        return False
    first = path.relative_to(home).parts[0]
    return first == "Library" or first.startswith(".")


def allowed_roots(config: ToolConfig) -> list[Path]:
    """主目录，加上设置里额外添加的文件夹（主目录以外，例如外接硬盘）。"""
    roots = [home_dir()]
    try:
        extras = json.loads(config.file_allowlist or "[]")
    except json.JSONDecodeError:
        extras = []
    for value in extras:
        try:
            root = Path(str(value)).expanduser().resolve()
        except Exception:  # noqa: BLE001
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _find_by_name(name: str, roots: list[Path]) -> list[Path]:
    """只给了文件名：在桌面、文稿、下载和额外文件夹里浅层查找（不扫整个主目录），最多返回 2 个。"""
    home = home_dir()
    starts = [home / folder for folder in NAME_SEARCH_DIRS] + [root for root in roots if root != home]
    matches: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        if len(matches) > 1 or depth > NAME_SEARCH_DEPTH:
            return
        try:
            entries = list(folder.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name == name and entry.is_file():
                matches.append(entry.resolve())
                if len(matches) > 1:
                    return
        for entry in entries:
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in NAME_SEARCH_SKIP:
                walk(entry, depth + 1)

    for start in starts:
        if start.is_dir():
            walk(start, 1)
    return matches


def is_sensitive_path(path: Path) -> bool:
    """可读范围之内也拒绝读取密钥、凭据与环境变量文件，以及主目录里的私密区域。"""
    if is_private_area(path):
        return True
    if any(part.lower() in SENSITIVE_DIR_NAMES for part in path.parts):
        return True
    name = path.name.lower()
    if name.startswith(".env") or name in SENSITIVE_FILE_NAMES:
        return True
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    return any(marker in name for marker in SENSITIVE_NAME_MARKERS)


def read_file_securely(path_value: str, config: ToolConfig) -> dict[str, Any]:
    if not config.files_enabled:
        return {"ok": False, "path": path_value, "content": "", "size": 0, "message": "读文件没有开启。可以在设置的“她能做什么”里打开。"}
    allowlist = allowed_roots(config)

    raw = _expand_path(path_value)
    raw_text = str(raw).strip()
    alias_map = {
        "桌面": Path.home() / "Desktop",
        "下载": Path.home() / "Downloads",
        "文稿": Path.home() / "Documents",
        "项目": Path.home() / "Projects",
    }
    candidates: list[Path] = [alias_map[raw_text]] if raw_text in alias_map else []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend([Path.cwd() / raw, Path.home() / raw])

    path: Path | None = None
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if resolved.exists():
            path = resolved
            break

    if path is None and "/" not in raw_text.replace("\\", "/") and raw_text:
        # 只给了文件名：在桌面、文稿、下载等处浅层找唯一匹配
        matches = [match for match in _find_by_name(raw_text, allowlist) if not is_sensitive_path(match)]
        if len(matches) == 1:
            path = matches[0]

    if path is None:
        return {"ok": False, "path": path_value, "content": "", "size": 0, "message": "文件不存在。请给出完整路径，或只给文件名并确保桌面、文稿、下载里只有一个同名文件。"}

    if not _inside_allowlist(path, allowlist):
        return {"ok": False, "path": str(path), "content": "", "size": 0, "message": "该路径不在她能读的范围内（主目录和设置里额外添加的文件夹）。"}

    if is_private_area(path):
        return {"ok": False, "path": str(path), "content": "", "size": 0, "message": "这里是系统数据或私密文件夹（资源库、隐藏文件夹、桑多涅自己的数据），不会读取。"}
    if is_sensitive_path(path):
        return {"ok": False, "path": str(path), "content": "", "size": 0, "message": "该文件可能包含密钥或凭据，已拒绝读取。"}

    if path.is_dir():
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines = []
        for entry in entries[:200]:
            if is_sensitive_path(entry):
                continue
            if entry.is_dir():
                lines.append(f"[目录] {entry.name}/")
            elif entry.is_file():
                lines.append(f"[文件] {entry.name}  ({entry.stat().st_size} bytes)")
        more = "\n……(还有更多内容)" if len(entries) > 200 else ""
        content = f"目录 {path} 的内容：\n" + "\n".join(lines) + more
        return {"ok": True, "path": str(path), "content": content, "size": len(entries), "message": "目录读取成功。"}

    if not path.is_file():
        return {"ok": False, "path": str(path), "content": "", "size": 0, "message": "目标不是文件。"}
    if path.suffix.lower() not in TEXT_SUFFIXES:
        mime = mimetypes.guess_type(path.name)[0] or ""
        if not mime.startswith("text/"):
            return {"ok": False, "path": str(path), "content": "", "size": 0, "message": "只支持读取文本或代码文件。"}
    if path.stat().st_size > MAX_FILE_BYTES:
        return {"ok": False, "path": str(path), "content": "", "size": path.stat().st_size, "message": f"文件超过 {MAX_FILE_BYTES // 1024}KB 限制。"}

    try:
        raw_bytes = path.read_bytes()
        if b"\x00" in raw_bytes[:4096]:
            return {"ok": False, "path": str(path), "content": "", "size": len(raw_bytes), "message": "文件看起来是二进制内容，已拒绝读取。"}
        content = raw_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "path": str(path), "content": "", "size": 0, "message": f"读取失败：{exc}"}

    return {"ok": True, "path": str(path), "content": content, "size": len(raw_bytes), "message": "读取成功。"}


def read_file_with_audit(session: Session, path_value: str, config: ToolConfig) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = read_file_securely(path_value, config)
    except Exception as exc:
        _record_tool_call(
            session,
            "file_read",
            "local",
            "filesystem",
            "error",
            str(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    _record_tool_call(
        session,
        "file_read",
        "local",
        "filesystem",
        "ok" if result["ok"] else "error",
        "" if result["ok"] else result["message"],
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return result
