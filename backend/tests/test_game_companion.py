from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    GameProfile,
    GameSession,
    Message,
    ScreenCompanionConfig,
    ScreenObservation,
    SemanticMemory,
    ToolConfig,
)
from app.services import screen_companion
from app.services.games import play
from app.services.games.play import GameState, game_context_block, may_speak, parse_game_state
from app.services.games.profiles import create_game, delete_game, load_save_card, update_game
from app.services.games.scope import game_scope, message_scope
from app.services.memory.retrieval import retrieve_semantic_memories_scored
from app.services.screen_companion import ScreenCompanion
from app.services.tools.windows import DesktopSnapshot, WindowImage, WindowInfo


def _state(scene: str = "menu", event: str = "none") -> GameState:
    return GameState(scene=scene, event=event, progress="", summary="在打", detail="")


def _vision_reply(scene: str, event: str = "none", progress: str = "第 3 幕") -> dict:
    analysis = json.dumps(
        {"scene": scene, "event": event, "progress": progress, "summary": f"在{scene}", "detail": "血量偏低"},
        ensure_ascii=False,
    )
    return {"ok": True, "analysis": analysis, "message": ""}


RECAP = json.dumps(
    {
        "recap": "这把打到第 3 幕了，那个 Boss 你处理得不错。",
        "save_card": {"progress": "第 3 幕", "build": "力量战", "goals": ""},
        "memories": [
            {"perspective": "user", "type": "preference", "content": "他打尖塔喜欢走力量流", "importance": 0.7, "confidence": 0.8},
            {"perspective": "shared", "type": "event", "content": "我们一起第一次打到了第 3 幕", "importance": 0.6, "confidence": 0.8},
        ],
    },
    ensure_ascii=False,
)


class FakeDesktop:
    def __init__(self) -> None:
        self.front = ("SlayTheSpire", 4)
        self.windows = [WindowInfo(41, "SlayTheSpire", 4, 1600, 900), WindowInfo(11, "Code", 2, 1200, 800)]
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
        self.fingerprint ^= (1 << 40) - 1  # 游戏画面每次都在变
        return WindowImage(b"jpeg", 640, 360, self.fingerprint)

    def notify(self, title: str, text: str) -> None:
        self.notes.append(text)


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)

    def test_role_and_app_are_required(self) -> None:
        with Session(self.engine) as session:
            with self.assertRaises(ValueError):
                create_game(session, {"name": "尖塔", "app_names": ["SlayTheSpire"]})
            with self.assertRaises(ValueError):
                create_game(session, {"name": "尖塔", "app_names": [], "role": "coach"})
            with self.assertRaises(ValueError):
                create_game(session, {"name": "尖塔", "app_names": ["SlayTheSpire"], "role": "boss"})
            game = create_game(session, {"name": "尖塔", "app_names": ["SlayTheSpire", " SlayTheSpire "], "role": "coach"})
            self.assertEqual(json.loads(game.app_names), ["SlayTheSpire"])

    def test_user_edit_replaces_but_model_merge_keeps(self) -> None:
        with Session(self.engine) as session:
            game = create_game(session, {"name": "尖塔", "app_names": ["SlayTheSpire"], "role": "partner"})
            update_game(session, game, {"save_card": {"progress": "第 2 幕", "goals": "通关进阶 5"}})
            play.merge_save_card(game, {"progress": "第 3 幕", "goals": ""})
            card = load_save_card(game)
            self.assertEqual((card["progress"], card["goals"]), ("第 3 幕", "通关进阶 5"))
            update_game(session, game, {"save_card": {"goals": ""}})
            self.assertEqual(load_save_card(game)["goals"], "")


class RuleTests(unittest.TestCase):
    def _game(self, role: str) -> GameProfile:
        return GameProfile(id=1, name="尖塔", role=role)

    def test_state_parsing_normalizes(self) -> None:
        state = parse_game_state('{"scene": "Boss战", "event": "boss_defeated", "summary": "打赢了"}')
        self.assertEqual((state.scene, state.event), ("other", "boss_defeated"))
        self.assertIsNone(parse_game_state("看不清"))

    def test_never_during_combat_and_recorder_silent(self) -> None:
        now = datetime.now(timezone.utc)
        fresh = GameSession(game_id=1)
        for role in ("spectator", "partner", "coach"):
            self.assertFalse(may_speak(self._game(role), fresh, _state("combat", "boss_defeated"), now))
        self.assertFalse(may_speak(self._game("recorder"), fresh, _state("result", "victory"), now))

    def test_role_timing(self) -> None:
        now = datetime.now(timezone.utc)
        fresh = GameSession(game_id=1)
        self.assertFalse(may_speak(self._game("spectator"), fresh, _state("menu"), now))
        self.assertTrue(may_speak(self._game("spectator"), fresh, _state("result", "boss_defeated"), now))
        self.assertTrue(may_speak(self._game("partner"), fresh, _state("map"), now))
        recent = GameSession(game_id=1, remark_count=1, last_remark_at=now - timedelta(minutes=6))
        self.assertTrue(may_speak(self._game("coach"), recent, _state("map"), now))
        self.assertFalse(may_speak(self._game("partner"), recent, _state("map"), now))  # 搭子要隔 8 分钟
        capped = GameSession(game_id=1, remark_count=3)
        self.assertFalse(may_speak(self._game("spectator"), capped, _state("result", "victory"), now))


class GameModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(ScreenCompanionConfig(id=1, enabled=True, allowed_apps='["Code"]', consent_at=datetime.now(timezone.utc)))
            session.add(ToolConfig(id=1, vision_api_key="test-key"))
            session.commit()
            game = create_game(session, {"name": "杀戮尖塔", "app_names": ["SlayTheSpire"], "role": "spectator"})
            self.game_id = game.id
        self.engine_patch = patch.object(screen_companion, "engine", self.engine)
        self.engine_patch.start()
        self.now = datetime.now(timezone.utc)
        self.desktop = FakeDesktop()
        self.companion = ScreenCompanion(clock=lambda: self.now, desktop=self.desktop)
        self.vision = Mock(return_value=_vision_reply("combat"))
        self.llm = AsyncMock(side_effect=self._llm)
        self.remark = '{"say": true, "text": "Boss 倒了。……还行。"}'

    async def _llm(self, session, config, messages, *, purpose, **kwargs):
        text = RECAP if purpose == "game_recap" else self.remark
        return {"text": text, "tool_calls": [], "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "latency_ms": 1}

    def tearDown(self) -> None:
        self.engine_patch.stop()
        self.engine.dispose()

    def _tick(self, *, force: bool = False) -> str:
        with patch.object(screen_companion, "call_vision", self.vision), patch.object(
            play, "chat_completion", self.llm
        ), patch.object(screen_companion, "chat_completion", self.llm):
            return asyncio.run(self.companion.tick(force=force))

    def _later(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)

    def _rows(self) -> list[ScreenObservation]:
        with Session(self.engine) as session:
            return list(session.exec(select(ScreenObservation).order_by(ScreenObservation.id)))

    def _sessions(self) -> list[GameSession]:
        with Session(self.engine) as session:
            return list(session.exec(select(GameSession).order_by(GameSession.id)))

    def _assistant(self) -> list[Message]:
        with Session(self.engine) as session:
            return list(session.exec(select(Message).where(Message.role == "assistant").order_by(Message.id)))

    def test_game_window_is_watched_without_being_on_the_app_list(self) -> None:
        self.assertEqual(self._tick(), "observed")
        (row,) = self._rows()
        self.assertEqual((row.app, row.game_id, row.scene, row.progress), ("杀戮尖塔", self.game_id, "combat", "第 3 幕"))
        self.assertEqual(self.desktop.captured, [41])
        self.assertIn("杀戮尖塔", self.vision.call_args.args[2])
        self.assertEqual(self.companion.status.state, "playing")
        self.assertEqual(len(self._sessions()), 1)

    def test_game_interval_is_shorter_than_general(self) -> None:
        self._tick()
        self._later(1)
        self.assertEqual(self._tick(), "waiting")
        self._later(1.5)
        self.assertEqual(self._tick(), "observed")  # 游戏默认 2 分钟，通用是 5 分钟
        self.assertEqual(len(self._sessions()), 1)

    def test_spectator_speaks_only_after_big_event(self) -> None:
        self.vision.return_value = _vision_reply("menu")
        self.assertEqual(self._tick(), "observed")
        self._later(3)
        self.vision.return_value = _vision_reply("result", "boss_defeated")
        self.assertEqual(self._tick(), "spoke")
        (message,) = self._assistant()
        self.assertEqual(message.content, "Boss 倒了。……还行。")
        self.assertEqual(self._sessions()[0].remark_count, 1)
        system = self.llm.await_args.args[2][0]["content"]
        self.assertIn("你们正在玩《杀戮尖塔》", system)
        self.assertIn("安静观众", system)
        self.assertEqual(self.llm.await_args.kwargs["purpose"], "game_companion")

    def test_session_end_writes_recap_save_card_and_scoped_memories(self) -> None:
        self._tick()
        self._later(3)
        self._tick()
        self.desktop.front = ("WeChat", 9)
        self._later(20)
        self.assertEqual(self._tick(), "not_allowed")
        (game_session,) = self._sessions()
        self.assertIsNotNone(game_session.ended_at)
        self.assertIn("第 3 幕", game_session.recap)
        with Session(self.engine) as session:
            game = session.get(GameProfile, self.game_id)
            self.assertEqual(load_save_card(game)["build"], "力量战")
            memories = session.exec(select(SemanticMemory)).all()
        self.assertEqual({m.scope for m in memories}, {game_scope(self.game_id)})
        self.assertEqual({m.perspective for m in memories}, {"user", "shared"})
        (message,) = self._assistant()
        self.assertEqual(game_session.message_id, message.id)
        self.assertEqual(self.desktop.notes, [message.content])

    def test_short_session_gets_no_recap(self) -> None:
        self._tick()
        self.desktop.front = ("WeChat", 9)
        self._later(20)
        self._tick()
        (game_session,) = self._sessions()
        self.assertIsNotNone(game_session.ended_at)
        self.assertEqual(game_session.recap, "")
        self.assertEqual(self._assistant(), [])

    def test_returning_within_fifteen_minutes_continues_the_session(self) -> None:
        self._tick()
        self._later(10)
        self._tick()
        self.assertEqual(len(self._sessions()), 1)
        self._later(20)
        self.desktop.front = ("WeChat", 9)
        self._tick()
        self.desktop.front = ("SlayTheSpire", 4)
        self._tick()
        self.assertEqual(len(self._sessions()), 2)

    def test_context_and_memory_scope(self) -> None:
        self._tick()
        with Session(self.engine) as session:
            block = game_context_block(session, "随便聊聊", self.now)
            self.assertIn("你们正在玩《杀戮尖塔》", block)
            self.assertIn("血量偏低", block)
            self.assertEqual(message_scope(session, "这把好难", self.now), game_scope(self.game_id))
            session.add(SemanticMemory(perspective="user", type="preference", content="他喜欢走力量流", importance=0.6, scope=game_scope(self.game_id)))
            session.commit()
            inside = retrieve_semantic_memories_scored(session, "力量流", touch=False, scopes={game_scope(self.game_id)})
            outside = retrieve_semantic_memories_scored(session, "力量流", touch=False, scopes=set())
            self.assertGreater(inside[0][0], outside[0][0] + 0.1)
            unrelated = retrieve_semantic_memories_scored(session, "今天有点累晚饭吃什么", touch=False, scopes=set())
            self.assertEqual(unrelated, [])
            playing = retrieve_semantic_memories_scored(session, "今天有点累晚饭吃什么", touch=False, scopes={game_scope(self.game_id)})
            self.assertEqual(len(playing), 1)  # 正在玩这个游戏时，即使话题不相关也记得

    def test_mentioning_a_game_brings_its_save_card(self) -> None:
        with Session(self.engine) as session:
            game = session.get(GameProfile, self.game_id)
            update_game(session, game, {"save_card": {"progress": "进阶 7"}})
            self.assertIn("进阶 7", game_context_block(session, "杀戮尖塔我又卡住了", self.now))
            self.assertEqual(game_context_block(session, "今天吃什么", self.now), "")
            self.assertEqual(message_scope(session, "杀戮尖塔我又卡住了", self.now), game_scope(self.game_id))
            self.assertEqual(message_scope(session, "今天吃什么", self.now), "")
            update_game(session, game, {"aliases": ["尖塔", "STS", "塔"]})
            self.assertEqual(json.loads(game.aliases), ["STS", "尖塔"])  # 单字别名太容易误配，不收
            self.assertIn("进阶 7", game_context_block(session, "说起来尖塔第四幕怎么打", self.now))
            self.assertIn("拿不准就直说", game_context_block(session, "sts 又卡了", self.now))

    def test_deleting_game_keeps_memories_as_general(self) -> None:
        self._tick()
        with Session(self.engine) as session:
            session.add(SemanticMemory(perspective="shared", type="event", content="我们一起打通了", scope=game_scope(self.game_id)))
            session.commit()
            delete_game(session, session.get(GameProfile, self.game_id))
            self.assertEqual(session.exec(select(GameSession)).all(), [])
            self.assertEqual(session.exec(select(ScreenObservation)).all(), [])
            self.assertEqual(session.exec(select(SemanticMemory)).one().scope, "")


if __name__ == "__main__":
    unittest.main()
