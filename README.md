# Sandrone Agent

A local-first companion agent with a single continuous timeline, human-like long-term memory, real-time voice calls, a PWA for your phone, and an optional read-only "keep me company while I use the computer / play games" mode. Backend: FastAPI + SQLite; frontend: React + Vite. The default LLM is DeepSeek (any OpenAI-compatible endpoint works). The documentation is in Chinese.

本地优先的陪伴型 Agent：**单一时间线对话 + 类人长期记忆 + 实时语音通话 + 手机 App + 只读的看屏（看一眼 / 陪着看）**。人设为《原神》角色桑多涅的同人设定。

> **免责声明**：这是非商业的同人项目，与 HoYoverse / miHoYo 无关，也未获其认可。仓库不包含任何官方素材、角色立绘、模型或配音；“桑多涅”及相关设定的权利归其权利人所有。语音、形象与 Live2D 素材需要你自备并确保有权使用。

## 功能

- **单一时间线**：没有“多会话”；网页、微信私聊与语音通话写进同一条时间线，换渠道也能接着聊。可删除消息、重新生成最新回复。
- **类人长期记忆**：关于你 / 关于她自己 / 我们之间三个视角，对话后异步提取；检索按字面与意思（可选本地中文向量模型）匹配；遗忘曲线、情绪显著度、记忆联想（1–2 跳）、日记反思与记忆整理；每条回复可展开查看她实际唤起了哪些记忆。API Key 与微信 token 在数据库里加密存放（主密钥在 macOS 钥匙串，其他系统在 `data/secret.key`）；数据库每天自动备份，后端日志写进 `data/logs/`。
- **关系与人设**：好感度、熟悉度、信任度随对话变化，人设随阶段软化；她有连续的情绪、精力与专注状态。
- **实时语音通话**：本地 sherpa-onnx 流式识别 + Silero VAD + Smart Turn v3 语义断句，LLM 流式回复逐句交给本地 GPT-SoVITS 流式合成；可随时打断。音色需自备（见 [`assets/tts/README.md`](assets/tts/README.md)）。
- **形象**：可选 Cubism Live2D 模型或逐帧形象包，口型跟随实时音量、表情跟随情绪标签；有形象包时通话里是类 Live2D 的网格形变（视线跟随鼠标、呼吸、情绪动作）；没有素材时显示剪影（见 [`docs/live2d.md`](docs/live2d.md)、[`docs/character.md`](docs/character.md)）。
- **手机 App（PWA）**：经 Tailscale Serve 的 HTTPS 远程访问，只放行白名单 tailnet 账号；可接收她主动发来的消息推送（见 [`docs/pwa.md`](docs/pwa.md)）。
- **微信私聊与主动消息**（可选）：腾讯 iLink Bot 协议，一次性配对码绑定主人；主动消息默认关闭，开启后她每天在随机时间决定要不要找你说话（见 [`docs/wechat_integration.md`](docs/wechat_integration.md)）。
- **工具**：只读文件（打开后可读主目录，资源库、隐藏文件夹与她自己的数据永远不读，拒绝凭据类文件）、网页搜索与读网页（仿 Claude Code 的 WebSearch/WebFetch：先找来源再只依据网页作答，默认 DuckDuckGo）。
- **智能体中枢**（默认关闭）：把任务派给这台电脑上的 Claude Code、Codex（任务也出现在 ChatGPT 桌面版里）、OpenCode、Hermes——统一走 ACP（Agent Client Protocol），过程实时显示在任务卡上，要权限时在卡上问你；Oh-DSH 在任务卡上的真终端里跑。Claude Code 与 OpenCode 在 sandbox-runtime 沙盒里、Codex 用它自己的权限配置，都读不到私密目录；写入任务在独立的 git 工作区里改，你看过改动再合并或丢弃；先给任务卡，确认后后台执行，做完她读结果用自己的话告诉你（见 [`docs/tools_design.md`](docs/tools_design.md)）。
- **看屏**（只读）：**看一眼**为主——你让她看时截一次图当场回答；**陪着看**可在设置里常开，或聊天/通话中说“这局陪我看着”临时开一段时间（只看当前前台的应用，到点自动停），画面变化才调视觉模型，截图不落盘，首次陪着看需同意截图外发。**游戏是看屏时自动识别的场景**：认出游戏时带上账号档案与可分享的**游戏知识**（规则与高手打法，[`knowledge/games/`](knowledge/games/README.md)，不含个人信息），没建档的游戏自动加进「游戏」页；陪着看时按你选的陪玩角色（记录员/安静观众/搭子/教练）只在间隙说话、玩完写战报，并维护分游戏长期记忆（见 [`docs/screen_companion.md`](docs/screen_companion.md)）。会动键鼠的“帮你操作电脑”不在本仓库。
- **回复速度**：网页对话流式显示；回复时的推理可在设置里选（默认自动：平时关闭，要查资料/读文件/看屏幕时开低）；每轮会变的上下文放在对话历史之后，以提高模型服务的前缀缓存命中；游戏记账在回复后的后台进行。
- **API 用量**：按时间、Provider 与用途查看调用次数、Token、延迟和脱敏错误。

