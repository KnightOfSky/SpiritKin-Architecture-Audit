# SpiritKinAI 文档索引

按用途分组的 docs/ 导航。新文档入库前先确认它属于哪一组、是否与现有文档重叠；被取代的文档移入 `archive/` 而不是删除。

## 入口（先读）

| 文档 | 用途 |
| --- | --- |
| [ai_collaboration_context.md](ai_collaboration_context.md) | 跨模型协作上下文与评审记录（外部模型第一入口，`collaboration_agent_worker` 注入） |
| [project_management_overview.md](project_management_overview.md) | 项目协作总览（`backend/app/collaboration.py` / `project_overview.py` 硬引用） |
| [landing_and_test_handoff.md](landing_and_test_handoff.md) | 落地与测试状态总表（与协作上下文部分重叠，待合并收敛） |
| [project_dictionary.md](project_dictionary.md) | 术语词典与命名规范 |

## 现行规范 / 契约

| 文档 | 用途 |
| --- | --- |
| [development_constitution.md](development_constitution.md) | 跨端开发、性能、安全、真实能力与验收的强制约束（修改前先读） |
| [current_architecture_snapshot.md](current_architecture_snapshot.md) | 现行架构地图（代码结构以此为准） |
| [ai_runtime_kernel_spec.md](ai_runtime_kernel_spec.md) | AI Runtime Kernel 稳定概念规范 |
| [runtime_1_0_freeze_audit_2026-07-20.md](runtime_1_0_freeze_audit_2026-07-20.md) | GPT-5.6 Runtime 1.0 Freeze 实现核对、阻断项与进入条件 |
| [growth_runtime.md](growth_runtime.md) | Growth Runtime 候选、谱系升级、Builder、Review 与 Registry 契约 |
| [benchmark_runtime.md](benchmark_runtime.md) | 统一 Before/After、Model Jury 与 Promotion Gate 评测契约 |
| [runtime_host_and_checkpoint.md](runtime_host_and_checkpoint.md) | Runtime Host、租约选主、Checkpoint、Migration 与 Resume 契约 |
| [world_observation_protocol.md](world_observation_protocol.md) | Observation Provider、World State 与三级数据保留契约 |
| [runtime_metadata_contract.md](runtime_metadata_contract.md) | 运行时元数据契约 |
| [trace_event_frontend_contract.md](trace_event_frontend_contract.md) | 前端事件消费契约（生成物见 `backend/app/realtime_contract.py`） |
| [enterprise_module_governance.md](enterprise_module_governance.md) | 模块治理契约（对应 `module_governance.py`） |
| [avatar_3d_animatable_model_pipeline.md](avatar_3d_animatable_model_pipeline.md) | 3D 数字人模型管线约束 |

## 进行中计划 / 路线图

| 文档 | 用途 |
| --- | --- |
| [mainwindow_carve_plan.md](mainwindow_carve_plan.md) | MainWindow 拆分执行计划（进行中） |
| [ecommerce_saas_foundation_plan.md](ecommerce_saas_foundation_plan.md) | 电商多租户 SaaS 基座实施方案（账户层/配额/自助控制台/凭据红线，GPT 执行基准） |
| [agent_cluster_optimal_plan.md](agent_cluster_optimal_plan.md) | Agent 集群主计划 |
| [ecommerce_blueprint_workflow_plan.md](ecommerce_blueprint_workflow_plan.md) | 电商蓝图工作流分层 |
| [remote_control_and_realtime_voice_plan.md](remote_control_and_realtime_voice_plan.md) | 实时语音与远程控制演进 |
| [tool_agent_kb_training_roadmap.md](tool_agent_kb_training_roadmap.md) | Tool / Agent / 知识库 / 训练路线 |
| [project_architecture_and_dev_log.md](project_architecture_and_dev_log.md) | 架构与开发日志（早期记录；现行架构以 snapshot 为准） |

## 运维 / 部署

| 文档 | 用途 |
| --- | --- |
| [light_cloud_control_plane.md](light_cloud_control_plane.md) | 轻云控制面架构决策（`control_plane_worker` 硬引用） |
| [mobile_link_bridge.md](mobile_link_bridge.md) | Android 链路桥规范（同上硬引用） |
| [cloud_deploy_runbook_zh.md](cloud_deploy_runbook_zh.md) | 云部署步骤 |
| [cloud_deploy_smoke_test.md](cloud_deploy_smoke_test.md) | 云部署验收清单 |
| [ios_native_terminal.md](ios_native_terminal.md) | iOS 终端策略 |

## 参考

| 文档 | 用途 |
| --- | --- |
| [model_distribution_plan.md](model_distribution_plan.md) | 模型分布与默认配置建议 |
| [copyright_materials_outline.md](copyright_materials_outline.md) | 软著材料大纲 |

## archive/ — 历史文档（已被取代，仅供追溯）

| 文档 | 取代者 |
| --- | --- |
| archive/codebase_map.md | current_architecture_snapshot.md |
| archive/codex_handoff.md | ai_collaboration_context.md |
| archive/tmp_agent_handoff_2026-04-17.md | ai_collaboration_context.md |
| archive/desktop_enterprise_architecture_plan.md | mainwindow_carve_plan.md（原诊断基于拆分前的单文件 MainWindow） |
| archive/complete_agent_stack_roadmap.md | 进度估算过时，含已弃用 Live2D 语境 |
| archive/live2d_mobile_strategy.md | Live2D 已移出路线图（2026-06-12） |
| archive/data_flywheel_and_kb_policy.md | 原则已并入 snapshot / kernel spec |
| archive/workflow_canvas_multi_agent_reference_notes.md | 修复已落地，仅历史笔记 |
| archive/multi_tenant_account_plan.md | ecommerce_saas_foundation_plan.md（截断草案，独有设计已合并） |
