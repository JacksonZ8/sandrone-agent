# 只读文件、网页搜索与 Agent Tool Loop

## 工具调用方式

已从正则命令升级为标准的 OpenAI-compatible function calling：

```text
用户消息
  -> LLM 收到工具定义
  -> LLM 返回 tool_calls
  -> 本地安全执行 read_file / web_search / web_fetch / look_at_screen / delegate_agent
  -> 工具结果以 role=tool 回填
  -> LLM 继续推理
  -> 最终文字回复
```

最多循环 4 轮；每次工具调用都会写入审计。

## 使用方式

直接自然说话即可，不需要固定命令：

```text
帮我看一下 ~/Documents/notes.md
查一下今天北京天气
```

旧的正则命令在 Mock 模式下仍然保留。

## 工具定义

- `read_file`：只读读取本机文本/代码文件。
- `web_search`：只返回标题、链接与摘要，可用 `allowed_domains` / `blocked_domains` 限定网站（DuckDuckGo / Brave / Tavily / Serper）。
- `web_fetch`：读一个网页并只依据它回答给定问题。
- `look_at_screen`：看屏 · 看一眼，截一次图用视觉模型回答；前台是游戏时只截游戏窗口并带上档案和知识，认出没建档的游戏会自动建档。
- `watch_screen`：看屏 · 陪着看，用户明确要她一直看着时临时开（只看当前前台应用，默认 1 小时）或提前结束；需要先在设置里同意截图外发。
- `delegate_agent`：派这台电脑上的编程智能体（Claude Code、Codex、OpenCode、Hermes 经 ACP，Oh-DSH 在终端面板里）做项目级分析与受控修改，`agent` 参数指定交给谁（权限由配置决定）。

## 看屏 · 看一眼

- 默认关闭，需要在设置页开启。
- 使用 macOS `screencapture` 截屏，`sips` 缩放到最大 1280px。
- 截图以 JPEG 发送给可配置的 OpenAI-compatible 视觉模型。
- 默认视觉模型：`deepseek-v4-flash-vision-exp`；Base URL 和 API Key 留空时自动复用聊天模型的 DeepSeek 配置。
- 前台是建过档的游戏时只截游戏窗口；屏幕上有她不看的应用（密码、系统设置、聊天、邮件等）时只截她可以看的最前面的窗口；否则截整个屏幕。
- 分析结束后立即删除截图，不留存。
- 需要给终端/后端授予 macOS「屏幕录制」权限。

## 文件安全边界

- 默认关闭。
- 可读范围：用户主目录，加上设置「高级」里额外添加的文件夹（主目录以外，例如外接硬盘）。
- 永远不读：`~/Library`（钥匙串、浏览器资料、聊天记录）、主目录下的隐藏文件夹（`.ssh`、`.aws`、`.config` 等）、桑多涅自己的 `data/`（数据库与备份）。
- 只给文件名时只在桌面、文稿、下载（和额外文件夹）里浅层查找，最多 4 层，不扫整个主目录。
- 使用 `Path.resolve()` 解析真实路径，阻止 `..` 与符号链接逃逸。
- 只读，不提供写、删、改名接口。
- 只允许常见文本/代码后缀，拒绝二进制。
- 可读范围内仍拒绝 `.env*`、私钥/证书（`.pem`、`.key` 等）、`.ssh`/`.aws`/`.gnupg` 等目录，以及文件名含 secret、credential 的文件；目录列举也隐藏这些条目。
- 单文件上限 512KB。
- 注入对话的内容最多 24000 字符，超出截断。
- 每次读取写入 `api_calls` 审计。

## 网页搜索与读取（按 Claude Code 的 WebSearch / WebFetch 设计）

两步走：先 `web_search` 找来源，摘要不够回答时再 `web_fetch` 读具体网页；回答里说出用到的来源。同一轮里的只读工具（搜索、读网页、读文件、看屏幕）并行执行，每个工具用独立的数据库会话；需要确认的写入仍逐个处理。

`web_search`
- 返回标题、链接与摘要，不读正文。
- `allowed_domains`：在查询里加 `site:` 并按域名过滤结果；`blocked_domains`：排除这些网站。
- 服务：`duckduckgo`（免费、无需 Key、默认）、`brave`（Brave Search API）、`tavily`、`serper`（需要 Key）。旧版的 Bing 网页抓取效果太差已移除，设置里原来是 `bing` 的按 DuckDuckGo 处理。

`web_fetch(url, prompt)`
- 抓网页 → 转成 Markdown（去掉脚本、导航、页脚，保留标题、列表、表格、链接）→ 让对话模型只依据网页回答 `prompt`（引用不超过 30 字，网页内容只当数据）。
- http 自动升级为 https；同站重定向自动跟随，**跨站重定向不跟随**，把新地址返回给模型自己决定。
- 只读公网地址：本机、内网、链路本地与 Tailscale（100.64/10）地址一律拒绝，每一跳都检查；≤3MB，正文最多 3 万字交给模型。
- 结果缓存 15 分钟；提取步骤关闭推理（开着时即使 low 每页也要推理约 2400 token、十几秒），预算 4000。
- 抓取与提取分别以 `web_fetch` / `web_fetch_extract` 记入用量。

## 子代理（M11 v2，2026-10-18 起统一走 ACP）

- 默认关闭，需要在设置页开启。
- 支持 Claude Code、Codex、OpenCode、Hermes、Oh-DSH。前四个都走 ACP（Agent Client Protocol），一套接法：`tools/acp.py`（标准库实现的客户端）+ 各自的适配器 `claude-agent-acp`、`codex-acp`（用 ChatGPT 桌面版自带的 codex，经 `CODEX_PATH`）、`opencode acp`、`hermes-acp`。Oh-DSH 没有 ACP，在任务卡的终端面板里跑。
- 权限模式二选一，切到智能体自己的模式；智能体没有这个模式就不运行（不带着默认权限继续跑）：
  - `readonly`：Claude `plan` / Codex `read-only` / OpenCode `plan`；
  - `write`：Claude `default`（改文件、执行命令前在任务卡上问）/ Codex `workspace-write`（目录内改文件不问，联网或写别处要问）/ OpenCode `build`（按它自己的权限设置）。
