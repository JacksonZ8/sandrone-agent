from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Message, ScreenCompanionConfig, ScreenObservation, ToolConfig
from app.services import screen_companion
from app.services.screen_companion import (
    ScreenCompanion,
    parse_remark,
    parse_vision_reply,
    screen_context_block,
    update_companion_config,
)
from app.services.tools import screen as screen_tool
from app.services.tools.windows import (
    DesktopSnapshot,
    WindowImage,
    WindowInfo,
    dhash,
    encode_window_image,
    hash_distance,
    parse_idle_seconds,
    parse_snapshot,
)

SEEN = json.dumps({"summary": "在 VS Code 里改 Python 测试", "detail": "终端里有 AssertionError", "notable": True}, ensure_ascii=False)


def _llm(text: str) -> AsyncMock:
    return AsyncMock(
        return_value={
            "text": text, "tool_calls": [], "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "latency_ms": 1,
        }
    )


def _window(id: int, app: str, pid: int, w: int = 1200, h: int = 800) -> WindowInfo:
    return WindowInfo(id=id, app=app, pid=pid, width=w, height=h)


class FakeDesktop:
    def __init__(self) -> None:
        self.front = ("Code", 2)
        self.windows = [_window(11, "Code", 2), _window(21, "Google Chrome", 3)]
        self.idle = 3.0
        self.fingerprint = 0
        self.captured: list[int] = []
        self.notes: list[str] = []

    def snapshot(self) -> DesktopSnapshot:
        return DesktopSnapshot(app=self.front[0], pid=self.front[1], windows=tuple(self.windows))

    def idle_seconds(self) -> float:
        return self.idle

    def capture(self, window_id: int) -> WindowImage:
        self.captured.append(window_id)
        return WindowImage(b"jpeg", 640, 400, self.fingerprint)

    def notify(self, title: str, text: str) -> None:
        self.notes.append(text)

    def running_apps(self) -> list[str]:
        return ["Code", "Google Chrome"]


class WindowHelperTests(unittest.TestCase):
    def test_snapshot_front_window_skips_small_and_other_apps(self) -> None:
        raw = json.dumps({
            "app": "Code", "pid": 2,
            "windows": [
                {"id": 1, "app": "Code", "pid": 2, "w": 300, "h": 40},  # 工具条
                {"id": 2, "app": "Google Chrome", "pid": 3, "w": 1500, "h": 900},
                {"id": 3, "app": "Code", "pid": 2, "w": 1500, "h": 900},
            ],
        })
        snapshot = parse_snapshot(raw)
        self.assertEqual(snapshot.front_window().id, 3)
        self.assertEqual(snapshot.front_window({"Google Chrome"}).id, 2)
        self.assertIsNone(snapshot.front_window({"WeChat"}))

    def test_idle_parse(self) -> None:
        self.assertAlmostEqual(parse_idle_seconds('| "HIDIdleTime" = 2500000000\n'), 2.5)
        self.assertIsNone(parse_idle_seconds("nothing"))

    def test_fingerprint_stable_for_same_content(self) -> None:
        image = Image.new("RGB", (400, 300), (20, 20, 20))
        for x in range(0, 400, 40):
            image.paste((200, 200, 200), (x, 0, x + 20, 300))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        encoded = encode_window_image(buffer.getvalue())
        self.assertLessEqual(max(encoded.width, encoded.height), 1280)
        self.assertEqual(hash_distance(encoded.fingerprint, dhash(image)), 0)
        flipped = image.transpose(Image.FLIP_LEFT_RIGHT)
        self.assertGreater(hash_distance(dhash(flipped), dhash(image)), 5)


class ParsingTests(unittest.TestCase):
    def test_vision_reply(self) -> None:
        seen = parse_vision_reply("```json\n" + SEEN + "\n```")
        self.assertEqual((seen.summary, seen.notable), ("在 VS Code 里改 Python 测试", True))
        self.assertEqual(parse_vision_reply("在看网页").summary, "在看网页")
        self.assertIsNone(parse_vision_reply('{"summary": ""}'))
        self.assertFalse(parse_vision_reply('{"summary": "看文档", "notable": "yes"}').notable)

    def test_remark(self) -> None:
        self.assertEqual(parse_remark('{"say": true, "text": "第 12 行减号写反了。"}'), "第 12 行减号写反了。")
        self.assertEqual(parse_remark('{"say": false, "text": "嗯"}'), "")
        self.assertEqual(parse_remark('{"say": "true", "text": "嗯"}'), "")
        self.assertEqual(len(parse_remark(json.dumps({"say": True, "text": "长" * 500}))), 120)


class CompanionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(ScreenCompanionConfig(
                id=1, enabled=True, remark_gap_minutes=45,
                consent_at=datetime.now(timezone.utc),
            ))
            session.add(ToolConfig(id=1, screen_enabled=True, vision_api_key="test-key"))
            session.commit()
        self.engine_patch = patch.object(screen_companion, "engine", self.engine)
        self.engine_patch.start()
        self.now = datetime.now(timezone.utc)
        self.desktop = FakeDesktop()
        self.companion = ScreenCompanion(clock=lambda: self.now, desktop=self.desktop)
        self.vision = unittest.mock.Mock(return_value={"ok": True, "analysis": SEEN, "message": ""})

    def tearDown(self) -> None:
        self.engine_patch.stop()
        self.engine.dispose()

    def _tick(self, llm: AsyncMock | None = None, *, force: bool = False) -> str:
        with patch.object(screen_companion, "call_vision", self.vision), patch.object(
            screen_companion, "chat_completion", llm or _llm('{"say": false, "text": ""}')
        ):
            return asyncio.run(self.companion.tick(force=force))

    def _config(self, **changes) -> None:
        with Session(self.engine) as session:
            config = session.get(ScreenCompanionConfig, 1)
            for key, value in changes.items():
                setattr(config, key, value)
            session.add(config)
            session.commit()

    def _rows(self) -> list[ScreenObservation]:
        with Session(self.engine) as session:
            return list(session.exec(select(ScreenObservation).order_by(ScreenObservation.id)))

    def _assistant(self) -> list[Message]:
        with Session(self.engine) as session:
            return list(session.exec(select(Message).where(Message.role == "assistant")))

    def test_enabling_requires_consent(self) -> None:
        self._config(enabled=False, consent_at=None)
        with Session(self.engine) as session:
            with self.assertRaises(ValueError):
                update_companion_config(session, {"enabled": True})
            config = update_companion_config(
                session, {"enabled": True, "consent": True, "hidden_apps": ["Discord", " Discord ", "", "1Password"]}
            )
            self.assertIsNotNone(config.consent_at)
            self.assertEqual(json.loads(config.hidden_apps), ["Discord"])  # 密码类应用本来就永远不看，不存进可编辑名单

    def test_any_app_is_watched_without_a_list(self) -> None:
        self.desktop.front = ("Figma", 2)
        self.desktop.windows = [_window(11, "Figma", 2)]
        self.assertEqual(self._tick(), "observed")
        self.assertEqual(self.desktop.captured, [11])

    def test_password_apps_stay_hidden_even_with_an_empty_list(self) -> None:
        self._config(hidden_apps="[]")  # 用户把默认不看的聊天应用都放开了
        self.desktop.front = ("1Password", 2)
        self.assertEqual(self._tick(), "not_allowed")
        self.desktop.front = ("WeChat", 2)
        self.desktop.windows = [_window(11, "WeChat", 2)]
        self.assertEqual(self._tick(), "observed")

    def test_off_and_paused_do_not_look(self) -> None:
        self._config(enabled=False)
        self.assertEqual(self._tick(), "off")
        self._config(enabled=True, paused_until=self.now + timedelta(minutes=30))
        self.assertEqual(self._tick(), "paused")
        self.assertEqual(self.desktop.captured, [])

    def test_app_outside_list_is_not_captured_or_named(self) -> None:
        self.desktop.front = ("WeChat", 9)
        self.assertEqual(self._tick(), "not_allowed")
        self.assertEqual(self.desktop.captured, [])
        self.assertEqual(self.companion.status.app, "")
        self.vision.assert_not_called()

    def test_away_is_skipped(self) -> None:
        self.desktop.idle = 900
        self.assertEqual(self._tick(), "away")
        self.assertEqual(self.desktop.captured, [])

    def test_observes_front_window_and_stays_quiet_when_she_declines(self) -> None:
        self.assertEqual(self._tick(), "observed")
        self.assertEqual(self.desktop.captured, [11])
        rows = self._rows()
        self.assertEqual((rows[0].app, rows[0].summary, rows[0].notable), ("Code", "在 VS Code 里改 Python 测试", True))
        self.assertEqual(self._assistant(), [])

    def test_interval_and_unchanged_screen_skip_vision(self) -> None:
        self.assertEqual(self._tick(), "observed")
        self.assertEqual(self._tick(), "waiting")
        self.now += timedelta(minutes=6)
        self.desktop.fingerprint = 0b11  # 只差两位：视为没变
        self.assertEqual(self._tick(), "unchanged")
        self.assertEqual(self.vision.call_count, 1)
        self.now += timedelta(minutes=6)
        self.desktop.fingerprint = (1 << 40) - 1
        self.assertEqual(self._tick(), "observed")
        self.assertEqual(self.vision.call_count, 2)

    def test_she_speaks_on_web_timeline_and_notifies(self) -> None:
        llm = _llm('{"say": true, "text": "第 12 行减号写反了吧。"}')
        self.assertEqual(self._tick(llm), "spoke")
        (message,) = self._assistant()
        self.assertEqual((message.thread_id, message.channel), ("main", "web"))
        self.assertTrue(json.loads(message.inner_state)["screen"])
        self.assertEqual(self._rows()[0].message_id, message.id)
        self.assertEqual(self.desktop.notes, ["第 12 行减号写反了吧。"])
        system = llm.await_args.args[2][0]["content"]
        self.assertIn("陪他看着屏幕", system)
        self.assertIn("AssertionError", system)
        self.assertEqual(llm.await_args.kwargs["purpose"], "screen_companion")

    def test_remark_gap_and_recent_chat_keep_her_quiet(self) -> None:
        llm = _llm('{"say": true, "text": "歇会儿？"}')
        self.assertEqual(self._tick(llm), "spoke")
        self.now += timedelta(minutes=10)
        self.desktop.fingerprint = (1 << 40) - 1
        self.assertEqual(self._tick(llm), "observed")  # 距上次开口不到 45 分钟
        self.now += timedelta(minutes=60)
        self.desktop.fingerprint = 0
        with Session(self.engine) as session:
            session.add(Message(role="user", content="我在写作业", created_at=self.now - timedelta(minutes=2), turn_index=9))
            session.commit()
        self.assertEqual(self._tick(llm), "observed")  # 刚聊过
        self.assertEqual(llm.await_count, 1)

    def test_watch_only_never_speaks(self) -> None:
        self._config(remark_gap_minutes=0)
        llm = _llm('{"say": true, "text": "嗯"}')
        self.assertEqual(self._tick(llm), "observed")
        llm.assert_not_awaited()

    def test_manual_check_looks_at_allowed_window_behind_browser(self) -> None:
        self.desktop.front = ("Google Chrome", 3)
        self.desktop.windows = [_window(21, "Google Chrome", 3), _window(11, "Code", 2)]
        self.assertEqual(self._tick(force=True), "observed")
        self.assertEqual(self.desktop.captured, [11])

    def test_no_vision_key_means_no_capture(self) -> None:
        with Session(self.engine) as session:
            tool = session.get(ToolConfig, 1)
            tool.vision_api_key = ""
            session.add(tool)
            session.commit()
        self.assertEqual(self._tick(), "error")
        self.assertEqual(self.desktop.captured, [])

    def test_capture_failure_reports_error(self) -> None:
        self.desktop.capture = unittest.mock.Mock(side_effect=RuntimeError("窗口截图失败：could not create image"))
        self.assertEqual(self._tick(), "error")
        self.assertIn("截图失败", self.companion.status.message)
        self.vision.assert_not_called()

    def test_context_block_only_recent_and_enabled(self) -> None:
        with Session(self.engine) as session:
            session.add(ScreenObservation(created_at=self.now - timedelta(hours=2), app="Code", summary="很久以前"))
            session.add(ScreenObservation(created_at=self.now - timedelta(minutes=3), app="Code", summary="在改测试"))
            session.commit()
            block = screen_context_block(session, self.now)
            self.assertIn("在改测试", block)
            self.assertNotIn("很久以前", block)
        self._config(enabled=False)
        with Session(self.engine) as session:
            self.assertEqual(screen_context_block(session, self.now), "")


