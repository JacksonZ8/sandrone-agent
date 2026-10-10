from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.database import get_session
from app.models import AffectionEvent, Message, SemanticMemory
from app.routers import chat as chat_router
from app.services import chat_service
from app.services.message_service import MessageActionError, delete_message, prepare_regenerate
from tests.llm_fakes import FakeStream, reply


def _reply(text: str) -> dict:
    return reply(text)


class MessageActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _turn(self, session: Session, text: str, reply: str) -> dict:
        with patch.object(chat_service, "chat_completion_stream", FakeStream(_reply(reply))):
            return asyncio.run(chat_service.create_chat_turn(session, text))

    def test_delete_detaches_references_but_keeps_memory(self) -> None:
        with Session(self.engine) as session:
            result = self._turn(session, "我喜欢黑咖啡", "记下了。")
            user_id = result["user_message"]["id"]
            session.add(SemanticMemory(content="喜欢黑咖啡", source_message_ids=json.dumps([user_id, 15])))
            session.add(AffectionEvent(delta=0.01, level_before=0.5, level_after=0.51, reason="测试", source_message_id=user_id))
            session.commit()

            delete_message(session, user_id)

            self.assertIsNone(session.get(Message, user_id))
            memory = session.exec(select(SemanticMemory)).one()
            self.assertEqual(json.loads(memory.source_message_ids), [15])
            events = session.exec(select(AffectionEvent).where(AffectionEvent.delta == 0.01)).all()
            self.assertTrue(events and all(event.source_message_id is None for event in events))
            with self.assertRaises(MessageActionError) as caught:
                delete_message(session, user_id)
            self.assertEqual(caught.exception.status, 404)

    def test_regenerate_only_latest_paired_reply(self) -> None:
        with Session(self.engine) as session:
            first = self._turn(session, "第一句", "回一。")
            second = self._turn(session, "第二句", "回二。")
            with self.assertRaises(MessageActionError):
                prepare_regenerate(session, first["assistant_message"]["id"])
            with self.assertRaises(MessageActionError):
                prepare_regenerate(session, second["user_message"]["id"])

            session.add(Message(turn_index=99, role="assistant", content="她主动发来的"))
            session.commit()
            proactive = session.exec(select(Message).where(Message.turn_index == 99)).one()
            with self.assertRaises(MessageActionError) as caught:
                prepare_regenerate(session, proactive.id)
            self.assertIn("主动", str(caught.exception))

    def test_regenerate_reuses_user_message_without_double_affection(self) -> None:
        with Session(self.engine) as session:
            original = self._turn(session, "谢谢你，今天真开心", "哼。")
            affection_before = len(session.exec(select(AffectionEvent)).all())
            user_message, old_reply = prepare_regenerate(session, original["assistant_message"]["id"])

            fake = FakeStream(_reply("……不客气。"))

            async def collect() -> list[dict]:
                return [
                    event
                    async for event in chat_service.create_chat_turn_stream(
                        session,
                        user_message.content,
                        existing_user_message=user_message,
                        replace_message_id=old_reply.id,
                    )
                ]

            with patch.object(chat_service, "chat_completion_stream", fake):
                events = asyncio.run(collect())

            final = events[-1]
            self.assertEqual(final["type"], "final")
            self.assertEqual(final["user_message"]["id"], original["user_message"]["id"])
            self.assertEqual(final["assistant_message"]["content"], "……不客气。")
            self.assertNotEqual(final["assistant_message"]["id"], original["assistant_message"]["id"])
            self.assertEqual(final["replaced_message_id"], original["assistant_message"]["id"])
            self.assertIsNone(session.get(Message, original["assistant_message"]["id"]))
            history = fake.messages
            self.assertEqual(history[-1], {"role": "user", "content": "谢谢你，今天真开心"})
            self.assertEqual(len(session.exec(select(Message)).all()), 2)
            self.assertEqual(len(session.exec(select(AffectionEvent)).all()), affection_before)

    def test_failed_regenerate_keeps_old_reply(self) -> None:
        from app.services.llm_service import LLMError

        with Session(self.engine) as session:
            original = self._turn(session, "在吗", "在。")
            user_message, old_reply = prepare_regenerate(session, original["assistant_message"]["id"])

            async def collect() -> list[dict]:
                return [
                    event
                    async for event in chat_service.create_chat_turn_stream(
                        session,
                        user_message.content,
                        existing_user_message=user_message,
                        replace_message_id=old_reply.id,
                    )
                ]

            with patch.object(chat_service, "chat_completion_stream", FakeStream(LLMError("超时"))):
                events = asyncio.run(collect())
            self.assertEqual(events[-1]["type"], "error")
            self.assertIsNotNone(session.get(Message, old_reply.id))


class MessageRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(chat_router.router)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()

    def test_delete_route(self) -> None:
        with Session(self.engine) as session:
            session.add(Message(turn_index=1, role="user", content="你好"))
            session.commit()
            message_id = session.exec(select(Message)).one().id
        self.assertEqual(self.client.delete(f"/api/messages/{message_id}").json(), {"ok": True, "deleted_id": message_id})
        self.assertEqual(self.client.delete(f"/api/messages/{message_id}").status_code, 404)

    def test_listing_marks_initiated_messages(self) -> None:
        with Session(self.engine) as session:
            session.add(Message(turn_index=1, role="user", content="你好"))
            session.add(Message(turn_index=1, role="assistant", content="嗯。"))
            session.add(Message(turn_index=2, role="assistant", content="在忙吗", inner_state='{"initiated": true}'))
            session.commit()
        rows = self.client.get("/api/messages").json()
        self.assertEqual([row["initiated"] for row in rows], [False, False, True])

    def test_regenerate_route_rejects_user_message(self) -> None:
        with Session(self.engine) as session:
            session.add(Message(turn_index=1, role="user", content="你好"))
            session.commit()
            message_id = session.exec(select(Message)).one().id
        response = self.client.post(f"/api/messages/{message_id}/regenerate", json={"reply_length": "short"})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
