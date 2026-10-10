from __future__ import annotations

import json
from pathlib import Path


def _expand_path(value: str) -> Path:
    value = value.strip().strip("\"'")
    return Path(value).expanduser()


def _inside_allowlist(path: Path, allowlist: list[Path]) -> bool:
    for root in allowlist:
        try:
            if path == root or root in path.parents:
                return True
        except Exception:
            continue
    return False


def _parse_allowlist(raw: str) -> list[Path]:
    try:
        values = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    roots: list[Path] = []
    for value in values:
        try:
            root = Path(str(value)).expanduser().resolve()
        except Exception:
            continue
        if root.exists() and root.is_dir():
            roots.append(root)
    return roots