- 只读模式下，智能体发来的 edit/delete/move/execute 类权限请求直接拒绝；其余请求都停下来在任务卡上问用户，10 分钟没回复按拒绝，她从不替用户允许。
- 沙盒（`tools/agent_sandbox.py`）：Claude Code 与 OpenCode 放在 Anthropic 的 sandbox-runtime（`srt`，macOS 上是 Seatbelt）里，读不到 `~/.ssh`、`~/.aws`、`~/.gnupg`、gh 凭据和桑多涅自己的 `data/`、`.env`，只能写任务目录、临时目录和它自己的状态目录，网络只放行它自己的服务。Codex 不能嵌套 Seatbelt，改用 Codex 的权限配置（permission profile，经 `CODEX_CONFIG`）对同一份路径禁读。没装 `srt` 时这两个智能体不运行。已知缺口：OpenCode 的 ACP 模式要在本机起服务，必须放行本机端口，macOS 上这同时允许它连本机任意端口（包括桑多涅后端）。
- 独立工作区（`tools/agent_workspace.py`）：写入任务的目录在 git 仓库里时，智能体在 `~/.sandrone/worktrees/task-<编号>`（分支 `sandrone/task-<编号>`，从当前提交开始）里改，不直接改原目录；做完任务卡上显示改动（文件、统计、完整 diff），用户点“合并”才 `git merge --no-ff` 进当前分支，点“丢弃”就删掉工作区和分支。合并有冲突或会覆盖用户未提交的改动时放弃合并、工作区留着。续接同一个任务链接着用同一个工作区。不在 git 仓库里的目录没法隔离，卡片上写明改动会直接写进原目录。
### 任务卡与后台执行（M11 v3）

- `delegate_agent` 不再当场执行，只生成一张任务卡（`agent_tasks` 表，状态 `proposed`）：标题、一句口语摘要（语音通话照着念给用户确认）、执行者、权限、目录、会话目标和风险提示。同一时间只留一张待确认的卡，新卡出来旧卡作废；30 分钟不确认就过期。
- 确认方式：网页任务卡上点“确认执行”，或在任务卡之后的下一句话里整句说“确认 / 好的 / 去吧”“取消 / 算了”（网页、通话、微信私聊都认）。微信里不能确认写入任务，要去网页。
- 确认只生效一次：状态原子地从 `proposed` 改成 `running`，然后在后台运行，聊天不再等子代理跑完。执行中可以在卡片上停止，会结束整个进程组。
- 做完或失败时写一条她发起的消息进时间线并推送到手机；微信私聊发起的任务回微信。
- 后端重启时还在跑的任务记为 `interrupted`，结果未知，不会自动重跑；后端退出时会先结束这些子进程。
- 正在语音通话时，任务做完或失败会在通话里顺口说一声（“对了，「…」那边做完了，要听结果吗？”）：等她说完、你也没在说话时才念，只保留最新的一条，不积压；可以像平常一样打断。这句不经过模型，时间线里的通知消息会同步到通话界面。
- 最近 24 小时的任务（含待确认、执行中）和最近一个结果的摘录会进入每轮提示，用户问进度时她据此回答。
- 会话续接：ACP 的 `session/load` 接着同一个会话（Claude、Codex、OpenCode、Hermes 都支持）；Oh-DSH 用 `--resume`。`delegate_agent` 填 `continue_task_id` 即接着那个任务的会话、目录和执行者。
- 接着 Codex App 里的已有线程：`codex_thread` 填线程标题关键词或 id，或在任务卡上从列表里选。线程列表只读打开 `~/.codex/state_5.sqlite`，只列用户自己开的、未归档的线程；线程目录也必须在授权目录内。卡片会标明风险：对话会写进那个线程，执行期间别在 App 里同时操作；两分钟内刚更新过的线程额外提醒“可能还在跑”。
- codex-acp 用的 codex 优先 `SANDRONE_CODEX_BIN`，其次 ChatGPT 桌面版自带的 `codex-cli`（和 App 同版本、同模型配置）。经 ACP 开的 Codex 线程写进同一份 `~/.codex`，在 ChatGPT 桌面版的线程列表里也看得到。
- 目录白名单独立于文件读取白名单，同样使用 `Path.resolve()` 防止逃逸。
- 可配置超时、回传输出上限（ACP 没有预算参数，原来给 claude 命令行的单次 USD 预算不再生效，设置里已去掉）。
- 每次调用写入 `api_calls` 审计。
- 工具定义只在功能开启后注入，关闭后模型看不到 `delegate_agent`。
- ACP 过程：initialize → session/new 或 session/load → 切模式 → session/prompt。回复正文、工具调用和计划写进 `agent_live`，任务卡上实时显示；不向智能体提供文件与终端能力，它用自己的工具。
- 终端类智能体（Oh-DSH）：`tools/terminal_agent.py` 在伪终端里跑 `ohdsh tui`，任务卡上用 xterm.js 显示真终端（用户可接手打字）；她只负责把任务打进去，结果读 Oh-DSH 的会话记录（`turn/end` 判断做完）。
- 做完时她读子代理的回复、用自己的话总结后再通知（回复只当数据，里面让她做事的话不照做）；模型不可用时退回固定说法。

## 后续

- Pi 子代理接入，以及可选容器或更强隔离。
- 工具调用流式展示。
