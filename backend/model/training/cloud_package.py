from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.model.training.workbench import (
    TrainingRecipe,
    build_training_command,
    detect_local_hardware_profile,
    recommend_training_recipe,
)

DEFAULT_CLOUD_TRAINING_DIR = "state/cloud_training_packages"


@dataclass(frozen=True)
class CloudTrainingPackage:
    package_id: str
    package_dir: str
    manifest_path: str
    dataset_path: str
    base_model: str
    output_dir: str
    command: list[str]
    recipe: dict[str, Any]
    notes: str = ""
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "package_dir": self.package_dir,
            "manifest_path": self.manifest_path,
            "dataset_path": self.dataset_path,
            "base_model": self.base_model,
            "output_dir": self.output_dir,
            "command": list(self.command),
            "command_text": " ".join(self.command),
            "recipe": dict(self.recipe),
            "notes": self.notes,
            "created_at": self.created_at,
        }


def resolve_cloud_training_dir(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_CLOUD_TRAINING_DIR", DEFAULT_CLOUD_TRAINING_DIR)
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def _safe_id(value: str) -> str:
    normalized = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
    return normalized or f"train-{int(time.time())}"


def build_cloud_training_package(
    *,
    dataset_path: str | os.PathLike[str],
    base_model: str,
    package_id: str | None = None,
    package_root: str | os.PathLike[str] | None = None,
    adapter_output_dir: str = "outputs/spiritkin-lora",
    recipe: TrainingRecipe | None = None,
    notes: str = "",
) -> CloudTrainingPackage:
    source_dataset = Path(dataset_path)
    if not source_dataset.is_absolute():
        source_dataset = Path.cwd() / source_dataset
    source_dataset = source_dataset.resolve()
    if not source_dataset.exists():
        raise FileNotFoundError(f"dataset not found: {source_dataset}")

    safe_package_id = _safe_id(package_id or f"cloud-train-{int(time.time())}")
    target_dir = resolve_cloud_training_dir(package_root) / safe_package_id
    target_dir.mkdir(parents=True, exist_ok=True)
    dataset_target = target_dir / "train.jsonl"
    if source_dataset != dataset_target:
        shutil.copyfile(source_dataset, dataset_target)

    selected_recipe = recipe or recommend_training_recipe(detect_local_hardware_profile())
    command = build_training_command(
        dataset_path="train.jsonl",
        output_dir=adapter_output_dir,
        base_model=base_model,
        recipe=selected_recipe,
    )
    manifest = {
        "schema_version": "spiritkin.cloud_training_package.v1",
        "package_id": safe_package_id,
        "created_at": time.time(),
        "base_model": base_model,
        "dataset": "train.jsonl",
        "adapter_output_dir": adapter_output_dir,
        "recipe": selected_recipe.snapshot(),
        "command": command,
        "command_text": " ".join(command),
        "recommended_cloud_flow": [
            "Upload this package directory to a GPU host.",
            "Create a Python environment with unsloth, trl, datasets and model dependencies.",
            "Run the command from this package directory.",
            "Download the adapter output directory and register it as a LoRA adapter in the local model runtime.",
        ],
        "notes": notes,
    }
    manifest_path = target_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = target_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# SpiritKinAI Cloud Training Package",
                "",
                "This directory is self-contained for a LoRA/QLoRA training run.",
                "",
                "## Run",
                "",
                "```bash",
                " ".join(command) if command else "# dataset-only recipe; train on another compatible stack",
                "```",
                "",
                "## Return Artifact",
                "",
                f"Download `{adapter_output_dir}` after training and register it in the desktop model provider/runtime.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return CloudTrainingPackage(
        package_id=safe_package_id,
        package_dir=str(target_dir),
        manifest_path=str(manifest_path),
        dataset_path=str(dataset_target),
        base_model=base_model,
        output_dir=adapter_output_dir,
        command=command,
        recipe=selected_recipe.snapshot(),
        notes=notes,
        created_at=float(manifest["created_at"]),
    )
