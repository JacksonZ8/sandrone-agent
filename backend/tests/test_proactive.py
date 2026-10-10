from __future__ import annotations

import asyncio
import json
import random
import unittest
from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Message, ProactiveConfig, ProactiveEvent
from app.services import proactive_service
from app.services.proactive_service import (
    ProactiveScheduler,
    WeChatTarget,
    in_quiet_hours,
    parse_proactive_reply,
    plan_slots,
)

LOCAL = timezone(timedelta(hours=8))


def _llm(text: str) -> AsyncMock:
    return AsyncMock(
        return_value={
            "text": text, "tool_calls": [], "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "latency_ms": 1,
        }
    )


SEND_REPLY = json.dumps({"send": True, "text": "刚把那组数据跑完。你那边考试准备得怎么样了？"}, ensure_ascii=False)


class ProactivePlanningTests(unittest.TestCase):
    def test_quiet_hours_wrap_midnight(self) -> None:
        start, end = time(23, 0), time(9, 0)
        self.assertTrue(in_quiet_hours(datetime(2026, 10, 8, 23, 30, tzinfo=LOCAL), start, end))
        self.assertTrue(in_quiet_hours(datetime(2026, 10, 8, 8, 59, tzinfo=LOCAL), start, end))
        self.assertFalse(in_quiet_hours(datetime(2026, 10, 8, 9, 0, tzinfo=LOCAL), start, end))
        self.assertFalse(in_quiet_hours(datetime(2026, 10, 8, 22, 59, tzinfo=LOCAL), start, end))

    def test_slots_are_future_outside_quiet_hours_and_spaced(self) -> None:
        start = datetime(2026, 10, 8, 9, 0, tzinfo=LOCAL)
        for seed in range(20):
            slots = plan_slots(start, 2, time(23, 0), time(9, 0), random.Random(seed))
            self.assertEqual(len(slots), 2)
            for slot in slots:
                self.assertGreater(slot, start)
                self.assertFalse(in_quiet_hours(slot, time(23, 0), time(9, 0)))
            self.assertGreaterEqual(slots[1] - slots[0], timedelta(minutes=90))

    def test_late_start_only_plans_remaining_window(self) -> None:
        start = datetime(2026, 10, 8, 22, 50, tzinfo=LOCAL)
        slots = plan_slots(start, 2, time(23, 0), time(9, 0), random.Random(1))
        self.assertLessEqual(len(slots), 1)
        self.assertTrue(all(start < slot < start.replace(hour=23) for slot in slots))

    def test_reply_parsing(self) -> None:
        self.assertFalse(parse_proactive_reply('{"send": false, "text": ""}').send)
        self.assertFalse(parse_proactive_reply("我想想。").send)
        draft = parse_proactive_reply('```json\n{"send": true, "text": "在吗"}\n```')
        self.assertEqual((draft.send, draft.text), (True, "在吗"))
        self.assertFalse(parse_proactive_reply('{"send": "true", "text": "在吗"}').send)
        self.assertEqual(len(parse_proactive_reply(json.dumps({"send": True, "text": "长" * 999})).text), 300)

class ProactiveSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(ProactiveConfig(id=1, enabled=True))
            session.commit()
        self.engine_patch = patch.object(proactive_service, "engine", self.engine)
        self.engine_patch.start()
        self.now = datetime(2026, 10, 8, 15, 0, tzinfo=LOCAL)
        self.send = AsyncMock()
        self.scheduler = ProactiveScheduler(
            clock=lambda: self.now,
            rng=random.Random(0),
            target_factory=lambda: WeChatTarget(owner_id="owner", send=self.send),
        )

    def tearDown(self) -> None:
        self.engine_patch.stop()
        self.engine.dispose()

    def _due_now(self) -> None:
        self.scheduler.plan_day = self.now.date()
        self.scheduler.slots = [self.now - timedelta(minutes=1)]

    def _tick(self, llm: AsyncMock | None = None) -> str:
        with patch.object(proactive_service, "chat_completion", llm or _llm(SEND_REPLY)):
            return asyncio.run(self.scheduler.tick())

    def _events(self) -> list[str]:
        with Session(self.engine) as session:
            return [row.status + ":" + row.reason for row in session.exec(select(ProactiveEvent).order_by(ProactiveEvent.id))]

    def _add_message(self, role: str, minutes_ago: int) -> None:
        created = (self.now - timedelta(minutes=minutes_ago)).astimezone(timezone.utc)
        with Session(self.engine) as session:
            session.add(Message(role=role, content="……", created_at=created, turn_index=1))
            session.commit()

    def test_disabled_does_nothing(self) -> None:
        with Session(self.engine) as session:
            config = session.get(ProactiveConfig, 1)
            config.enabled = False
            session.add(config)
            session.commit()
        self._due_now()
        self.assertEqual(self._tick(), "disabled")
        self.send.assert_not_awaited()

    def test_not_due_waits(self) -> None:
        self.scheduler.plan_day = self.now.date()
        self.scheduler.slots = [self.now + timedelta(hours=1)]
        self.assertEqual(self._tick(), "waiting")
        self.assertEqual(self._events(), [])

    def test_due_slot_sends_and_records_on_main_timeline(self) -> None:
        self._add_message("user", minutes_ago=600)
        self._due_now()
        llm = _llm(SEND_REPLY)
        self.assertEqual(self._tick(llm), "sent")
        self.send.assert_awaited_once()
        self.assertEqual(self.send.await_args.args[0], "owner")
        with Session(self.engine) as session:
            message = session.exec(select(Message).where(Message.role == "assistant")).one()
        self.assertEqual((message.thread_id, message.channel), ("main", "wechat_dm"))
        self.assertIn("考试", message.content)
        self.assertTrue(json.loads(message.inner_state)["initiated"])
        self.assertEqual(llm.await_args.kwargs["purpose"], "proactive")
        self.assertIn("主动联系", llm.await_args.args[2][0]["content"])

    def test_she_may_choose_silence(self) -> None:
        self._due_now()
        self.assertEqual(self._tick(_llm('{"send": false, "text": ""}')), "declined")
        self.send.assert_not_awaited()

    def test_no_second_ping_before_reply(self) -> None:
        self._due_now()
        self.assertEqual(self._tick(), "sent")
        self._due_now()
        self.assertEqual(self._tick(), "awaiting_reply")
        self.assertEqual(self.send.await_count, 1)

    def test_recent_chat_suppresses_ping(self) -> None:
        self._add_message("user", minutes_ago=30)
        self._due_now()
        self.assertEqual(self._tick(), "recently_chatted")
        self.send.assert_not_awaited()

    def test_quiet_hours_suppress_ping(self) -> None:
        self.now = datetime(2026, 10, 8, 23, 30, tzinfo=LOCAL)
        self._due_now()
        self.assertEqual(self._tick(), "quiet_hours")
        self.send.assert_not_awaited()

    def test_daily_limit(self) -> None:
        with Session(self.engine) as session:
            for _ in range(2):
                session.add(ProactiveEvent(status="sent", created_at=(self.now - timedelta(hours=3)).astimezone(timezone.utc)))
            session.commit()
        self._add_message("user", minutes_ago=150)
        self._due_now()
        self.assertEqual(self._tick(), "daily_limit")

    def test_send_failure_is_logged_without_timeline_message(self) -> None:
        self.send.side_effect = RuntimeError("iLink send failed ret=0 errcode=-14")
        self._due_now()
        self.assertEqual(self._tick(), "failed")
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(Message)).all(), [])

    def test_wechat_unavailable(self) -> None:
        self.scheduler.target_factory = lambda: None
        self._due_now()
        self.assertEqual(self._tick(), "wechat_unavailable")

    def test_simultaneously_due_slots_fire_once(self) -> None:
        self.scheduler.plan_day = self.now.date()
        self.scheduler.slots = [self.now - timedelta(hours=3), self.now - timedelta(minutes=5)]
        self.assertEqual(self._tick(), "sent")
        self.assertEqual(self.scheduler.slots, [])


if __name__ == "__main__":
    unittest.main()
