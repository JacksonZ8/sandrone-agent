from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models import ApiCall
from app.services.usage.audit import sanitize_error
from app.services.usage.aggregation import get_usage_summary


class UsageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_summary_filters_status_tokens_latency_and_privacy(self) -> None:
        now = datetime.now(timezone.utc)
        rows = [
            ApiCall(purpose="chat", provider="deepseek", model="chat-v1", prompt_tokens=100, completion_tokens=20, total_tokens=0, latency_ms=120, status="ok", created_at=now - timedelta(hours=1)),
            ApiCall(purpose="chat", provider="deepseek", model="chat-v1", prompt_tokens=50, completion_tokens=10, total_tokens=60, latency_ms=80, status="error", error="Bearer secret-token at https://api.example.com/x?token=abc", created_at=now - timedelta(hours=2)),
            ApiCall(purpose="web_search", provider="bing", model="private search terms", latency_ms=0, status="empty", created_at=now - timedelta(hours=3)),
            ApiCall(purpose="file_read", provider="local", model="/Users/alice/private/note.md", latency_ms=0, status="generated", created_at=now - timedelta(hours=4)),
            ApiCall(purpose="chat", provider="other", model="old", total_tokens=10, latency_ms=50, status="strange", created_at=now - timedelta(days=40)),
        ]
        with Session(self.engine) as session:
            session.add_all(rows)
            session.commit()
            summary = get_usage_summary(session, start_at=now - timedelta(days=7), end_at=now)

        self.assertEqual(summary["totals"]["calls"], 4)
        self.assertEqual(summary["totals"]["success_calls"], 3)
        self.assertEqual(summary["totals"]["failed_calls"], 1)
        self.assertEqual(summary["totals"]["prompt_tokens"], 150)
        self.assertEqual(summary["totals"]["completion_tokens"], 30)
        self.assertEqual(summary["totals"]["total_tokens"], 180)
        self.assertEqual(summary["totals"]["average_latency_ms"], 100)
        self.assertEqual(summary["totals"]["max_latency_ms"], 120)
        self.assertEqual({item["purpose"] for item in summary["by_purpose"]}, {"chat", "web_search", "file_read"})
        self.assertEqual([(item["provider"], item["model"]) for item in summary["by_model"]], [("deepseek", "chat-v1")])
        serialized = str(summary)
        self.assertNotIn("private search terms", serialized)
        self.assertNotIn("/Users/alice", serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("token=abc", serialized)

    def test_provider_and_purpose_filters_use_safe_identity(self) -> None:
        with Session(self.engine) as session:
            session.add(ApiCall(purpose="screen_analysis", provider="vision-model-name", model="screen", status="ok"))
            session.add(ApiCall(purpose="chat", provider="deepseek", model="chat-v1", status="ok"))
            session.commit()
            summary = get_usage_summary(session, provider="vision", purpose="screen_analysis")

        self.assertEqual(summary["totals"]["calls"], 1)
        self.assertEqual(summary["filters"], {"provider": "vision", "purpose": "screen_analysis"})

    def test_sanitize_error_removes_common_secrets_and_paths(self) -> None:
        raw = "Authorization: Bearer top-secret https://host/path?api_key=secret /home/alice/project C:\\Users\\Bob\\file.txt sk-abcdefgh12345678"
        sanitized = sanitize_error(raw)
        self.assertNotIn("top-secret", sanitized)
        self.assertNotIn("api_key=secret", sanitized)
        self.assertNotIn("/home/alice", sanitized)
        self.assertNotIn("Users\\Bob", sanitized)
        self.assertNotIn("sk-abcdefgh", sanitized)


if __name__ == "__main__":
    unittest.main()
