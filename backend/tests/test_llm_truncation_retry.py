from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ApiCall, LLMConfig
from app.services import llm_service


def _response(content: str, finish: str, tokens: int) -> Mock:
    response = Mock(status_code=200)
    response.json.return_value = {
        "choices": [{"finish_reason": finish, "message": {"content": content, "reasoning_content": "想了很久……"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": tokens, "total_tokens": tokens + 10},
    }
    return response


class TruncationRetryTests(unittest.TestCase):
    """推理模型把额度用光、正文为空时，加大额度重试一次，而不是返回空回复。"""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.config = LLMConfig(provider="deepseek", api_key="test", max_tokens=1024)

    def _call(self, *responses: Mock) -> tuple[dict, AsyncMock]:
        post = AsyncMock(side_effect=list(responses))
        with patch.object(llm_service.httpx.AsyncClient, "post", post), Session(self.engine) as session:
            result = asyncio.run(llm_service.chat_completion(session, self.config, [{"role": "user", "content": "心脏怎么打"}]))
        return result, post

    def test_retries_with_bigger_budget(self) -> None:
        result, post = self._call(_response("", "length", 1024), _response("先堆格挡。", "stop", 300))
        self.assertEqual(result["text"], "先堆格挡。")
        self.assertEqual(post.await_count, 2)
        self.assertEqual(post.await_args.kwargs["json"]["max_tokens"], 2048)
        with Session(self.engine) as session:
            rows = list(session.exec(select(ApiCall).order_by(ApiCall.id)))
        self.assertEqual([row.status for row in rows], ["error", "ok"])
        self.assertIn("加大额度重试", rows[0].error)

    def test_normal_answer_is_not_retried(self) -> None:
        result, post = self._call(_response("嗯。", "stop", 20))
        self.assertEqual(result["text"], "嗯。")
        self.assertEqual(post.await_count, 1)

    def test_retries_only_once(self) -> None:
        result, post = self._call(_response("", "length", 1024), _response("", "length", 2048))
        self.assertEqual(result["text"], "")
        self.assertEqual(post.await_count, 2)


if __name__ == "__main__":
    unittest.main()
