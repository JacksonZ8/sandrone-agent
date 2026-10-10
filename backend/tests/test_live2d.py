from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import live2d


class Live2DStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(live2d, "LIVE2D_DIR", self.root)
        self.patch.start()
        self.character = patch.object(live2d, "CHARACTER_DIR", self.root / "character")
        self.character.start()
        app = FastAPI()
        app.include_router(live2d.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.character.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def _model(self, relative: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    def test_empty_folder_is_not_ready(self) -> None:
        status = self.client.get("/api/live2d/status").json()
        self.assertFalse(status["ready"])
        self.assertIsNone(status["model_url"])
        self.assertFalse(status["core_present"])

    def test_needs_both_core_and_model(self) -> None:
        self._model("sandrone/sandrone.model3.json")
        self.assertFalse(self.client.get("/api/live2d/status").json()["ready"])
        (self.root / "live2dcubismcore.min.js").write_text("//", encoding="utf-8")
        status = self.client.get("/api/live2d/status").json()
        self.assertTrue(status["ready"])
        self.assertEqual(status["model_url"], "/live2d/sandrone/sandrone.model3.json")
        self.assertEqual(status["core_url"], "/live2d/live2dcubismcore.min.js")

    def test_mapping_selects_model_and_bad_json_is_reported(self) -> None:
        self._model("a/a.model3.json")
        self._model("b/b.model3.json")
        (self.root / "mapping.json").write_text(json.dumps({"model": "b/b.model3.json"}), encoding="utf-8")
        self.assertEqual(self.client.get("/api/live2d/status").json()["model_url"], "/live2d/b/b.model3.json")
        (self.root / "mapping.json").write_text("{oops", encoding="utf-8")
        status = self.client.get("/api/live2d/status").json()
        self.assertEqual(status["model_url"], "/live2d/a/a.model3.json")
        self.assertIn("error", status["mapping"])

    def test_portrait_pack_is_reported(self) -> None:
        self.assertIsNone(self.client.get("/api/live2d/status").json()["portrait_url"])
        pack = self.root / "character" / "portrait"
        pack.mkdir(parents=True)
        (pack / "manifest.json").write_text("{}", encoding="utf-8")
        status = self.client.get("/api/live2d/status").json()
        self.assertEqual(status["portrait_url"], "/character/portrait/manifest.json")
        self.assertFalse(status["ready"])

    def test_films_are_listed_when_present(self) -> None:
        self.assertEqual(self.client.get("/api/live2d/status").json()["films"], {})
        films = self.root / "character" / "films"
        films.mkdir(parents=True)
        (films / "theatre.mp4").write_bytes(b"0")
        status = self.client.get("/api/live2d/status").json()
        self.assertEqual(status["films"], {"theatre": {"video": "/character/films/theatre.mp4", "poster": "/character/films/theatre.jpg"}})

    def test_hidden_folders_are_skipped(self) -> None:
        self._model(".trash/x.model3.json")
        self.assertEqual(self.client.get("/api/live2d/status").json()["models"], [])


class EmotionTagTests(unittest.TestCase):
    def test_streaming_parser_strips_known_tag(self) -> None:
        from app.services.voice.emotion import EmotionTagParser

        parser = EmotionTagParser()
        emotions, text = [], ""
        for delta in ["【得", "意】记性好", "是我的本事。"]:
            emotion, piece = parser.feed(delta)
            if emotion:
                emotions.append(emotion)
            text += piece
        text += parser.flush()
        self.assertEqual(emotions, ["得意"])
        self.assertEqual(text, "记性好是我的本事。")

    def test_no_tag_or_unknown_tag(self) -> None:
        from app.services.voice.emotion import EmotionTagParser, strip_emotion_tag

        parser = EmotionTagParser()
        self.assertEqual(parser.feed("嗯，"), (None, "嗯，"))
        self.assertEqual(strip_emotion_tag("【困惑】嗯？"), (None, "嗯？"))
        self.assertEqual(strip_emotion_tag("[开心] 好呀"), ("开心", "好呀"))


if __name__ == "__main__":
    unittest.main()
