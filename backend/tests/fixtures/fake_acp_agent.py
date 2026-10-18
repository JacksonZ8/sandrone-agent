"""测试用的假 ACP 智能体：按 FAKE_ACP_MODE 走不同剧本（stream / permission / crash）。"""

import json
import os
import sys

MODE = os.environ.get("FAKE_ACP_MODE", "stream")
LOG = os.environ.get("FAKE_ACP_LOG", "")
pending = {}


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(entry):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update(session_id, payload):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": payload}})


print("starting fake agent (stdout noise)", flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method is None:  # 对我们发出的请求的回复（权限）
        log({"permission_reply": message.get("result")})
        result = message.get("result") or {}
        choice = (result.get("outcome") or {}).get("optionId", "cancelled")
        session_id, prompt_id = pending.pop("prompt")
        update(session_id, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": f"权限结果：{choice}"}})
        send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})
        continue
    log({"method": method, "params": message.get("params")})
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
    elif method == "session/new":
        # 模式像 Claude/OpenCode 一样放在 configOptions 里
        mode = {"id": "mode", "currentValue": "default", "options": [{"value": "default"}, {"value": "plan"}]}
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"sessionId": "s-new", "configOptions": [mode]}})
    elif method == "session/set_config_option":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"configOptions": []}})
    elif method == "session/load":
        sid = message["params"]["sessionId"]
        update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "旧会话的回放"}})
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "session/prompt":
        sid = message["params"]["sessionId"]
        if MODE == "crash":
            sys.stderr.write("boom: model not configured\n")
            sys.exit(3)
        update(sid, {"sessionUpdate": "plan", "entries": [{"content": "读代码", "status": "in_progress"}]})
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "读 README.md", "kind": "read", "status": "in_progress"})
        update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
        update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "项目是一个"}})
        update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "陪伴型 Agent。"}})
        if MODE == "permission":
            pending["prompt"] = (sid, message["id"])
            send({"jsonrpc": "2.0", "id": 900, "method": "session/request_permission", "params": {
                "sessionId": sid,
                "toolCall": {"toolCallId": "t2", "title": "运行 npm test", "kind": os.environ.get("FAKE_ACP_KIND", "execute")},
                "options": [
                    {"optionId": "allow", "name": "允许", "kind": "allow_once"},
                    {"optionId": "reject", "name": "拒绝", "kind": "reject_once"},
                ],
            }})
        else:
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
    elif method == "session/cancel":
        pass
    elif "id" in message:
        send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "unknown"}})
