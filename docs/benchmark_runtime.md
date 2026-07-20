# SpiritKin Benchmark Runtime 契约

版本：1.0（2026-07-19）
实现：`backend/evaluation/benchmark_runtime.py`

Benchmark Runtime 是 Growth、模型替换和生产组件 Promotion 的统一测量事实源。它不是执行器；它接收有来源的 Before/After 测量，校验字段、派生总分和差值，并输出 fail-closed Promotion Gate。

支持目标：Model、Agent、Workflow、Skill、Worker、Vision、Runtime、End-to-End、Capability、Tool、Code、Training 和 Prompt。

```json
{
  "benchmark_id": "benchmark-...",
  "target": "growth-workflow-...",
  "target_type": "workflow",
  "version": "2.0",
  "baseline_version": "1.0",
  "dataset": "ecommerce-listing-v1",
  "success_rate": 0.93,
  "latency_ms": 1100,
  "cost": 1.6,
  "retry_count": 2,
  "review_count": 1,
  "quality_score": 89,
  "overall_score": 91.4,
  "promotion_gate": {"status": "passed", "passed": true}
}
```

总分由服务端按 `success_rate * 60 + quality_score * 0.4` 计算。当前最低门槛是成功率 0.80、质量分 70，且 After 的成功率和质量不得低于 Before、总分必须严格提升。成本和延迟保留为可比较指标，但不能抵消质量或成功率回退。

Model 额外要求至少两个不同具名 Jury provider 基于同一报告给出可审计 approve。客户端不能手填 Jury verdict；服务端用受管 Benchmark 生成固定结构的评审请求，经现有多模型 Review Committee 调用后，只接受包含相同 `benchmark_id` 的结构化结果，并校验评审来源去重、置信度和结论。证据不足时模型候选保持 `waiting_jury`。客户端也不能覆盖 candidate target/type、measurement source、overall score 或 Promotion Gate；Growth Review 与 Registry 仍需独立人工治理，所有评测报告固定 `activation_enabled=false`。
