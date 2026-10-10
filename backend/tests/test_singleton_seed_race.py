from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import ProactiveConfig, ToolConfig
from app.services.proactive_service import get_proactive_config
from app.services.tools.config import seed_tool_config


class SingletonSeedRaceTests(unittest.TestCase):
    """模拟两个请求同时发现单行配置缺失：后提交的一方应读取已有行而不是报 500。"""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _race(self, model, seed) -> None:
        with Session(self.engine) as winner:
            winner.add(model(id=1))
            winner.commit()
        with Session(self.engine) as loser:
            real_get = loser.get
            first_lookup = [True]

            def racing_get(*args, **kwargs):
                if first_lookup[0]:
                    first_lookup[0] = False
                    return None  # 看到“还没有这行”，随后插入时撞上对方刚提交的行
                return real_get(*args, **kwargs)

            with patch.object(loser, "get", side_effect=racing_get):
                row = seed(loser)
            self.assertIsNotNone(row)
            self.assertEqual(row.id, 1)

    def test_tool_config_seed_survives_race(self) -> None:
        self._race(ToolConfig, seed_tool_config)

    def test_proactive_config_seed_survives_race(self) -> None:
        self._race(ProactiveConfig, get_proactive_config)


if __name__ == "__main__":
    unittest.main()
