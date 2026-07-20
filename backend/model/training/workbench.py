from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HardwareProfile:
    gpu_name: str = ""
    vram_gb: int = 0
    system_ram_gb: int = 0
    cuda_available: bool = True

    @property
    def is_small_vram(self) -> bool:
        return self.vram_gb > 0 and self.vram_gb <= 8

    @property
    def is_mid_vram(self) -> bool:
        return 8 < self.vram_gb <= 16


@dataclass(frozen=True)
class TrainingRecipe:
    method: str
    base_model_hint: str
    max_model_size: str
    quantization: str
    batch_size: int
    gradient_accumulation_steps: int
    max_seq_length: int
    target_modules: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "base_model_hint": self.base_model_hint,
            "max_model_size": self.max_model_size,
            "quantization": self.quantization,
            "batch_size": self.batch_size,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "max_seq_length": self.max_seq_length,
            "target_modules": list(self.target_modules),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class TrainingDatasetExport:
    path: str
    count: int
    task_types: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {"path": self.path, "count": self.count, "task_types": dict(self.task_types)}


def recommend_training_recipe(profile: HardwareProfile) -> TrainingRecipe:
    if not profile.cuda_available:
        return TrainingRecipe(
            method="dataset_only",
            base_model_hint="remote_or_cloud_gpu",
            max_model_size="n/a",
            quantization="n/a",
            batch_size=0,
            gradient_accumulation_steps=0,
            max_seq_length=0,
            target_modules=(),
            notes=("当前环境无 CUDA，建议只导出训练集，在云端或其他 GPU 环境训练。",),
        )

    if profile.is_small_vram:
        return TrainingRecipe(
            method="qlora",
            base_model_hint="Qwen2.5-1.5B-Instruct or Qwen2.5-3B-Instruct",
            max_model_size="3B",
            quantization="4bit",
            batch_size=1,
            gradient_accumulation_steps=16,
            max_seq_length=2048,
            target_modules=("q_proj", "k_proj", "v_proj", "o_proj", "up_proj", "down_proj", "gate_proj"),
            notes=(
                "8GB 显存优先训路由、工具调用和风格，不建议训 7B。",
                "关闭长上下文，先用高质量小数据跑通 eval 改进。",
            ),
        )

    if profile.is_mid_vram:
        return TrainingRecipe(
            method="qlora",
            base_model_hint="Qwen2.5-3B-Instruct or Qwen2.5-7B-Instruct",
            max_model_size="7B",
            quantization="4bit",
            batch_size=1,
            gradient_accumulation_steps=8,
            max_seq_length=4096,
            target_modules=("q_proj", "k_proj", "v_proj", "o_proj", "up_proj", "down_proj", "gate_proj"),
            notes=(
                "16GB 显存可做 7B QLoRA，但建议先用 3B 快速迭代数据质量。",
                "优先训练 tool routing、失败兜底、项目语气和结构化输出。",
            ),
        )

    return TrainingRecipe(
        method="lora",
        base_model_hint="Qwen2.5-7B-Instruct or similar instruct model",
        max_model_size="7B+",
        quantization="8bit_or_16bit",
        batch_size=2,
        gradient_accumulation_steps=8,
        max_seq_length=4096,
        target_modules=("q_proj", "k_proj", "v_proj", "o_proj", "up_proj", "down_proj", "gate_proj"),
        notes=("显存较充裕时仍建议先保持 LoRA，不要直接全参微调。",),
    )


def detect_local_hardware_profile(environ: dict[str, str] | None = None) -> HardwareProfile:
    environ = environ or os.environ
    gpu_name = str(environ.get("SPIRITKIN_TRAIN_GPU_NAME") or environ.get("CUDA_VISIBLE_DEVICES") or "")
    vram_raw = str(environ.get("SPIRITKIN_TRAIN_VRAM_GB") or environ.get("SPIRITKIN_VRAM_GB") or "16").strip()
    ram_raw = str(environ.get("SPIRITKIN_SYSTEM_RAM_GB") or "0").strip()
    cuda_raw = str(environ.get("SPIRITKIN_TRAIN_CUDA") or "1").strip().lower()
    try:
        vram_gb = int(float(vram_raw))
    except ValueError:
        vram_gb = 16
    try:
        system_ram_gb = int(float(ram_raw))
    except ValueError:
        system_ram_gb = 0
    if not gpu_name:
        gpu_name = "RTX 5060 Ti"
    return HardwareProfile(
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        system_ram_gb=system_ram_gb,
        cuda_available=cuda_raw not in {"0", "false", "no", "off"},
    )


def _normalize_training_example(example: dict[str, Any]) -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "system",
                "content": "你是 SpiritKinAI 的自我改进训练样本。根据输入学习更稳定的路由、工具调用和失败处理。",
            },
            {"role": "user", "content": str(example.get("input_text") or "")},
            {"role": "assistant", "content": str(example.get("expected_behavior") or "")},
        ],
        "metadata": {
            "example_id": example.get("example_id", ""),
            "source": example.get("source", ""),
            "task_type": example.get("task_type", ""),
            "weight": example.get("weight", 1.0),
            **dict(example.get("metadata") or {}),
        },
    }


def export_self_training_dataset(training_package, output_path: str | Path) -> TrainingDatasetExport:
    snapshot = training_package.snapshot() if hasattr(training_package, "snapshot") else dict(training_package or {})
    examples = list(snapshot.get("examples") or [])
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    task_types: dict[str, int] = {}
    with path.open("w", encoding="utf-8") as fh:
        for example in examples:
            normalized = _normalize_training_example(dict(example))
            task_type = str(normalized["metadata"].get("task_type") or "unknown")
            task_types[task_type] = task_types.get(task_type, 0) + 1
            fh.write(json.dumps(normalized, ensure_ascii=False) + "\n")

    return TrainingDatasetExport(path=str(path), count=len(examples), task_types=task_types)


def build_training_command(
    *,
    dataset_path: str,
    output_dir: str,
    base_model: str,
    recipe: TrainingRecipe,
    trainer: str = "unsloth",
) -> list[str]:
    if recipe.method == "dataset_only":
        return []

    if trainer == "unsloth":
        return [
            "python",
            "-m",
            "backend.model.training.unsloth_lora_train",
            "--model",
            base_model,
            "--dataset",
            dataset_path,
            "--output",
            output_dir,
            "--load-in-4bit" if recipe.quantization == "4bit" else "--no-4bit",
            "--max-seq-length",
            str(recipe.max_seq_length),
            "--batch-size",
            str(recipe.batch_size),
            "--gradient-accumulation-steps",
            str(recipe.gradient_accumulation_steps),
        ]

    raise ValueError(
        f"unsupported trainer '{trainer}': only 'unsloth' is wired; "
        "PEFT training is intentionally not exposed until backend.model.training.peft_lora_train exists"
    )
