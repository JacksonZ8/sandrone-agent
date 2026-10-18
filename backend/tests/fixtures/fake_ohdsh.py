"""测试用的假 Oh-DSH 终端界面：ohdsh tui --inline --cwd X [--resume ID]。

启动时在 FAKE_OHDSH_ROOT 下建会话记录（zstd 压缩的 jsonl），打一段“开场动画”；
每收到一行输入就记一轮 user/message → assistant/message → turn/end。FAKE_OHDSH_MODE=exit 时启动就退出。
"""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

args = sys.argv[1:]
if os.environ.get("FAKE_OHDSH_MODE") == "exit":
    print("Oh-DSH: no model configured")
    sys.exit(2)
cwd = args[args.index("--cwd") + 1]
session_id = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
folder = Path(os.environ["FAKE_OHDSH_ROOT"]) / ("-" + cwd.replace("/", "-") + "--") / session_id
folder.mkdir(parents=True, exist_ok=True)
record = folder / "session.jsonl"
events = [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else [{"type": "session"}]


def save():
    record.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
    subprocess.run(["zstd", "-q", "-f", str(record), "-o", str(folder / "session.jsonl.zstd")], check=True)


save()
for frame in range(3):
    print(f"Oh-DSH TUI 探索未至之境！{'.' * frame}", flush=True)
    time.sleep(0.1)
print("> ", end="", flush=True)
for line in sys.stdin:
    text = line.strip()
    turn = max([e.get("data", {}).get("turn", 0) for e in events if e.get("type") == "turn/start"] or [0]) + 1
    events.append({"type": "turn/start", "data": {"turn": turn}})
    events.append({"type": "user/message", "data": {"content": [{"type": "text", "text": text}], "role": "user"}})
    save()
    print(f"\r\n思考中……（{text}）", flush=True)
    time.sleep(0.5)
    events.append({"type": "assistant/message", "data": {"turn": turn, "message": {"role": "assistant", "content": [{"type": "text", "text": "先看看。"}]}}})
    events.append({"type": "assistant/message", "data": {"turn": turn, "message": {"role": "assistant", "content": [{"type": "text", "text": f"第 {turn} 轮：收到「{text}」，已完成。"}]}}})
    events.append({"type": "turn/end", "data": {"turn": turn, "reason": {"kind": "completed"}}})
    save()
    print("完成。\r\n> ", end="", flush=True)
