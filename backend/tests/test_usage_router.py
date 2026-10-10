from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.models import ApiCall
from app.routers import usage


class UsageRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(usage.router)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()

    def test_summary_is_read_only(self) -> None:
        with Session(self.engine) as session:
            session.add(ApiCall(purpose="chat", provider="deepseek", model="chat-v1", total_tokens=10, status="ok"))
            session.commit()

        summary = self.client.get("/api/usage/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["totals"]["calls"], 1)
        self.assertEqual(self.client.get("/api/usage/prices").status_code, 404)
        self.assertEqual(self.client.put("/api/usage/prices", json={}).status_code, 404)
        self.assertEqual(self.client.delete("/api/usage/prices/1").status_code, 404)

    def test_invalid_range_is_rejected(self) -> None:
        response = self.client.get(
            "/api/usage/summary",
            params={"start_at": "2026-08-31T10:00:00Z", "end_at": "2026-08-31T09:00:00Z"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
