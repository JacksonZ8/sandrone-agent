from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import config as app_config
from app.models import GameMilestone, GameProfile, GameSession, Message, ScreenObservation, SemanticMemory
from app.services.games import bookkeeping, knowledge, play
from app.services.games.account import add_milestone, list_milestones
from app.services.games.play import GameState, game_context_block, record_event_milestone, write_recap
from app.services.games.profiles import create_game, delete_game, load_save_card

KNOWLEDGE = """# 《杀戮尖塔》游戏知识
> 最后整理：2026-10-10；涉及版本：未注明
## 概览
爬塔卡牌肉鸽，共四幕。
## 规则与机制
每回合 3 点能量[1]。
## 关卡与 Boss
腐化之心 750 血[2]，每回合最多受到 300 伤害[2]；死亡节拍：每打出一张牌失去 1 点生命[2]。
## 高手思路
7 回合内结束心脏战[2]。
## 版本与改动
暂无资料
## 来源
- [1] 新手指南 — https://example.org/basics
- [2] 腐化之心 — https://example.org/heart
"""


def _llm(*texts: str) -> AsyncMock:
    return AsyncMock(
        side_effect=[
            {"text": text, "tool_calls": [], "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "latency_ms": 1}
            for text in texts
        ]
    )


class KnowledgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(app_config, "GAMES_DIR", root / "data-games"),
            patch.object(app_config, "KNOWLEDGE_DIR", root / "knowledge" / "games"),
        ]
        for item in self.patches:
            item.start()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.game = create_game(
            self.session,
            {"name": "杀戮尖塔", "app_names": ["SlayTheSpire"], "aliases": ["尖塔", "Slay the Spire"], "role": "coach"},
        )
        self.root = root

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def run_with(self, coro, *texts: str, target=knowledge) -> AsyncMock:
        llm = _llm(*texts)
        with patch.object(target, "chat_completion", llm):
            self.result = asyncio.run(coro)
        return llm


class KnowledgeFileTests(KnowledgeTestCase):
    def test_file_lives_in_repo_dir_named_after_english_alias(self) -> None:
        self.run_with(knowledge.import_note(self.session, self.game, "笔记", "能量每回合 3 点。"), json.dumps({"general": "每回合 3 点能量", "personal": {}}), KNOWLEDGE)
        self.assertEqual(self.game.knowledge_file, "slay-the-spire.md")
        path = self.root / "knowledge" / "games" / "slay-the-spire.md"
        self.assertTrue(path.read_text(encoding="utf-8").startswith("# 《杀戮尖塔》游戏知识"))
        self.assertEqual(self.result["file"], "knowledge/games/slay-the-spire.md")
        # 原始资料与备份只在本机 data 目录
        self.assertTrue((self.root / "data-games" / str(self.game.id) / "sources" / "001-note.md").is_file())

    def test_note_is_split_into_knowledge_and_account(self) -> None:
        split = json.dumps({"general": "心脏每回合最多受 300 伤害。", "personal": {"progress": "进阶 7", "roster": "铁甲战士"}}, ensure_ascii=False)
        llm = self.run_with(knowledge.import_note(self.session, self.game, "", "我进阶7了，用铁甲。心脏每回合最多受300伤害。"), split, KNOWLEDGE)
        card = load_save_card(self.game)
        self.assertEqual((card["progress"], card["roster"]), ("进阶 7", "铁甲战士"))
        self.assertTrue(self.result["account_updated"])
        consolidate_input = llm.await_args_list[1].args[2][1]["content"]
        self.assertIn("心脏每回合最多受 300 伤害", consolidate_input)
        self.assertNotIn("进阶7", consolidate_input)
        self.assertIn("绝不写任何个人信息", llm.await_args_list[1].args[2][0]["content"])

    def test_purely_personal_note_does_not_touch_knowledge(self) -> None:
        llm = self.run_with(knowledge.import_note(self.session, self.game, "", "我今天抽到了"), json.dumps({"general": "", "personal": {"roster": "新角色"}}, ensure_ascii=False))
        self.assertEqual(llm.await_count, 1)
        self.assertEqual(knowledge.read_knowledge(self.game), "")

    def test_private_lines_never_reach_the_shared_file(self) -> None:
        knowledge.ensure_knowledge_file(self.session, self.game)
        knowledge.write_knowledge(self.game, KNOWLEDGE + "\n我的 UID: 123456789 记得加好友\n")
        self.assertNotIn("123456789", knowledge.read_knowledge(self.game))

    def test_bad_consolidation_does_not_overwrite(self) -> None:
        knowledge.ensure_knowledge_file(self.session, self.game)
        knowledge.write_knowledge(self.game, KNOWLEDGE)
        with self.assertRaises(RuntimeError):
            self.run_with(knowledge.import_note(self.session, self.game, "", "x"), json.dumps({"general": "某条", "personal": {}}), "抱歉，我不能整理。")
        self.assertEqual(knowledge.read_knowledge(self.game), KNOWLEDGE)

    def test_excerpt_and_context(self) -> None:
        knowledge.ensure_knowledge_file(self.session, self.game)
        knowledge.write_knowledge(self.game, KNOWLEDGE)
        excerpt = knowledge.knowledge_excerpt(self.game, "心脏 Boss 怎么打")
        self.assertIn("750 血[2]", excerpt)
        self.assertIn("[2] 腐化之心 — https://example.org/heart", excerpt)
        self.assertNotIn("暂无资料", excerpt)
        self.assertIn("爬塔卡牌肉鸽", knowledge.knowledge_excerpt(self.game, "今天天气"))
        block = game_context_block(self.session, "尖塔的心脏 Boss 怎么打")
        self.assertIn("750 血", block)

    def test_deleting_a_game_keeps_the_shared_file(self) -> None:
        knowledge.ensure_knowledge_file(self.session, self.game)
        knowledge.write_knowledge(self.game, KNOWLEDGE)
        add_milestone(self.session, self.game.id, "击败心脏", kind="manual")
        delete_game(self.session, self.session.get(GameProfile, self.game.id))
        self.assertTrue((self.root / "knowledge" / "games" / "slay-the-spire.md").is_file())
        self.assertFalse((self.root / "data-games" / str(self.game.id)).exists())
        self.assertEqual(self.session.exec(select(GameMilestone)).all(), [])