## 目录

```text
backend/     FastAPI 后端（app/、tests/）
frontend/    React + TypeScript + Vite
assets/tts/  GPT-SoVITS 音色放置说明（不含任何音色）
scripts/     一键启停、本地检查、模型下载、图标生成
docs/        设计与使用文档
knowledge/   可分享的游戏知识（每个游戏一份 Markdown）
data/        运行时数据：SQLite、缓存、自备素材（不进 Git）
```

## 快速开始

需要 macOS（看屏、窗口截图与一键脚本依赖 macOS）、Python 3.11+、Node 20+。把 `.env.example` 复制为 `.env` 按需修改。

### macOS 一键启停（推荐）

首次完成下方后端与前端依赖安装后，可直接在 Finder 中双击：

- `start_sandrone.command`：启动 FastAPI 后端和 Vite 前端；TTS 随后端自动管理。
- `stop_sandrone.command`：安全停止由一键脚本启动的前端和后端。

启动成功后打开 `http://127.0.0.1:5173`。PID 与日志保存在 `data/runtime/`，不会进入 Git。重复启动不会创建第二组服务；如果 `8000` 或 `5173` 已被其他进程占用，脚本会提示并退出，不会强制接管或终止该进程。

### 1. 后端

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

不配置 GPT-SoVITS 也能使用文字对话；要用语音请先按 [`assets/tts/README.md`](assets/tts/README.md) 准备你自己的音色，或在 `.env` 设置 `SANDRONE_TTS_MODE=mock` 用提示音联调。

实时语音通话需要额外下载本地识别与断句模型（约 145MB，带 SHA-256 校验）：

```bash
scripts/fetch_voice_models.sh
```

记忆检索可选用本地中文向量模型（bge-small-zh-v1.5，约 95MB，带 SHA-256 校验），按意思而不只按字面想起相关记忆；不下载时只用分词匹配：

```bash
scripts/fetch_memory_model.sh
```

### 2. 前端开发模式

```bash
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。

### 3. 只跑后端（使用已构建的前端）

```bash
cd frontend && npm run build
cd ../backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`。接口文档在 `http://127.0.0.1:8000/docs`。

### 4. 本地检查

```bash
scripts/check.sh
```

在临时 SQLite 与 Mock TTS 下运行后端全部测试、应用导入、前端类型检查与构建，并扫描 Git 禁入项；不会连接真实数据库、微信或 TTS。只改后端时可加 `--skip-frontend`。

## 首次配置

