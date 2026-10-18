from __future__ import annotations

import unittest

import httpx
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ApiCall, LLMConfig
from app.services.usage import balance


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class BalanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _config(self, session: Session, **fields) -> None:
        session.add(LLMConfig(id=1, provider="deepseek", base_url="https://api.deepseek.com", api_key="sk-test", **fields))
        session.commit()

    def test_formats_like_the_pet(self) -> None:
        payload = {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "12.5"}, {"currency": "USD", "total_balance": "1"}]}
        self.assertEqual(balance.format_balance(payload), "¥12.50 · US$1.00")
        self.assertIsNone(balance.format_balance({"balance_infos": [{"currency": "EUR", "total_balance": "3"}]}))
        self.assertIsNone(balance.format_balance({"balance_infos": [{"currency": "CNY", "total_balance": "1e3"}]}))

    def test_queries_official_endpoint_with_saved_key(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"], seen["auth"] = str(request.url), request.headers["Authorization"]
            return httpx.Response(200, json={"balance_infos": [{"currency": "CNY", "total_balance": "8.06"}]})

        with Session(self.engine) as session:
            self._config(session)
            result = balance.fetch_balance(session, client=client(handler))
            audit = session.exec(select(ApiCall)).one()
        self.assertEqual(result, {"ok": True, "text": "¥8.06"})
        self.assertEqual(seen, {"url": balance.BALANCE_URL, "auth": "Bearer sk-test"})
        self.assertEqual((audit.purpose, audit.status), ("deepseek_balance", "ok"))

    def test_fixed_messages_for_failures(self) -> None:
        with Session(self.engine) as session:
            self._config(session)
            self.assertEqual(balance.fetch_balance(session, client=client(lambda r: httpx.Response(401)))["text"], balance.AUTH_FAILED)
            self.assertEqual(balance.fetch_balance(session, client=client(lambda r: httpx.Response(302, headers={"location": "https://evil.example/"})))["text"], balance.UNAVAILABLE)

            def down(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("boom")

            self.assertEqual(balance.fetch_balance(session, client=client(down))["text"], balance.UNREACHABLE)

    def test_never_sends_the_key_to_a_custom_base_url(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("不应发出请求")

        with Session(self.engine) as session:
            self._config(session)
            config = session.get(LLMConfig, 1)
            config.base_url = "https://proxy.example/v1"
            session.add(config)
            session.commit()
            self.assertEqual(balance.fetch_balance(session, client=client(handler)), {"ok": False, "text": balance.NOT_CONFIGURED})


if __name__ == "__main__":
    unittest.main()