class ResearchTests(KnowledgeTestCase):
    def _fetch(self, answers: dict[str, dict]) -> AsyncMock:
        async def fake(session, url, prompt):
            return answers[url]

        return AsyncMock(side_effect=fake)

    def test_research_reads_pages_in_parallel_and_skips_irrelevant(self) -> None:
        results = [
            {"title": "心脏", "url": "https://wiki.example/heart", "snippet": ""},
            {"title": "无关", "url": "https://slang.example/slay", "snippet": ""},
            {"title": "跳转", "url": "https://jump.example/x", "snippet": ""},
        ]
        fetch = self._fetch({
            "https://wiki.example/heart": {"ok": True, "url": "https://wiki.example/heart", "title": "腐化之心", "answer": "- 750 血；每回合最多受 300 伤害；死亡节拍每张牌 1 伤害。" * 2},
            "https://slang.example/slay": {"ok": True, "url": "https://slang.example/slay", "title": "slay", "answer": "无关"},
            "https://jump.example/x": {"ok": False, "url": "https://jump.example/x", "redirect": "https://wiki.example/act4", "error": "跳转"},
            "https://wiki.example/act4": {"ok": True, "url": "https://wiki.example/act4", "title": "第四幕", "answer": "- 第四幕需要红蓝绿三把钥匙才能进入，否则直接结局。" * 2},
        })
        with patch.object(knowledge, "search_web_with_audit", Mock(side_effect=[results, []])), patch.object(knowledge, "web_fetch", fetch), patch.object(knowledge, "is_public_url", return_value=True):
            llm = self.run_with(knowledge.research(self.session, self.game, "心脏打法"), KNOWLEDGE)
        self.assertEqual([s["url"] for s in self.result["added"]], ["https://wiki.example/heart", "https://wiki.example/act4"])
        self.assertEqual(self.result["failed"][0]["error"], "页面没有讲这个游戏的相关内容")
        self.assertIn("高水平玩家的打法思路", fetch.await_args_list[0].args[2])
        self.assertIn("资料[2] 第四幕", llm.await_args.args[2][1]["content"])

    def test_falls_back_to_duckduckgo(self) -> None:
        good = [{"title": "腐化之心", "url": "https://wiki.example/heart", "snippet": ""}]
        search = Mock(side_effect=[[], [], good, []])
        fetch = self._fetch({"https://wiki.example/heart": {"ok": True, "url": "https://wiki.example/heart", "title": "腐化之心", "answer": "- 心脏 750 血，每回合伤害上限 300。" * 2}})
        from app.models import ToolConfig

        self.session.add(ToolConfig(id=1, search_provider="brave", search_api_key="k"))
        self.session.commit()
        with patch.object(knowledge, "search_web_with_audit", search), patch.object(knowledge, "web_fetch", fetch):
            self.run_with(knowledge.research(self.session, self.game, "心脏"), KNOWLEDGE)
        self.assertEqual(search.call_args_list[2].args[2].search_provider, "duckduckgo")
        self.assertEqual(self.result["added"][0]["url"], "https://wiki.example/heart")


