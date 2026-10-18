from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import ToolConfig
from app.services.tools import files
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


class HomeFolderTests(unittest.TestCase):
    """默认整个主目录可读，不需要先填白名单；资源库、主目录隐藏文件夹与桑多涅数据永远不读。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        for folder in ("Desktop/proj", "Documents", "Library/Messages", ".config/gh", "Music"):
            (self.home / folder).mkdir(parents=True)
        (self.home / "Desktop/proj/plan.md").write_text("计划", encoding="utf-8")
        (self.home / "Music/lyrics.txt").write_text("歌词", encoding="utf-8")
        (self.home / "Library/Messages/chat.txt").write_text("聊天", encoding="utf-8")
        (self.home / ".config/gh/hosts.yml").write_text("oauth_token: x", encoding="utf-8")
        self.data = self.home / "sandrone/data"
        self.data.mkdir(parents=True)
        (self.data / "notes.txt").write_text("db", encoding="utf-8")
        self.patches = [
            patch.object(files, "home_dir", return_value=self.home),
            patch.object(files, "DATA_DIR", self.data),
        ]
        for item in self.patches:
            item.start()
        self.config = ToolConfig(files_enabled=True, file_allowlist="[]")

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def test_anything_in_home_is_readable_without_a_list(self) -> None:
        result = read_file_securely(str(self.home / "Music/lyrics.txt"), self.config)
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(result["content"], "歌词")

    def test_private_areas_are_never_read(self) -> None:
        for path in ("Library/Messages/chat.txt", ".config/gh/hosts.yml", "sandrone/data/notes.txt"):
            with self.subTest(path=path):
                result = read_file_securely(str(self.home / path), self.config)
                self.assertFalse(result["ok"])
                self.assertEqual(result["content"], "")

    def test_home_listing_hides_library_and_hidden_folders(self) -> None:
        result = read_file_securely(str(self.home), self.config)
        self.assertTrue(result["ok"])
        self.assertIn("Desktop/", result["content"])
        self.assertNotIn("Library", result["content"])
        self.assertNotIn(".config", result["content"])

    def test_bare_name_searches_only_desktop_documents_downloads(self) -> None:
        self.assertEqual(read_file_securely("plan.md", self.config)["content"], "计划")
        self.assertFalse(read_file_securely("lyrics.txt", self.config)["ok"])  # Music 不在按名查找的范围里
        self.assertFalse(read_file_securely("chat.txt", self.config)["ok"])

    def test_outside_home_needs_an_extra_folder(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            target = Path(other).resolve() / "a.txt"
            target.write_text("外接盘", encoding="utf-8")
            self.assertFalse(read_file_securely(str(target), self.config)["ok"])
            extra = ToolConfig(files_enabled=True, file_allowlist=json.dumps([other]))
            self.assertEqual(read_file_securely(str(target), extra)["content"], "外接盘")

    def test_switch_off_means_nothing_is_read(self) -> None:
        off = ToolConfig(files_enabled=False, file_allowlist="[]")
        self.assertFalse(read_file_securely(str(self.home / "Music/lyrics.txt"), off)["ok"])


if __name__ == "__main__":
    unittest.main()
