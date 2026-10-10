from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session

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


def is_sensitive_path(path: Path) -> bool:
    """白名单之内也拒绝读取密钥、凭据与环境变量文件。"""
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
        return {"ok": False, "path": path_value, "content": "", "size": 0, "message": "文件读取功能未开启。请先在设置里添加允许访问的目录。"}
    try:
        allowlist = [Path(x).expanduser().resolve() for x in json.loads(config.file_allowlist or "[]")]
    except json.JSONDecodeError:
        allowlist = []
    if not allowlist:
        return {"ok": False, "path": path_value, "content": "", "size": 0, "message": "没有配置允许访问的目录。"}

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
        # 只给了文件名：在白名单目录里找唯一匹配
        matches: list[Path] = []
        for root in allowlist:
            if not root.exists():
                continue
            direct = root / raw_text
            if direct.exists():
                matches.append(direct)
            else:
                matches.extend(
                    p for p in root.glob(f"**/{raw_text}") if p.is_file() and _inside_allowlist(p, allowlist)
                )
                if len(matches) > 1:
                    break
        if len(matches) == 1:
            path = matches[0]

    if path is None:
        return {"ok": False, "path": path_value, "content": "", "size": 0, "message": "文件不存在。请给出绝对路径，或只给文件名并确保白名单目录中只有一个同名文件。"}

    if not _inside_allowlist(path, allowlist):
        return {"ok": False, "path": str(path), "content": "", "size": 0, "message": "该路径不在允许访问的目录内。"}

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
