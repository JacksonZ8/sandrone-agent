from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlmodel import Session, SQLModel, create_engine

from app import secret_store
from app.models import LLMConfig, ToolConfig, WeChatAccount


class EncryptedColumnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "db.sqlite"
        self.engine = create_engine(f"sqlite:///{self.path}")
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _raw(self, sql: str) -> list[tuple]:
        connection = sqlite3.connect(self.path)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def test_secrets_are_ciphertext_on_disk_and_plain_in_code(self) -> None:
        with Session(self.engine) as session:
            session.add(LLMConfig(id=1, api_key="sk-live-123"))
            session.add(ToolConfig(id=1, vision_api_key="vk-456", search_api_key=""))
            session.add(WeChatAccount(id=1, token="wx-token"))
            session.commit()
        on_disk = self._raw("SELECT api_key FROM llm_config")[0][0]
        self.assertTrue(on_disk.startswith(secret_store.PREFIX))
        self.assertNotIn("sk-live-123", self.path.read_bytes().decode("latin-1"))
        self.assertEqual(self._raw("SELECT search_api_key FROM tool_config")[0][0], "")  # 空值保持空
        with Session(self.engine) as session:
            self.assertEqual(session.get(LLMConfig, 1).api_key, "sk-live-123")
            self.assertEqual(session.get(ToolConfig, 1).vision_api_key, "vk-456")
            self.assertEqual(session.get(WeChatAccount, 1).token, "wx-token")

    def test_old_plaintext_is_readable_then_rewritten(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute("INSERT INTO llm_config (id, provider, base_url, model, api_key, temperature, max_tokens, timeout_seconds, reply_thinking, updated_at) VALUES (1, 'deepseek', 'x', 'm', 'sk-old', 0.7, 1024, 60, 'auto', '2026-10-01')")
        connection.commit()
        connection.close()
        with Session(self.engine) as session:
            self.assertEqual(session.get(LLMConfig, 1).api_key, "sk-old")
        with self.engine.begin() as connection:
            self.assertEqual(secret_store.plaintext_secret_count(connection), 1)
            self.assertEqual(secret_store.encrypt_existing_secrets(connection), 1)
            self.assertEqual(secret_store.plaintext_secret_count(connection), 0)
        self.assertTrue(self._raw("SELECT api_key FROM llm_config")[0][0].startswith(secret_store.PREFIX))
        with Session(self.engine) as session:
            self.assertEqual(session.get(LLMConfig, 1).api_key, "sk-old")

    def test_lost_master_key_reads_as_empty_instead_of_crashing(self) -> None:
        stored = secret_store.encrypt("sk-live")
        with patch.object(secret_store, "_fernet", Fernet(Fernet.generate_key())), self.assertLogs(secret_store.logger, "WARNING"):
            self.assertEqual(secret_store.decrypt(stored), "")


class KeychainTests(unittest.TestCase):
    def test_missing_item_creates_a_key(self) -> None:
        calls = []

        def run(args, **kwargs):
            calls.append(args[1])
            return CompletedProcess(args, 44 if args[1] == "find-generic-password" else 0, "", "")

        with patch.object(secret_store.subprocess, "run", side_effect=run):
            key = secret_store._keychain_key()
        Fernet(key)
        self.assertEqual(calls, ["find-generic-password", "add-generic-password"])

    def test_existing_item_is_used(self) -> None:
        stored = Fernet.generate_key()
        with patch.object(secret_store.subprocess, "run", return_value=CompletedProcess([], 0, stored.decode() + "\n", "")):
            self.assertEqual(secret_store._keychain_key(), stored)

    def test_locked_keychain_never_generates_a_new_key(self) -> None:
        with patch.object(secret_store.subprocess, "run", return_value=CompletedProcess([], 51, "", "locked")) as run:
            with self.assertRaises(secret_store.SecretStoreError):
                secret_store._keychain_key()
        self.assertEqual(run.call_count, 1)  # 没有尝试写入新密钥，否则旧密文永远解不开


if __name__ == "__main__":
    unittest.main()
