"""看屏：看一眼会认出游戏并带上档案；没建档的游戏自动加；语音可以临时开“陪着看”，到点结束。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import GameProfile, GameSession, ScreenCompanionConfig, ScreenObservation, ToolConfig
from app.services import screen_companion
from app.services.games.detect import adopt_detected_game, parse_look_reply
from app.services.screen_companion import ScreenCompanion, start_watch, stop_watch, update_companion_config
from app.services.tools import screen as screen_tool
from app.services.tools.registry import active_tool_schemas, execute_tool_call
from tests.test_screen_companion import FakeDesktop, _llm, _window

GAME_APP = "Balatro"


def vision(reply: str) -> Mock:
    return Mock(return_value={"ok": True, "analysis": reply, "message": ""})


class LookTests(unittest.TestCase):
    """看一眼。"""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(ToolConfig(id=1, screen_enabled=True, vision_api_key="test-key"))
            session.commit()
        self.desktop = FakeDesktop()
        self.desktop.front = (GAME_APP, 7)
        self.desktop.windows = [_window(31, GAME_APP, 7), _window(21, "Google Chrome", 3)]
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.engine.dispose()

    def _full_screen(self) -> Path:
        path = Path(self.tmp.name) / "screen.jpg"
        path.write_bytes(b"full")
        return path

    def _look(self, session: Session, reply: str, question: str = "我该选哪张牌") -> tuple[dict, Mock]:
        model = vision(reply)
        with (
            patch.object(screen_tool, "MAC_DESKTOP", self.desktop),
            patch.object(screen_tool, "_capture_screen", side_effect=self._full_screen),
            patch.object(screen_tool, "call_vision", model),
        ):
            return execute_tool_call(session, "look_at_screen", {"question": question}), model

    def _add_game(self, session: Session) -> GameProfile:
        game = GameProfile(name="小丑牌", app_names=json.dumps([GAME_APP]), role="coach", save_card=json.dumps({"progress": "进阶 5"}))
        session.add(game)
        session.commit()
        session.refresh(game)
        return game

    def test_known_game_uses_its_window_profile_and_counts_as_playing(self) -> None:
        with Session(self.engine) as session:
            game = self._add_game(session)
            result, model = self._look(session, "选左边那张，过牌更稳。")
            self.assertEqual((result["analysis"], result["game"]), ("选左边那张，过牌更稳。", "小丑牌"))
            self.assertEqual(self.desktop.captured, [31])  # 只截游戏窗口
            prompt = model.call_args.args[2]
            self.assertIn("《小丑牌》", prompt)
            self.assertIn("进阶 5", prompt)
            self.assertIn("我该选哪张牌", prompt)
            playing = session.exec(select(GameSession).where(GameSession.game_id == game.id)).one()
            self.assertIsNone(playing.ended_at)
            seen = session.exec(select(ScreenObservation)).one()
            self.assertEqual((seen.game_id, seen.session_id), (game.id, playing.id))

    def test_unknown_game_is_added_automatically(self) -> None:
        with Session(self.engine) as session:
            reply = json.dumps({"answer": "这是选牌界面，建议拿防御牌。", "game": "《小丑牌》"}, ensure_ascii=False)
            result, _ = self._look(session, reply)
            self.assertEqual(result["analysis"], "这是选牌界面，建议拿防御牌。")
            self.assertIn("自动加进游戏资料", result["note"])
            game = session.exec(select(GameProfile)).one()
            self.assertEqual(
                (game.name, json.loads(game.app_names), game.role, game.recap_enabled, game.auto_detected),
                ("小丑牌", [GAME_APP], "spectator", False, True),
            )
            # 下次看就是建过档的游戏：只截游戏窗口
            self._look(session, "好。")
            self.assertEqual(self.desktop.captured, [31])

    def test_ordinary_screen_and_browser_games_add_nothing(self) -> None:
        with Session(self.engine) as session:
            self.desktop.front = ("Google Chrome", 3)
            plain, _ = self._look(session, json.dumps({"answer": "是一道动态规划题。", "game": ""}, ensure_ascii=False), "讲讲这题")
            self.assertEqual(plain, {"ok": True, "analysis": "是一道动态规划题。"})
            web_game, _ = self._look(session, json.dumps({"answer": "网页小游戏。", "game": "2048"}, ensure_ascii=False))
            self.assertNotIn("game", web_game)
            self.assertEqual(session.exec(select(GameProfile)).all(), [])

    def test_plain_text_reply_still_works(self) -> None:
        self.assertEqual(parse_look_reply("屏幕上是终端。"), ("屏幕上是终端。", ""))
        self.assertEqual(parse_look_reply('{"answer": "好", "game": ""}'), ("好", ""))

    def test_detected_game_joins_an_existing_profile(self) -> None:
        with Session(self.engine) as session:
            game = self._add_game(session)
            found, added = adopt_detected_game(session, "小丑牌", "Balatro-Steam")
            self.assertEqual((found.id, added), (game.id, False))
            self.assertEqual(json.loads(found.app_names), sorted([GAME_APP, "Balatro-Steam"]))


class WatchTests(unittest.TestCase):
    """陪着看：语音临时开，只看开始时前台的应用，到点结束。"""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(ScreenCompanionConfig(id=1, enabled=False))
            session.add(ToolConfig(id=1, screen_enabled=True, vision_api_key="test-key"))
            session.commit()
        self.engine_patch = patch.object(screen_companion, "engine", self.engine)
        self.engine_patch.start()
        self.desktop = FakeDesktop()  # 前台是 Code
        self.now = datetime.now(timezone.utc)
        self.companion = ScreenCompanion(clock=lambda: self.now, desktop=self.desktop)
        self.seen = json.dumps({"summary": "在写代码", "detail": "", "notable": False, "game": ""}, ensure_ascii=False)

    def tearDown(self) -> None:
        self.engine_patch.stop()
        self.engine.dispose()

    def _tick(self) -> str:
        with patch.object(screen_companion, "call_vision", vision(self.seen)), patch.object(
            screen_companion, "chat_completion", _llm('{"say": false, "text": ""}')
        ):
            return asyncio.run(self.companion.tick())

    def _consent(self, session: Session) -> None:
        update_companion_config(session, {"consent": True})

    def test_needs_consent_but_not_always_on(self) -> None:
        with Session(self.engine) as session:
            refused = start_watch(session, 30, desktop=self.desktop)
            self.assertFalse(refused["ok"])
            self.assertIn("同意", refused["error"])
            self._consent(session)
            started = start_watch(session, 30, desktop=self.desktop)
            self.assertTrue(started["ok"])
            self.assertEqual(started["app"], "Code")
            config = session.get(ScreenCompanionConfig, 1)
            self.assertFalse(config.enabled)
            self.assertEqual(json.loads(config.watch_apps), ["Code"])

    def test_watch_session_observes_only_its_app_then_expires(self) -> None:
        with Session(self.engine) as session:
            self._consent(session)
            start_watch(session, 30, desktop=self.desktop)
        self.assertEqual(self._tick(), "observed")
        self.assertEqual(self.desktop.captured, [11])
        self.desktop.front = ("Google Chrome", 3)
        self.now += timedelta(minutes=10)
        self.assertEqual(self._tick(), "not_allowed")
        self.now += timedelta(minutes=25)  # 过了 30 分钟
        self.assertEqual(self._tick(), "off")
        with Session(self.engine) as session:
            config = session.get(ScreenCompanionConfig, 1)
            self.assertIsNone(config.watch_until)
            self.assertEqual(config.watch_apps, "[]")

    def test_stop_and_tool_dispatch(self) -> None:
        with Session(self.engine) as session:
            self._consent(session)
            started = start_watch(session, 999, desktop=self.desktop)
            self.assertTrue(started["ok"])
            until = session.get(ScreenCompanionConfig, 1).watch_until
            self.assertLessEqual(until.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc), timedelta(minutes=240))
            stopped = execute_tool_call(session, "watch_screen", {"action": "stop"})
            self.assertEqual(stopped["message"], "已经不看了。")
            self.assertEqual(self._tick(), "off")

    def test_game_seen_while_watching_is_added(self) -> None:
        with Session(self.engine) as session:
            self._consent(session)
            start_watch(session, 30, desktop=self.desktop)
        self.seen = json.dumps({"summary": "在打牌", "detail": "", "notable": False, "game": "小丑牌"}, ensure_ascii=False)
        self.assertEqual(self._tick(), "observed")
        with Session(self.engine) as session:
            game = session.exec(select(GameProfile)).one()
            self.assertEqual((game.name, json.loads(game.app_names), game.auto_detected), ("小丑牌", ["Code"], True))

    def test_watch_tool_only_offered_with_screen_enabled(self) -> None:
        on = type("C", (), {"files_enabled": False, "screen_enabled": True, "agent_enabled": False})()
        off = type("C", (), {"files_enabled": False, "screen_enabled": False, "agent_enabled": False})()
        names = lambda config: {schema["function"]["name"] for schema in active_tool_schemas(config)}  # noqa: E731
        self.assertTrue({"look_at_screen", "watch_screen"} <= names(on))
        self.assertFalse({"look_at_screen", "watch_screen"} & names(off))


if __name__ == "__main__":
    unittest.main()
