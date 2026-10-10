from __future__ import annotations

import unittest

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.security import LocalRequestGuard


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocalRequestGuard)

    @app.get("/api/thing")
    def read_thing() -> dict:
        return {"ok": True}

    @app.post("/api/thing")
    def write_thing() -> dict:
        return {"ok": True}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.websocket("/api/ws")
    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    return app


class LocalRequestGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_app(), base_url="http://127.0.0.1:8000")

    def tearDown(self) -> None:
        self.client.close()

    def test_local_reads_pass_without_marker(self) -> None:
        self.assertEqual(self.client.get("/api/thing").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_localhost_name_is_accepted(self) -> None:
        response = self.client.get("/health", headers={"Host": "localhost:5173"})
        self.assertEqual(response.status_code, 200)

    def test_foreign_host_is_rejected_for_reads(self) -> None:
        response = self.client.get("/api/thing", headers={"Host": "attacker.example:8000"})
        self.assertEqual(response.status_code, 400)

    def test_mutation_requires_local_marker(self) -> None:
        self.assertEqual(self.client.post("/api/thing").status_code, 403)
        response = self.client.post("/api/thing", headers={"X-Sandrone-Local": "1"})
        self.assertEqual(response.status_code, 200)

    def test_mutation_from_foreign_origin_is_rejected(self) -> None:
        response = self.client.post(
            "/api/thing",
            headers={"X-Sandrone-Local": "1", "Origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_mutation_from_local_frontend_origin_passes(self) -> None:
        response = self.client.post(
            "/api/thing",
            headers={"X-Sandrone-Local": "1", "Origin": "http://127.0.0.1:5173"},
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()


class LocalWebSocketGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_app(), base_url="http://127.0.0.1:8000")

    def tearDown(self) -> None:
        self.client.close()

    def test_local_origin_connects(self) -> None:
        with self.client.websocket_connect("ws://127.0.0.1:8000/api/ws", headers={"Origin": "http://localhost:5173"}) as ws:
            self.assertEqual(ws.receive_text(), "hello")

    def test_foreign_or_missing_origin_is_refused(self) -> None:
        for headers in ({"Origin": "https://attacker.example"}, {}):
            with self.assertRaises(WebSocketDisconnect) as caught:
                with self.client.websocket_connect("ws://127.0.0.1:8000/api/ws", headers=headers):
                    pass
            self.assertEqual(caught.exception.code, 1008)

    def test_foreign_host_is_refused(self) -> None:
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "ws://127.0.0.1:8000/api/ws", headers={"Origin": "http://localhost:5173", "Host": "attacker.example"}
            ):
                pass


class TailnetAccessTests(unittest.TestCase):
    """经 Tailscale Serve 的远程访问：只放行白名单里的 tailnet 账号。"""

    def setUp(self) -> None:
        app = FastAPI()

        @app.get("/api/thing")
        def read_thing() -> dict:
            return {"ok": True}

        @app.post("/api/thing")
        def write_thing() -> dict:
            return {"ok": True}

        @app.websocket("/api/ws")
        async def echo(websocket: WebSocket) -> None:
            await websocket.accept()
            await websocket.send_text("hello")
            await websocket.close()

        app.add_middleware(LocalRequestGuard, tailnet_logins={"owner@example.com"})
        self.client = TestClient(app, base_url="https://mac.tail1234.ts.net")
        self.owner = {"Tailscale-User-Login": "Owner@Example.com"}

    def tearDown(self) -> None:
        self.client.close()

    def test_owner_reads_and_writes_from_tailnet(self) -> None:
        self.assertEqual(self.client.get("/api/thing", headers=self.owner).status_code, 200)
        response = self.client.post(
            "/api/thing",
            headers={**self.owner, "X-Sandrone-Local": "1", "Origin": "https://mac.tail1234.ts.net"},
        )
        self.assertEqual(response.status_code, 200)

    def test_other_or_missing_login_is_refused(self) -> None:
        self.assertEqual(self.client.get("/api/thing").status_code, 400)
        stranger = {"Tailscale-User-Login": "someone@example.com"}
        self.assertEqual(self.client.get("/api/thing", headers=stranger).status_code, 400)

    def test_identity_header_on_local_host_is_still_checked(self) -> None:
        local = TestClient(self.client.app, base_url="http://127.0.0.1:8000")
        stranger = {"Tailscale-User-Login": "someone@example.com"}
        self.assertEqual(local.get("/api/thing", headers=stranger).status_code, 400)
        self.assertEqual(local.get("/api/thing").status_code, 200)

    def test_cross_site_origin_still_refused_for_writes(self) -> None:
        response = self.client.post(
            "/api/thing",
            headers={**self.owner, "X-Sandrone-Local": "1", "Origin": "https://attacker.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_websocket_from_tailnet_owner(self) -> None:
        url = "wss://mac.tail1234.ts.net/api/ws"
        headers = {**self.owner, "Origin": "https://mac.tail1234.ts.net"}
        with self.client.websocket_connect(url, headers=headers) as ws:
            self.assertEqual(ws.receive_text(), "hello")
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(url, headers={"Origin": "https://mac.tail1234.ts.net"}):
                pass

    def test_no_logins_configured_means_no_remote_access(self) -> None:
        app = FastAPI()

        @app.get("/api/thing")
        def read_thing() -> dict:
            return {"ok": True}

        app.add_middleware(LocalRequestGuard, tailnet_logins=set())
        client = TestClient(app, base_url="https://mac.tail1234.ts.net")
        self.assertEqual(client.get("/api/thing", headers=self.owner).status_code, 400)