1. 打开「设置」，Provider 选 `DeepSeek`，填 API Key，先点「测试连接」，再「保存配置」。地址、模型、回复思考等在「高级」里，一般不用动。
2. 在「她能做什么」里打开想要的能力：读你的文件、看一眼屏幕、陪着你看、主动找你（上网查资料一直开着）。每项一个开关，打开即生效，不需要填目录或应用名单。
3. 想让她主动发微信：在「微信」里扫码登录并用配对码绑定。
4. 额外文件夹（主目录以外）、不看的应用、看屏频率、主动消息的节奏、视觉模型、搜索服务和子代理都在「高级」里。
5. 没有 Key 时可临时选择「本地 Mock（无 Key 联调）」验证完整链路。

API Key 加密保存在本机 SQLite 中，接口只回传掩码和后 4 位。

让她把任务派给这台电脑上的智能体（「高级」里的子代理）还需要它们的 ACP 接入程序和沙盒：

```bash
npm install -g @agentclientprotocol/claude-agent-acp @agentclientprotocol/codex-acp opencode-ai @anthropic-ai/sandbox-runtime
```

Codex 会用 ChatGPT 桌面版自带的 codex；Hermes 自带 `hermes-acp`（先 `hermes model` 选好模型）；Oh-DSH 用桌面版自带的终端界面。

## 双主体记忆

桑多涅不是用户档案查询器。她有并行的三层长期记忆：

- `perspective=user`：关于用户的事实、偏好、项目。
- `perspective=self`：关于她自己的感受、偏好、自我认知，由她自己巩固，只读。
- `perspective=shared`：关于“我们”的关系、约定与共同经历，默认只读。

此外还有：

- `messages`：不可变情景记忆，记录原始对话和她的状态快照。
- `agent_diary`：她的第一人称日记。完整内容只进她自己的上下文，页面只展示她愿意给用户看的一句话摘要。
- `agent_state`：她连续的情绪、精力、当前专注的事，随对话与反思变化。

```text
每轮对话
  ├─ 原始对话写入 messages（情景记忆，不可变）
  ├─ 她的状态快照随回复保存
  ├─ 关于你 / 关于我 / 我们之间 三层记忆被检索注入工作记忆
  └─ 回复

对话后（后台异步）
  ├─ LLM 从 user / self / shared 三个视角提取候选记忆
  ├─ 规则提取兜底
  ├─ 去重入库，来源可追溯
  ├─ 字面或意思相近的记忆自动建立联想边（memory_links）
  ├─ 每 10 轮或间隔超过 20 小时，她写一篇只属于自己的日记
  └─ 遗忘曲线按重要度衰减；长期没被用户重提、强度过低的记忆进入休眠
```

## 主要接口

