from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import LLMConfig, Message
from app.services import chat_service, llm_service
from app.services.chat_prompt import layout_messages
from app.services.chat_service import get_recent_messages
from tests.llm_fakes import FakeStream, reply


class ThinkingTests(unittest.TestCase):
    def test_levels_map_to_deepseek_fields(self) -> None:
        deepseek = LLMConfig(provider="deepseek", base_url="https://api.deepseek.com")
        self.assertEqual(llm_service.thinking_fields(deepseek, "off"), {"thinking": {"type": "disabled"}})
        self.assertEqual(llm_service.thinking_fields(deepseek, "low"), {"thinking": {"type": "enabled"}, "reasoning_effort": "low"})
        self.assertEqual(llm_service.thinking_fields(deepseek, None), {})
        other = LLMConfig(provider="openai_compatible", base_url="https://api.example.com/v1")
        self.assertEqual(llm_service.thinking_fields(other, "off"), {})

    def test_auto_turns_thinking_on_only_when_tools_are_likely(self) -> None:
        self.assertEqual(llm_service.resolve_thinking("auto", "今天好累"), "off")
        for text in ("帮我查一下北京天气", "杀戮尖塔2现在有几个角色？搜一下", "看看我的屏幕", "这个链接 https://x.example 讲了啥"):
            self.assertEqual(llm_service.resolve_thinking("auto", text), "low", text)
        self.assertEqual(llm_service.resolve_thinking("high", "今天好累"), "high")

    def test_reasoning_gets_headroom(self) -> None:
        deepseek = LLMConfig(provider="deepseek", base_url="https://api.deepseek.com")
        payload = {"max_tokens": 1024}
        llm_service._apply_thinking(payload, deepseek, "low")
        self.assertEqual(payload["max_tokens"], llm_service.THINKING_MIN_TOKENS)
        payload = {"max_tokens": 1024}
        llm_service._apply_thinking(payload, deepseek, "off")
        self.assertEqual(payload["max_tokens"], 1024)

    def test_unsupported_thinking_fields_are_dropped_and_retried(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        bad = Mock(status_code=400, text='{"error": "unknown field: thinking"}')
        good = Mock(status_code=200)
        good.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": "嗯。"}}], "usage": {}}
        post = Mock(side_effect=[bad, good])

        async def fake_post(self, url, **kwargs):
            return post(url, **kwargs)

        config = LLMConfig(provider="deepseek", base_url="https://api.deepseek.com", api_key="k")
        with Session(engine) as session, patch.object(llm_service.httpx.AsyncClient, "post", fake_post):
            result = asyncio.run(llm_service.chat_completion(session, config, [{"role": "user", "content": "在吗"}], thinking="off"))
        self.assertEqual(result["text"], "嗯。")
        self.assertNotIn("thinking", post.call_args_list[1].kwargs["json"])


class LayoutTests(unittest.TestCase):
    def test_changing_context_goes_right_before_the_latest_message(self) -> None:
        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
        layout = layout_messages("固定", "这一轮", history)
        self.assertEqual([m["content"] for m in layout], ["固定", "a", "b", "这一轮", "c"])
        self.assertEqual([m["content"] for m in layout_messages("固定", "这一轮", history[:2])], ["固定", "a", "b", "这一轮"])

    def test_history_window_start_moves_every_ten_messages(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            for index in range(57):
                session.add(Message(role="user", content=str(index), turn_index=index))
            session.commit()
            first = get_recent_messages(session, limit=40, aligned=True)
            self.assertEqual((first[0].content, len(first)), ("10", 47))
            session.add(Message(role="user", content="57", turn_index=57))
            session.commit()
            self.assertEqual(get_recent_messages(session, limit=40, aligned=True)[0].content, "10")  # 起点不动，前缀缓存仍可命中
            self.assertEqual(len(get_recent_messages(session, limit=40)), 40)


class StreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _events(self, fake: FakeStream) -> list[dict]:
        async def collect() -> list[dict]:
            with Session(self.engine) as session:
                session.add(LLMConfig(id=1, provider="deepseek", api_key="k", reply_thinking="off"))
                session.commit()
                return [event async for event in chat_service.create_chat_turn_stream(session, "在吗")]

        with patch.object(chat_service, "chat_completion_stream", fake), patch.object(chat_service, "execute_tool_call", return_value={"ok": True, "results": []}):
            return asyncio.run(collect())

    def test_reply_text_streams_before_final(self) -> None:
        fake = FakeStream(reply("在。"))
        events = self._events(fake)
        kinds = [event["type"] for event in events]
        self.assertLess(kinds.index("delta"), kinds.index("final"))
        self.assertEqual(fake.calls[0]["thinking"], "off")
        self.assertEqual(fake.messages[0]["role"], "system")
        self.assertEqual(fake.messages[-1], {"role": "user", "content": "在吗"})
        self.assertEqual(fake.messages[-2]["role"], "system")  # 这一轮的上下文紧挨着最新一句

    def test_partial_text_is_cleared_when_she_goes_to_use_a_tool(self) -> None:
        call = {"id": "c1", "name": "web_search", "arguments": {"query": "天气"}}
        fake = FakeStream(reply("我查一下。", [call]), reply("晴。"))
        kinds = [event["type"] for event in self._events(fake)]
        self.assertEqual(kinds.count("delta_reset"), 1)
        self.assertLess(kinds.index("delta_reset"), kinds.index("tool_call"))
        self.assertEqual(kinds[-1], "final")


    def test_read_only_tools_in_one_round_run_in_parallel(self) -> None:
        import time

        calls = [{"id": f"c{i}", "name": "web_fetch", "arguments": {"url": f"https://x.example/{i}", "prompt": "?"}} for i in range(3)]
        fake = FakeStream(reply("", calls), reply("好了。"))

        def slow_tool(name, arguments):
            time.sleep(0.3)
            return {"ok": True, "answer": arguments["url"]}

        async def collect() -> list[dict]:
            with Session(self.engine) as session:
                session.add(LLMConfig(id=1, provider="deepseek", api_key="k"))
                session.commit()
                return [event async for event in chat_service.create_chat_turn_stream(session, "查一下这三个页面")]

        started = time.perf_counter()
        with patch.object(chat_service, "chat_completion_stream", fake), patch.object(chat_service, "_run_tool_isolated", side_effect=slow_tool):
            events = asyncio.run(collect())
        self.assertLess(time.perf_counter() - started, 0.8)  # 串行要 0.9 秒以上
        tool_messages = [m for m in fake.messages if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["c0", "c1", "c2"])  # 结果按原顺序回填
        self.assertIn("https://x.example/2", tool_messages[2]["content"])
        self.assertEqual(events[-1]["type"], "final")

if __name__ == "__main__":
    unittest.main()