class HelpScreenshotTests(unittest.TestCase):
    """看一眼：屏幕上没有不看的应用时截整屏；有的话只截她可以看的最前面的窗口，整屏截图不会带上密码或聊天。"""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.desktop = FakeDesktop()
        self.screen_file = Path(tempfile.mkdtemp()) / "screen.jpg"
        self.screen_file.write_bytes(b"whole")

    def _capture(self):
        with Session(self.engine) as session:
            session.add(ScreenCompanionConfig(id=1))
            session.commit()
            with patch.object(screen_tool, "MAC_DESKTOP", self.desktop), patch.object(
                screen_tool, "_capture_screen", return_value=self.screen_file
            ):
                return screen_tool._capture_for_help(session)

    def test_whole_screen_when_nothing_hidden_is_open(self) -> None:
        image, refusal, app, _ = self._capture()
        self.assertEqual((image, refusal, app, self.desktop.captured), (b"whole", "", "Code", []))

    def test_only_a_visible_window_when_a_hidden_app_is_open(self) -> None:
        self.desktop.windows = [_window(31, "微信", 4), _window(11, "Code", 2)]
        image, refusal, app, _ = self._capture()
        self.assertEqual((image, refusal, app, self.desktop.captured), (b"jpeg", "", "Code", [11]))

    def test_refuses_when_only_hidden_apps_are_open(self) -> None:
        self.desktop.front = ("1Password", 4)
        self.desktop.windows = [_window(31, "1Password", 4)]
        image, refusal, _, _ = self._capture()
        self.assertIsNone(image)
        self.assertIn("不看", refusal)
        self.assertEqual(self.desktop.captured, [])


if __name__ == "__main__":
    unittest.main()
