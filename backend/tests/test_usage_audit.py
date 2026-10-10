from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ApiCall, LLMConfig
from app.services import llm_service, tts_service
from app.services.tools import files, screen, search


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeAsyncClient:
    def __init__(self, outcome) -> None:
        self.outcome = outcome

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class LLMAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _config(self, **overrides) -> LLMConfig:
        values = {
            "provider": "deepseek",
            "base_url": "https://api.example.com",
            "model": "chat-v1",
            "api_key": "test-key",
            "temperature": 0.2,
            "max_tokens": 128,
            "timeout_seconds": 10,
        }
        values.update(overrides)
        return LLMConfig(**values)

    def _calls(self, session: Session) -> list[ApiCall]:
        return list(session.exec(select(ApiCall)).all())

    async def test_mock_success_writes_one_record(self) -> None:
        with Session(self.engine) as session:
            result = await llm_service.chat_completion(
                session,
                self._config(provider="mock", model="mock-sandrone", api_key=""),
                [{"role": "user", "content": "测试"}],
            )
            calls = self._calls(session)

        self.assertTrue(result["text"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].status, "ok")

    async def test_missing_key_writes_one_error(self) -> None:
        with Session(self.engine) as session:
            with self.assertRaises(llm_service.LLMError):
                await llm_service.chat_completion(
                    session,
                    self._config(api_key=""),
                    [{"role": "user", "content": "hello"}],
                )
            calls = self._calls(session)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].status, "error")

    async def test_timeout_writes_one_timeout(self) -> None:
        timeout = httpx.TimeoutException("request timed out")
        with Session(self.engine) as session, patch.object(
            llm_service.httpx,
            "AsyncClient",
            side_effect=lambda *args, **kwargs: _FakeAsyncClient(timeout),
        ):
            with self.assertRaises(llm_service.LLMError):
                await llm_service.chat_completion(
                    session,
                    self._config(),
                    [{"role": "user", "content": "hello"}],
                )
            calls = self._calls(session)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].status, "timeout")
        self.assertGreaterEqual(calls[0].latency_ms, 0)

    async def test_http_error_is_recorded_once_and_sanitized(self) -> None:
        response = _FakeResponse(
            status_code=401,
            text="Authorization: Bearer leaked https://api.example.com/x?api_key=secret",
        )
        with Session(self.engine) as session, patch.object(
            llm_service.httpx,
            "AsyncClient",
            side_effect=lambda *args, **kwargs: _FakeAsyncClient(response),
        ):
            with self.assertRaises(llm_service.LLMError):
                await llm_service.chat_completion(
                    session,
                    self._config(),
                    [{"role": "user", "content": "hello"}],
                )
            calls = self._calls(session)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].status, "error")
        self.assertNotIn("leaked", calls[0].error)
        self.assertNotIn("api_key=secret", calls[0].error)

    async def test_network_error_and_invalid_json_each_write_one_error(self) -> None:
        outcomes = [
            httpx.ConnectError("network failed", request=httpx.Request("POST", "https://api.example.com")),
            _FakeResponse(payload=ValueError("invalid json")),
        ]
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__), Session(self.engine) as session:
                with patch.object(
                    llm_service.httpx,
                    "AsyncClient",
                    side_effect=lambda *args, **kwargs: _FakeAsyncClient(outcome),
                ):
                    with self.assertRaises(llm_service.LLMError):
                        await llm_service.chat_completion(
                            session,
                            self._config(),
                            [{"role": "user", "content": "hello"}],
                        )
                calls = self._calls(session)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].status, "error")
                session.delete(calls[0])
                session.commit()

    async def test_success_records_usage_once(self) -> None:
        response = _FakeResponse(
            payload={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }
        )
        with Session(self.engine) as session, patch.object(
            llm_service.httpx,
            "AsyncClient",
            side_effect=lambda *args, **kwargs: _FakeAsyncClient(response),
        ):
            await llm_service.chat_completion(
                session,
                self._config(),
                [{"role": "user", "content": "hello"}],
            )
            calls = self._calls(session)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].prompt_tokens, 10)
        self.assertEqual(calls[0].completion_tokens, 4)
        self.assertEqual(calls[0].total_tokens, 14)


class ToolAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _calls(self, session: Session) -> list[ApiCall]:
        return list(session.exec(select(ApiCall)).all())

    def test_file_read_records_one_safe_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            note = root / "private-note.md"
            note.write_text("hello", encoding="utf-8")
            config = SimpleNamespace(files_enabled=True, file_allowlist=json.dumps([str(root)]))
            with Session(self.engine) as session:
                result = files.read_file_with_audit(session, str(note), config)
                calls = self._calls(session)

        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].provider, calls[0].model), ("local", "filesystem"))
        self.assertNotIn("private-note", calls[0].model)
        self.assertGreaterEqual(calls[0].latency_ms, 0)

    def test_search_records_one_call_without_query_identity(self) -> None:
        config = SimpleNamespace(search_provider="duckduckgo")
        with Session(self.engine) as session, patch.object(
            search,
            "search_web",
            return_value=[{"title": "result", "url": "https://example.com", "snippet": "ok"}],
        ):
            search.search_web_with_audit(session, "private search terms", config)
            calls = self._calls(session)

        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].provider, calls[0].model), ("duckduckgo", "search"))
        self.assertNotIn("private search terms", calls[0].model)

    def test_screen_records_one_call_with_real_model(self) -> None:
        config = SimpleNamespace(vision_model="vision-v1")
        result = {"ok": True, "analysis": "done", "message": "屏幕分析完成。"}
        with (
            Session(self.engine) as session,
            patch.object(screen, "get_tool_config", return_value=config),
            patch.object(screen, "_analyze_screen", return_value=result),
        ):
            self.assertEqual(screen.analyze_screen(session, "private question"), result)
            calls = self._calls(session)

        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].provider, calls[0].model), ("vision", "vision-v1"))


class TTSAuditTests(unittest.TestCase):
    def _engine(self, root: str):
        engine = create_engine(f"sqlite:///{Path(root) / 'audit.db'}")
        SQLModel.metadata.create_all(engine)
        return engine

    def test_success_and_timeout_each_record_once(self) -> None:
        outcomes = [
            ({"voice_id": "voice-v1", "latency_ms": 12}, "ok"),
            (httpx.TimeoutException("voice request timeout"), "timeout"),
        ]
        for outcome, expected_status in outcomes:
            with self.subTest(status=expected_status), tempfile.TemporaryDirectory() as tmp:
                engine = self._engine(tmp)
                with (
                    patch.object(tts_service, "engine", engine),
                    patch.object(
                        tts_service,
                        "_synthesize_tts",
                        side_effect=outcome if isinstance(outcome, Exception) else None,
                        return_value=None if isinstance(outcome, Exception) else outcome,
                    ),
                ):
                    if isinstance(outcome, Exception):
                        with self.assertRaises(httpx.TimeoutException):
                            tts_service.synthesize_tts("private speech")
                    else:
                        tts_service.synthesize_tts("private speech")
                with Session(engine) as session:
                    calls = list(session.exec(select(ApiCall)).all())
                engine.dispose()

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].status, expected_status)
            self.assertEqual(calls[0].purpose, "tts")
            self.assertNotIn("private speech", calls[0].error)


if __name__ == "__main__":
    unittest.main()
