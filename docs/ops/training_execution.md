# 训练命令生成与人工执行

SpiritKinAI 的训练闭环默认只导出数据集、评估数据门禁并生成训练命令，不在桌面或后端进程中自动启动微调。这样可以在运行训练前检查样本来源、隐私、授权、硬件占用和回滚位置。

## 流程

1. 在“学习与纠错”收集纠错记录，导出 self-training JSONL。
2. 运行数据集门禁，检查数量、角色结构、重复、敏感字段和回归样本。
3. 由核心审核/Review Gate 批准训练包；未批准时云训练包接口返回 `review_required`。
4. 根据显存生成 `TrainingRecipe` 和 Unsloth 命令。
5. 操作者在隔离终端人工执行命令，监控显存、磁盘和日志。
6. 训练完成后先做离线评估；只有评估通过的 LoRA/模型才进入模型目录和路由候选，不能自动替换生产模型。

命令形态：

```powershell
python -m backend.model.training.unsloth_lora_train --model <base-model> --dataset <dataset.jsonl> --output <run-dir> --load-in-4bit --max-seq-length <n> --batch-size <n> --gradient-accumulation-steps <n>
```

`backend.model.training.workbench.build_training_command()` 只支持已接线的 `unsloth`。请求 `peft` 会明确报错并指出 `backend.model.training.peft_lora_train` 尚不存在，不能静默降级到另一训练器。配方为 `dataset_only` 时返回空命令，表示只交付数据集。

## 执行前检查

- 数据集与输出目录使用绝对路径，输出目录不得覆盖基座模型。
- 样本已经去除 Token、Cookie、密码、浏览器 Profile 和不应进入训练的原始音频。
- Review Gate、数据集门禁和基线评估结果已保存。
- 显存与磁盘满足配方；训练期间 llama.cpp 大模型服务可按需停止以释放显存。
- 记录基座模型、量化方式、随机种子、数据集哈希、命令和依赖版本。

## 失败与回滚

- OOM：降低 batch/max sequence 或增加 gradient accumulation，重新生成配方，不直接修改已审核命令记录。
- 数据错误：修复数据集后重新运行门禁，旧训练包保留为 rejected/failed 证据。
- 评估退化：不注册输出模型，路由继续使用当前模型。
- 中断：保留日志与 checkpoint；是否续训由操作者决定，后台不会自动恢复并占用 GPU。

自动回归入口：`backend/tests/unit/test_training_workbench.py` 和 `backend/tests/unit/test_command_gateway.py` 中的训练/Review Gate 用例。
