"""记忆召回评测：在数据库副本上，比较纯分词匹配与加上向量相似度后的检索效果。

测试题是私有数据（来自真实记忆），放在 data/eval/memory_recall.json，不进 Git：
  {"cases": [{"query": "今天好累", "relevant": [3, 7]}, ...]}
命中 relevant 里任一条即算命中。只读原库：先用 SQLite 在线备份复制到临时目录再检索。

用法：backend/.venv/bin/python scripts/eval_memory_recall.py [--db data/sandrone_agent.db] [--cases data/eval/memory_recall.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "sandrone_agent.db")
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "eval" / "memory_recall.json")
    parser.add_argument("--limit", type=int, default=10, help="每次检索返回的条数（与对话里一致）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    copy = Path(tempfile.mkdtemp()) / "eval.db"
    source = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    target = sqlite3.connect(copy)
    source.backup(target)
    source.close()
    target.close()
    os.environ["SANDRONE_DATABASE_URL"] = f"sqlite:///{copy}"
    os.environ.setdefault("SANDRONE_TTS_MODE", "mock")
    sys.path.insert(0, str(ROOT / "backend"))

    from sqlmodel import Session, SQLModel

    from app.database import engine
    from app.services.memory import embedding
    from app.services.memory.retrieval import retrieve_semantic_memories_scored

    SQLModel.metadata.create_all(engine)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]

    def run(mode: str) -> dict[str, float]:
        if mode == "lexical":
            embedding._EMBEDDER, embedding._FAILED = None, True
        else:
            embedding._FAILED = False
            if embedding.get_embedder() is None:
                raise SystemExit("没有向量模型：先运行 scripts/fetch_memory_model.sh")
        embedding.embed_query.cache_clear()
        hits = {1: 0, 3: 0, 5: 0}
        reciprocal = 0.0
        with Session(engine) as session:
            for case in cases:
                ranked = [m.id for _, m in retrieve_semantic_memories_scored(session, case["query"], limit=args.limit, touch=False)]
                rank = next((i for i, memory_id in enumerate(ranked) if memory_id in case["relevant"]), None)
                for k in hits:
                    hits[k] += rank is not None and rank < k
                reciprocal += 0 if rank is None else 1 / (rank + 1)
                if args.verbose:
                    shown = "未召回" if rank is None else f"第{rank + 1}"
                    print(f"  [{mode}] {case['query'][:18]:<20} {shown:<5} top3={ranked[:3]}")
        total = len(cases)
        return {"hit@1": hits[1] / total, "hit@3": hits[3] / total, "hit@5": hits[5] / total, "mrr": reciprocal / total}

    results = {mode: run(mode) for mode in ("lexical", "hybrid")}
    print(f"{len(cases)} 道题，每次取前 {args.limit} 条")
    print(f"{'':10}{'hit@1':>8}{'hit@3':>8}{'hit@5':>8}{'MRR':>8}")
    for mode, metrics in results.items():
        print(f"{mode:10}" + "".join(f"{metrics[key]:>8.2f}" for key in ("hit@1", "hit@3", "hit@5", "mrr")))


if __name__ == "__main__":
    main()
