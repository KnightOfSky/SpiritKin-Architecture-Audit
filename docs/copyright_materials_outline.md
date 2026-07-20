# SpiritKinAI 软著申请材料大纲

## 软件基本信息
- **软件名称**: SpiritKinAI 个人智能体集群助手系统
- **版本号**: V1.0
- **开发完成日期**: (待填)
- **运行环境**: Windows 10+ / Python 3.12+ / Docker

## 模块说明

| 模块 | 功能 |
|------|------|
| 原子操作抽象层 (backend/action/) | 跨设备最小可审计动作定义：启动/关闭应用、屏幕捕获、剪贴板读写、文件操作、窗口管理、浏览器操作等 30+ 原子操作 |
| 工具注册与执行层 (backend/tools/) | ToolSpec 白名单注册、ToolRegistry 管理、桌面/飞书/OpenClaw/Android/iOS 多域工具集成、MCP 外部工具适配 |
| 复合技能编排模块 (backend/skills/) | SkillSpec/SkillRegistry/SkillRunner，支持多步流程编排、工具白名单校验、参数模板、dry-run 验证、工作流记忆→候选技能自动升级 |
| 智能体集群协调器 (backend/orchestrator/) | AgentCluster 多智能体调度、Planner 路由规划、IntentResolver 意图解析、AgentPerformanceTracker 性能追踪、电商项目状态机 |
| 执行器层 (backend/executors/) | LocalPC/Feishu/OpenClaw/Remote/Android 多设备执行器，统一 ExecutionRequest→ExecutionResult 协议 |
| 设备连接层 (backend/devices/) | LocalPC/Android 设备后端，registry 注册模式 |
| 知识库检索增强模块 (backend/knowledge/) | 文档分块/索引/向量化/关键词检索/重排序/增量索引/Obsidian Vault 连接器 |
| 记忆系统 (backend/memory/) | 短期对话记忆、长期记忆持久化、工作流执行记忆（SQLite/JSONL）、人格状态机、统一记忆编排器 |
| 语音感知模块 (backend/perception/) | Whisper ASR、热词检测、双工会话管理、流式 VAD 框架 |
| 表达模块 (backend/expression/) | pyttsx3/Edge-TTS 语音合成、PhonemeEvent 口型同步协议 |
| 安全与权限中心 (backend/security/) | 策略引擎（glob 匹配）、能力令牌注册、速率限制、审计日志、用户身份解析 |
| 事件系统 (backend/runtime/events/) | 事件持久化、会话回放 |
| 移动接入层 (backend/mobile/) | Android/iOS HTTP 端点、推送队列、快捷指令编目 |
| 评估与验证框架 (backend/evaluation/) | 工作流回放报告、失败样本库、技能验证、审计关联、轨迹分析 |
| 运行时 (backend/app/) | SpiritKinRuntime 主循环、WebSocket 事件桥、HTTP 命令网关 |
| 前端面板 (frontend/) | Capability Dashboard、Live2D 形象页、实时事件流、多端入口 |

## 功能清单
1. 自然语言/语音指令理解与多智能体路由分发
2. 跨设备（本机/远端/Android/iOS）原子操作执行
3. 高风险操作确认门机制
4. 知识库文档检索增强（RAG）与增量索引
5. 工作流记忆记录、回忆与候选技能自动发现
6. 执行轨迹回放与质量评估
7. 权限策略引擎与审计日志
8. 移动端 HTTP 命令网关与实时事件面板
9. Live2D 数字人形象展示与情绪状态同步
10. 长期记忆持久化与人格状态连续性

## 源代码页选择建议
- 核心创新代码：`backend/orchestrator/agent_cluster.py`, `backend/skills/`, `backend/memory/orchestrator.py`
- 原子操作定义：`backend/action/atomic_operations.py`
- 安全审计：`backend/security/policy.py`
- 总计建议提交 60 页以内核心源码

## 注意事项
- 不提交：临时 handoff 文档、smoke 脚本、调试输出、测试替身、模型缓存、第三方库、密钥配置
- 提交前清理：删除 state/ 下运行时数据、.env 中真实密钥
