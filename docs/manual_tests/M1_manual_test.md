# M1 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / Python 3.12 / llama.cpp b10058
- 状态：本机隔离链路通过；自然长期对话样本保留为体验复核项

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 真实 Embedding | Nomic `:8081` 健康，768 维请求成功 | 通过 |
| 2 | 对话模型不可达降级 | provider 降级、维度切换重索引和状态可观测用例通过 | 通过 |
| 3 | 一周前偏好召回 | 隔离目录写入七日前偏好，重启加载后由 768 维 llama.cpp Embedding 命中同一记忆，DMAE 激活度升至 active 并注入 Soul 提示词 | 通过 |
| 4 | 进程重启持久化 | 写入、销毁实例、重新加载、召回和提示词捕获在独立脚本内连续完成 | 通过 |

- 禁改区抽查：Safety 双重校验通过。
- 证据：`backend/tests/unit/test_memory_semantics.py`、`scripts/smoke_memory_relationship.py`、`tmp/memory-relationship-smoke-20260718/smoke-report.json`。
- 结论：方案规定的一周前偏好召回已用受控真实数据通过；自然用户数据仅作为后续体验复核，不再阻塞实现验收。
