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
- `look_at_screen`：截取当前屏幕并用视觉模型分析。
- `delegate_agent`：派 Claude Code 或 Codex CLI 子代理做项目级分析与受控修改（M11 v2，权限由配置决定）。

## 屏幕观察

- 默认关闭，需要在设置页开启。
- 使用 macOS `screencapture` 截屏，`sips` 缩放到最大 1280px。
- 截图以 JPEG 发送给可配置的 OpenAI-compatible 视觉模型。
- 默认视觉模型：`deepseek-v4-flash-vision-exp`；Base URL 和 API Key 留空时自动复用聊天模型的 DeepSeek 配置。
- 截取的是整个屏幕而不是单个窗口；外发前不做裁剪，设置页已明确提示。
- 分析结束后立即删除截图，不留存。
- 需要给终端/后端授予 macOS「屏幕录制」权限。

## 文件安全边界

- 默认关闭。
- 只允许访问设置页配置的目录白名单。
- 使用 `Path.resolve()` 解析真实路径，阻止 `..` 与符号链接逃逸。
- 只读，不提供写、删、改名接口。
- 只允许常见文本/代码后缀，拒绝二进制。
- 白名单内仍拒绝 `.env*`、私钥/证书（`.pem`、`.key` 等）、`.ssh`/`.aws`/`.gnupg` 等目录，以及文件名含 secret、credential 的文件；目录列举也隐藏这些条目。
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

## 子代理（M11 v2）

- 默认关闭，需要在设置页开启。
- 支持 `claude` 与 `codex` 两个 CLI。
- 当前通过本机子进程在授权目录中运行，不宣称 Docker 隔离。
- 权限模式二选一：
  - `readonly`：claude `--permission-mode plan` / codex `--sandbox read-only`；
  - `write`：claude `--permission-mode acceptEdits` / codex `--sandbox workspace-write`。
- 写入模式允许修改授权工作目录内的文件；Codex 的 workspace-write 由 macOS Seatbelt 沙箱约束。
- 网页端每次写入调用前弹出确认卡，显示目录与任务摘要；允许/拒绝后模型继续，超时自动拒绝。
- 微信等非网页渠道暂不支持写入确认，写入请求会被拒绝并让模型提示用户改用网页。
- 目录白名单独立于文件读取白名单，同样使用 `Path.resolve()` 防止逃逸。
- 可配置超时、回传输出上限、单次 USD 预算。
- 每次调用写入 `api_calls` 审计。
- 工具定义只在功能开启后注入，关闭后模型看不到 `delegate_agent`。

## 后续

- Pi 子代理接入，以及可选容器或更强隔离。
- 工具调用流式展示。
