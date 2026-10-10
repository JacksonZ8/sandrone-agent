from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.models import ToolConfig
from app.services.tools.files import is_sensitive_path, read_file_securely


class FileToolSensitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "notes.md").write_text("hello", encoding="utf-8")
        (self.root / ".env.local").write_text("KEY=1", encoding="utf-8")
        (self.root / "secrets.yaml").write_text("token: x", encoding="utf-8")
        (self.root / "credentials.json").write_text("{}", encoding="utf-8")
        (self.root / ".ssh").mkdir()
        (self.root / ".ssh" / "config.txt").write_text("Host x", encoding="utf-8")
        self.config = ToolConfig(files_enabled=True, file_allowlist=json.dumps([str(self.root)]))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ordinary_text_file_is_readable(self) -> None:
        result = read_file_securely(str(self.root / "notes.md"), self.config)
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "hello")

    def test_secret_like_files_are_refused_inside_allowlist(self) -> None:
        for name in ("secrets.yaml", "credentials.json", ".ssh/config.txt"):
            with self.subTest(name=name):
                result = read_file_securely(str(self.root / name), self.config)
                self.assertFalse(result["ok"])
                self.assertEqual(result["content"], "")
                self.assertIn("密钥或凭据", result["message"])

    def test_bare_filename_lookup_cannot_reach_secret(self) -> None:
        result = read_file_securely("secrets.yaml", self.config)
        self.assertFalse(result["ok"])
        self.assertEqual(result["content"], "")

    def test_directory_listing_hides_sensitive_entries(self) -> None:
        result = read_file_securely(str(self.root), self.config)
        self.assertTrue(result["ok"])
        self.assertIn("notes.md", result["content"])
        for hidden in (".env.local", "secrets.yaml", "credentials.json", ".ssh"):
            self.assertNotIn(hidden, result["content"])

    def test_classifier_keeps_normal_code_files(self) -> None:
        self.assertFalse(is_sensitive_path(Path("/p/app/services/tokenizer.py")))
        self.assertFalse(is_sensitive_path(Path("/p/docs/forgot_password_flow.md")))
        self.assertTrue(is_sensitive_path(Path("/p/server.key")))
        self.assertTrue(is_sensitive_path(Path("/p/.env")))


if __name__ == "__main__":
    unittest.main()
