# 桑多涅 Agent 记忆设计（双主体版）

## 目标

不是“把聊天记录塞进向量库”，也不是“给用户建档案”，而是让桑多涅拥有接近人的记忆：

- 她有自己的工作记忆、情景记忆、语义记忆、自传记忆和自我状态。
- 记忆分为三个视角：关于用户、关于她自己、关于“我们”。
- 她的自我记忆和日记默认只读，不能被用户像数据库一样改写。
- 对话后的提取、反思与遗忘是异步过程，不阻塞回复。

## 记忆分层

| 层 | 数据 | 实现 |
|---|---|---|
| 工作记忆 | 系统提示词 + 最近 40 条消息 + 三层检索记忆 + 日记片段 | 已实现 |
| 情景记忆 | `messages`，含时间、重要性、情绪正负、她的状态快照 | 已实现 |
| 语义记忆 | `semantic_memories`，含 `perspective`、类型、置信度、强度、访问次数、情绪正负 | 已实现 |
| 自传记忆 | `agent_diary`：她的第一人称日记，完整内容只进她自己的上下文 | 已实现 |
| 自我状态 | `agent_state`：情绪、精力、当前专注、需求 | 已实现 |
| 关系状态 | `relationship_state`：信任、熟悉度 | 建档，后续驱动 |
| 程序记忆 | `prompts/sandrone_persona.md`：官方考据版人格 | 已实现 |

## 三个视角

- `user`：用户是谁、偏好什么、在做什么。用户可编辑、删除。
- `self`：她自己怎么想、在意什么、如何变化。只读。
- `shared`：我们的约定、共同经历、关系变化。只读。

## 记忆循环

```text
编码：每条用户/助手消息 -> messages，并保存她的情绪/精力/专注快照
提取：后台 LLM -> user / self / shared 三视角候选 -> 规则兜底
去重：分词 Jaccard 相似度 >= 0.72 视为重复
检索：max(分词重合 + 子串命中, 向量语义相关度) + 重要度 + 时间衰减 + 访问次数
联想：分词相近（0.32–0.72）或向量余弦 >= 0.70 的记忆连边；每次整理补齐旧记忆之间的边
注入：工作记忆中分组为“关于用户 / 关于你自己 / 你们之间”
自传：每 10 轮或超过 20 小时，她写一篇第一人称日记
纠错：关于用户的记忆可编辑删除；自我与共同记忆只读
```

## 检索评分

```text
lexical   = min(关键词Jaccard + 子串加成, 1)
semantic  = clamp((cos(这句话, 记忆) - 0.42) / (0.68 - 0.42), 0, 1)   # 有本地向量模型时
relevance = max(lexical, semantic)
prior     = (0.22 * importance + 0.12 * exp(-age_days / 21)
             + 0.08 * min(log1p(access_count) / 6, 1) + 0.08 * min(strength, 1)
             + 0.06 * min(|valence|, 1)) / 0.56
score     = 0.65 * relevance + 0.35 * prior
```

以 `backend/app/services/memory/retrieval.py` 为准；字面和语义都不相关、且得分低于 0.25 的记忆不进入候选。

向量模型是本地的 bge-small-zh-v1.5（ONNX，`scripts/fetch_memory_model.sh` 下载，约 95MB）。分词器是纯 Python 实现，与 HuggingFace tokenizers 逐词一致；记忆向量缓存在 `memory_embeddings` 表，内容变了才重算；每轮只给这句话算一次向量（约 2 毫秒）。没有模型时退回纯分词匹配。

阈值和权重在真实记忆上量过：`scripts/eval_memory_recall.py` 用私有测试题（`data/eval/memory_recall.json`，每题一句话和应该想起的记忆，大多与目标没有共同的词）在数据库副本上比较。30 道题、取前 10 条时：

| 检索 | hit@1 | hit@3 | MRR |
|---|---|---|---|
| 原来（分词，相关度权重 0.44） | 0.10 | 0.23 | 0.18 |
| 分词，相关度权重 0.65 | 0.27 | 0.37 | 0.32 |
| 分词 + 向量，权重 0.65 | 0.70 | 0.83 | 0.78 |

原来的权重下，重要度和新近度压过了相关度，最近几条高重要度的记忆几乎每句都排在前三。

## 人格权边界

- `GET /api/diary` 只返回她愿意给用户看的一句话摘要。
- `PATCH/DELETE /api/memories/{id}` 只允许 `perspective=user`。
- 她的日记全文、`agent_state.inner_note` 不通过任何用户接口返回。

## M3 已实现

- Ebbinghaus 遗忘曲线：`strength` 按重要度半衰期衰减；低强度且长期未唤起的记忆进入休眠，不物理删除。
- 情绪显著度：`valence` 参与检索评分，情绪越强的记忆越容易被唤起。
- 反思层：她定期写第一人称日记；日记后自动进行记忆整理。
- 记忆联想：新记忆与相关旧记忆自动建立 `memory_links`。
- 记忆整理：`POST /api/memories/consolidate` 可合并重复、休眠过时/矛盾记忆、建立联想。

## 记忆痕迹（已实现）

每条桑多涅回复保存 `memory_traces_json`，记录本次回答实际唤起了什么：

- `state`：她当时的情绪 / 精力 / 专注；
- `semantic`：被检索注入的长期记忆，含视角、类型与检索得分；
- `diary`：进入上下文的近期日记摘要（只保存她愿意公开的一句话）；
- `episodic`：被唤起的旧对话片段与来源消息 ID。

网页时间线中每条回复下方显示淡色 `回忆 · N`，展开可见完整痕迹；隐藏日记全文不会暴露给用户。

## 海马体式索引 v1（已实现）

- 在原有 `memory_links` 联想边上做 BFS 多跳检索，最多 2 跳；
- 每次命中直接记忆后，会顺藤摸瓜取回关联记忆，作为「联想记忆」注入上下文；
- 联想分 = 边权重 × 记忆重要度 × 时间衰减；
- 记忆痕迹新增 `associative` 层，网页 `回忆 · N` 中可看到是哪条记忆通过什么关系被联想出来。

## 后续方向

- 关键实体图：从记忆里抽实体节点，替代纯记忆到记忆的边。
- 旧对话（情景记忆）的检索仍只用分词匹配，可同样加上向量。
- 主动记忆：她基于日记和未完成的话题，主动发起对话。
- 睡眠式巩固：低峰时段批量整理，而不是每条消息都提取。
- 语音拟人化：音色、情绪、语速和交互速度。

## 参考论文与实现

- [CoALA: Cognitive Architectures for Language Agents](https://arxiv.org/abs/2309.02427)
- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)
- [HippoRAG: Neurobiologically Inspired Long-Term Memory](https://arxiv.org/abs/2405.14831)
- [A Survey on the Memory Mechanism of LLM-based Agents](https://arxiv.org/abs/2404.13501)
- [Human-inspired Perspectives: A Survey on AI Long-term Memory](https://arxiv.org/abs/2411.00489)

## 桑多涅人设来源

- [桑多涅 - 维基百科](https://zh.wikipedia.org/wiki/%E6%A1%91%E5%A4%9A%E6%B6%85)
- [桑多涅 - 百度百科](https://baike.baidu.com/item/%E6%A1%91%E5%A4%9A%E6%B6%85/61716401)
- [桑多涅 - 萌娘百科](https://zh.moegirl.tw/%E6%A1%91%E5%A4%9A%E6%B6%85)