class AccountTests(KnowledgeTestCase):
    def _bookkeep(self, user_text: str, llm_text: str, tool_calls: list | None = None):
        user = Message(role="user", content=user_text, turn_index=1)
        assistant = Message(role="assistant", content="记下了。", turn_index=1, tool_calls_json=json.dumps(tool_calls or []))
        self.session.add_all([user, assistant])
        self.session.commit()
        llm = _llm(llm_text)
        with patch.object(bookkeeping, "chat_completion", llm), patch.object(knowledge.threading, "Thread") as thread:
            result = bookkeeping.run_game_bookkeeping(self.session, user, assistant)
        return result, llm, thread

    def test_background_bookkeeping_updates_account_and_notes_knowledge(self) -> None:
        decision = json.dumps({
            "save_card": {"progress": "进阶 8", "bogus": "x"},
            "milestone": "第一次过进阶 7",
            "knowledge": [{"text": "心脏第 4 回合会清除负面并获得人工制品。", "source_url": "https://wiki.example/heart"}],
        }, ensure_ascii=False)
        fetched = [{"name": "web_fetch", "arguments": {"url": "https://wiki.example/heart", "prompt": "心脏"}}]
        result, llm, thread = self._bookkeep("尖塔进阶 7 过了！心脏第四回合会清负面吧", decision, fetched)
        self.assertEqual(result, {"updated": ["progress"], "milestone": True, "knowledge": 1})
        self.assertEqual(load_save_card(self.session.get(GameProfile, self.game.id))["progress"], "进阶 8")
        self.assertEqual([row.text for row in list_milestones(self.session, self.game.id)], ["第一次过进阶 7"])
        (source,) = knowledge.list_sources(self.game)
        self.assertEqual((source["kind"], source["pending"], source["url"]), ("chat", True, "https://wiki.example/heart"))
        thread.return_value.start.assert_called_once()
        prompt = llm.await_args.args[2][0]["content"]
        self.assertIn("https://wiki.example/heart", prompt)
        self.assertEqual((llm.await_args.kwargs["purpose"], llm.await_args.kwargs["thinking"]), ("game_bookkeeping", "low"))

    def test_bookkeeping_skips_turns_not_about_a_game(self) -> None:
        result, llm, _ = self._bookkeep("今天好累", "{}")
        self.assertIsNone(result)
        llm.assert_not_awaited()

    def test_pending_notes_are_consolidated(self) -> None:
        knowledge.save_source(self.game, "chat", "聊天中记下", "", "心脏会清负面", pending=True)
        self.run_with(knowledge.consolidate_pending(self.session, self.game), KNOWLEDGE)
        self.assertFalse(knowledge.list_sources(self.game)[0]["pending"])

    def test_big_events_become_milestones_once(self) -> None:
        now = datetime.now(timezone.utc)
        state = GameState(scene="result", event="boss_defeated", progress="第 3 幕 Boss", summary="打赢了", detail="")
        record_event_milestone(self.session, self.game, state, None, now)
        record_event_milestone(self.session, self.game, state, None, now + timedelta(minutes=3))
        record_event_milestone(self.session, self.game, GameState("combat", "none", "", "在打", ""), None, now)
        (row,) = list_milestones(self.session, self.game.id)
        self.assertEqual(row.text, "击败 Boss：第 3 幕 Boss")

    def test_recap_updates_account_milestones_and_knowledge(self) -> None:
        now = datetime.now(timezone.utc)
        game_session = GameSession(game_id=self.game.id, started_at=now - timedelta(minutes=30), last_seen_at=now)
        self.session.add(game_session)
        self.session.commit()
        self.session.refresh(game_session)
        for scene in ("combat", "result"):
            self.session.add(ScreenObservation(app="杀戮尖塔", summary=f"在{scene}", game_id=self.game.id, session_id=game_session.id, scene=scene, created_at=now))
        self.session.commit()
        recap = json.dumps({
            "recap": "心脏倒了。",
            "save_card": {"progress": "通关进阶 7"},
            "milestones": ["首次击败腐化之心"],
            "knowledge": ["心脏每 3 回合强化一次，拖得越久越危险"],
            "memories": [{"perspective": "shared", "type": "event", "content": "我们一起打倒了心脏", "importance": 0.7, "confidence": 0.8}],
        }, ensure_ascii=False)
        llm_recap = _llm(recap)
        llm_knowledge = _llm(KNOWLEDGE)
        with patch.object(play, "chat_completion", llm_recap), patch.object(knowledge, "chat_completion", llm_knowledge):
            asyncio.run(write_recap(self.session, self.game, game_session))
        self.assertEqual(load_save_card(self.game)["progress"], "通关进阶 7")
        self.assertEqual([row.text for row in list_milestones(self.session, self.game.id)], ["首次击败腐化之心"])
        self.assertIn("心脏每 3 回合强化一次", llm_knowledge.await_args.args[2][1]["content"])
        self.assertEqual(knowledge.list_sources(self.game)[0]["kind"], "play")
        self.assertEqual(self.session.exec(select(SemanticMemory)).one().perspective, "shared")
        recap_prompt = llm_recap.await_args.args[2][0]["content"]
        self.assertIn("不含他的任何个人信息", recap_prompt)


if __name__ == "__main__":
    unittest.main()
