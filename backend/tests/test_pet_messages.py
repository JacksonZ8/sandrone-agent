from __future__ import annotations

import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.models import Message
from app.routers import pet


class PetMessagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(pet.router)

        def session():
            with Session(self.engine) as s:
                yield s

        app.dependency_overrides[get_session] = session
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _add(self, role: str, content: str, initiated: bool = False) -> int:
        with Session(self.engine) as s:
            message = Message(role=role, content=content, inner_state=json.dumps({"initiated": True} if initiated else {}))
            s.add(message)
            s.commit()
            return message.id

    def test_first_poll_only_returns_the_latest_id(self) -> None:
        last = self._add("assistant", "旧的主动消息", initiated=True)
        self.assertEqual(self.client.get("/api/pet/messages").json(), {"latest_id": last, "messages": []})

    def test_only_messages_she_started_become_bubbles(self) -> None:
        start = self._add("user", "在吗")
        self._add("assistant", "在。")  # 普通回复不冒泡
        remark = self._add("assistant", "  你又在调这个按钮了？\n别急。 ", initiated=True)
        self._add("user", "嗯")
        body = self.client.get(f"/api/pet/messages?after={start}").json()
        self.assertEqual([(m["id"], m["text"]) for m in body["messages"]], [(remark, "你又在调这个按钮了？ 别急。")])
        self.assertEqual(body["latest_id"], remark + 1)
        self.assertEqual(self.client.get(f"/api/pet/messages?after={body['latest_id']}").json()["messages"], [])

    def test_long_messages_are_shortened(self) -> None:
        start = self._add("user", "x")
        self._add("assistant", "很长" * 80, initiated=True)
        text = self.client.get(f"/api/pet/messages?after={start}").json()["messages"][0]["text"]
        self.assertEqual(len(text), pet.BUBBLE_MAX_CHARS)
        self.assertTrue(text.endswith("…"))


if __name__ == "__main__":
    unittest.main()