后端只接受本机访问：`Host` 必须是 `127.0.0.1` 或 `localhost`；`/api/` 下除 GET 外的请求必须带 `X-Sandrone-Local: 1` 请求头，且 `Origin`（如有）必须是本机。网页前端已自动携带；用 curl 调试时需手动加上该请求头。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/api/chat` | 发送消息 |
| GET | `/api/messages` | 读取单一时间线 |
| GET | `/api/memories` | 查看长期记忆（可按 `perspective` 过滤） |
| POST | `/api/memories/consolidate` | 整理记忆：合并、休眠、建立联想 |
| PATCH | `/api/memories/{id}` | 编辑记忆（仅 `user` 视角） |
| DELETE | `/api/memories/{id}` | 删除（仅 `user` 视角，软删除） |
| GET | `/api/relationship` | 好感度、熟悉度、信任度与变化流水 |
| GET | `/api/diary` | 查看她愿意分享的日记摘要 |
| POST | `/api/diary/reflect` | 让她现在写一篇日记 |
| GET | `/api/llm-config` | 读取 LLM 配置（Key 掩码） |
| PUT | `/api/llm-config` | 保存 LLM 配置 |
| POST | `/api/llm-config/test` | 测试连接 |
| GET | `/api/tools/files/config` | 文件工具配置 |
| PUT | `/api/tools/files/config` | 读文件开关与主目录以外的额外文件夹 |
| POST | `/api/tools/files/read` | 只读读取文件 |
| GET | `/api/tools/search/config` | 搜索工具配置 |
| POST | `/api/tools/search` | 网页搜索 |
| GET | `/api/tools/screen/config` | 看屏 · 看一眼的配置 |
| PUT | `/api/tools/screen/config` | 更新视觉模型配置 |
| POST | `/api/tools/screen/analyze` | 截屏并分析 |
| GET/PUT | `/api/companion/config` | 看屏 · 陪着看的配置（同意截图外发需 `consent: true`，`stop_watch: true` 结束语音临时开） |
| POST | `/api/companion/check` | 立刻看一眼她可以看的窗口 |
| GET/DELETE | `/api/companion/observations` | 她看到的概括 / 清除 |
| GET/POST | `/api/games` | 游戏档案列表 / 添加（必须选陪玩角色） |
| GET/PUT/DELETE | `/api/games/{id}` | 游戏详情（含账号档案、别名）/ 修改 / 删除 |
| GET | `/api/games/{id}/sessions` · `/api/games/{id}/memories` | 场次战报 / 该游戏的长期记忆 |
| GET/POST/DELETE | `/api/games/{id}/milestones` | 账号档案里的里程碑 |
| GET/PUT | `/api/games/{id}/knowledge` | 可分享的游戏知识文件 |
| POST | `/api/games/{id}/research` · `/api/games/{id}/notes` | 让她查资料 / 导入笔记 |
| GET | `/api/tools/agent/config` | 子代理配置与 CLI 检测 |
| PUT | `/api/tools/agent/config` | 更新子代理配置 |
| POST | `/api/tools/agent/run` | 手动派一次只读子代理 |
| GET | `/api/agent-tasks` | 最近的子代理任务卡与结果 |
| GET | `/api/agent-tasks/codex-threads` | 只读列出 Codex App 里的已有线程 |
| POST | `/api/agent-tasks/{id}/confirm` | 确认任务卡并在后台执行（只生效一次） |
| POST | `/api/agent-tasks/{id}/cancel` | 取消任务卡或停止执行 |
| PUT | `/api/agent-tasks/{id}/thread` | 确认前改成接着某个 Codex App 线程或新开会话 |
| GET | `/api/wechat/status` | 微信连接状态 |
| POST | `/api/wechat/login/start` | 获取微信登录二维码 |
| GET | `/api/wechat/login/status` | 查询扫码结果 |
| PUT | `/api/wechat/config` | 手动保存微信凭据 |
| POST | `/api/wechat/pairing/start` | 生成一次性主人配对码（10 分钟有效） |
| POST | `/api/wechat/logout` | 断开微信 |
| GET/PUT | `/api/proactive/config` | 主动消息开关与节奏 |
| GET | `/api/proactive/events` | 最近的主动联系决定记录 |
| POST | `/api/proactive/preview` | 只生成不发送，预览她此刻会说什么 |
| POST | `/api/proactive/send-now` | 立即让她决定并真实发送一条微信 |
| GET | `/api/live2d/status` | Live2D 素材是否就绪（Core、模型、映射） |
| GET | `/api/push/key` | Web Push 公钥 |
| POST | `/api/push/subscribe` · `/api/push/unsubscribe` · `/api/push/test` | 手机通知订阅、取消与测试 |
| GET | `/api/voice/status` | 实时通话识别模型是否就绪 |
| WS | `/api/voice/ws` | 实时语音通话（上行 16kHz PCM16，下行字幕与她的 PCM 音频；只接受本机 Origin） |
| DELETE | `/api/messages/{id}` | 删除一条消息 |
| POST | `/api/messages/{id}/regenerate` | 重新生成最新一条回复（SSE） |
| GET | `/api/tts/status` | 语音资产状态 |
| POST | `/api/tts` | 合成语音（本地 GPT-SoVITS，带缓存） |

## 许可证

代码以 [MIT](LICENSE) 许可发布。第三方依赖遵循各自的许可证；Live2D Cubism Core 与模型、GPT-SoVITS 音色等需自备的素材不在本仓库内，遵循其各自的授权。
