# SpiritKinAI 模型分布建议

当前建议：主链路保持同系列或同服务端，外围能力用混搭模型。

## 当前状态

- 文本默认模型：`openai_compatible` / `qwen/qwen3.6-35b-a3b`，Base URL `http://localhost:1234/v1`。
- 视觉默认模型：`openai_compatible` / `qwen3-vl:4b`，Base URL `http://localhost:11434/v1`。
- 桌面端可配置本地 Provider：Ollama、LM Studio、自定义 OpenAI-compatible。
- Agent 管理默认包含：主 Agent、编程 Agent、视觉 Agent、视频动画 Agent、游戏开发 Agent、电商 Agent、Skill 执行器、外部评审 Agent。

## 推荐分布

| 角色 | 推荐模型策略 | 说明 |
| --- | --- | --- |
| 主 Agent / 路由总控 | 同系列强文本模型 | 保持指令风格、工具调用和安全边界稳定。 |
| 编程 Agent | 同系列代码模型或 Codex CLI | 代码编辑、测试、调试优先要可复现。 |
| 视觉 Agent | 独立视觉模型 | 图像和屏幕理解本来就是专项能力，可以混搭。 |
| 外部评审 Agent | 不同系列强模型 | 用不同模型做交叉审查，降低同源盲点。 |
| RAG embedding | 专用 embedding 模型 | 不建议让聊天模型兼任 embedding。 |
| RAG reranker | 专用 reranker 模型 | 用于知识库召回重排，比直接扩 top_k 更稳。 |
| ASR/TTS | 专项语音模型/服务 | 语音链路和推理模型解耦。 |
| Skill 执行器 | 非 LLM 或小模型 | 执行层应走确定性工具、权限和 Harness。 |

## 同系列 vs 混搭

同系列更适合：

- 主对话、计划、工具调用、确认执行链路。
- 多轮任务需要一致角色设定和状态管理。
- 本地模型能力有限但行为要稳定时。

混搭更适合：

- 视觉、embedding、reranker、ASR/TTS 等专项模型。
- 外部评审、裁判模型、失败归因。
- 高风险执行前的第二意见。
- 长上下文或大文件分析需要临时调用云端强模型。

## 建议 Route Profile

### local_stable

- `main_text`: LM Studio Qwen 文本模型。
- `vision`: Ollama Qwen-VL。
- `embedding`: bge-m3 或 Qwen embedding。
- `reranker`: bge-reranker 或 Qwen reranker。
- 用途：日常本地工作、隐私优先、低成本。

### hybrid_review

- `main_text`: 本地 Qwen 或同系列主模型。
- `programming`: Codex CLI 或代码专用模型。
- `reviewer`: Claude/GPT/DeepSeek/Kimi 任一不同系列强模型。
- `vision`: Qwen-VL。
- 用途：代码修改、复杂任务、执行前复核。

### cloud_strong

- `main_text`: 云端强模型。
- `reviewer`: 另一个不同系列强模型。
- `vision`: 云端或本地视觉模型。
- 用途：长上下文、复杂规划、重要交付。

## 落地建议

1. 先把 `local_stable` 作为默认 Profile，确保日常操作稳定。
2. 为代码和高风险任务启用 `hybrid_review`，让外部评审 Agent 只读或 review-only。
3. 不要把所有 Agent 都绑同一个模型；至少把视觉、embedding、reranker 拆出去。
4. 所有自动执行前仍走权限中心和确认门，不因为模型更强就跳过确认。
5. 每个 Profile 都要能被 Harness replay 验证；没有回放证据的 Profile 不作为默认。

## 配置存储建议

- 模型、Provider、Agent、Route Profile：继续用 JSON。它有层级结构，能表示嵌套成员、权重、开关和备注。
- 批量评测集、人工标注、模型对比结果：可导出 CSV/JSONL。CSV 方便表格检查，JSONL 更适合程序消费。
- 知识库正文：保持原始 Markdown/TXT/PDF 等文件；索引元数据用 JSON manifest；检索向量后续应进入 SQLite/向量库。
