from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services import tool_service


class ToolServiceCompatibilityTests(unittest.TestCase):
    def test_path_policy_and_secure_file_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            note = root / "note.md"
            note.write_text("hello sandrone", encoding="utf-8")
            config = SimpleNamespace(files_enabled=True, file_allowlist=json.dumps([str(root)]))

            self.assertTrue(tool_service._inside_allowlist(note.resolve(), [root]))
            self.assertFalse(tool_service._inside_allowlist(Path("/tmp/outside.txt"), [root]))

            result = tool_service.read_file_securely(str(note), config)
            self.assertTrue(result["ok"])
            self.assertEqual(result["content"], "hello sandrone")

    def test_active_tool_schemas_follow_feature_flags(self) -> None:
        config = SimpleNamespace(files_enabled=True, screen_enabled=False, agent_enabled=True)
        names = {
            schema["function"]["name"]
            for schema in tool_service.active_tool_schemas(config)
        }
        self.assertEqual(names, {"read_file", "web_search", "web_fetch", "delegate_agent"})

    def test_agent_allowlist_parser_ignores_missing_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roots = tool_service._parse_allowlist(json.dumps([tmp, "/definitely/missing/sandrone-path"]))
            self.assertEqual(roots, [Path(tmp).resolve()])


if __name__ == "__main__":
    unittest.main()
