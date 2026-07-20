from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import parse, request

from backend.state_store import resolve_state_path

DEFAULT_MODEL_CATALOG_PATH = "state/model_catalog.json"
HF_MODEL_API = "https://huggingface.co/api/models/{model_id}"


CURATED_BASE_MODELS: tuple[dict[str, Any], ...] = (
    {
        "model_id": "claude-opus-4",
        "provider": "anthropic",
        "role": "chief_coordinator",
        "domain": "long_context_planning_coding_review",
        "size_class": "cloud",
        "priority": 108,
        "source_url": "https://docs.anthropic.com/",
        "notes": "云端长上下文/复杂规划候选；适合总控计划、代码评审和长文档分析。",
    },
    {
        "model_id": "gemini-2.5-pro",
        "provider": "gemini",
        "role": "chief_coordinator",
        "domain": "long_context_multimodal_planning",
        "size_class": "cloud",
        "priority": 106,
        "source_url": "https://ai.google.dev/gemini-api/docs",
        "notes": "云端长上下文和多模态候选；适合大上下文规划、文档/视频/图片混合任务。",
    },
    {
        "model_id": "gpt-5",
        "provider": "openai",
        "role": "chief_coordinator",
        "domain": "planning_tool_use_code_review",
        "size_class": "cloud",
        "priority": 105,
        "source_url": "https://platform.openai.com/docs/models",
        "notes": "云端总控/工具使用/代码评审候选；通过 OpenAI-compatible adapter 接入。",
    },
    {
        "model_id": "deepseek-reasoner",
        "provider": "deepseek",
        "role": "reviewer",
        "domain": "reasoning_repair_judge",
        "size_class": "cloud",
        "priority": 101,
        "source_url": "https://api-docs.deepseek.com/",
        "notes": "推理/裁判/修复建议候选，适合作为外部评审 Agent。",
    },
    {
        "model_id": "qwen3.7-max",
        "provider": "alibaba_cloud",
        "role": "chief_coordinator",
        "domain": "cloud_reasoning_planning",
        "size_class": "cloud",
        "priority": 100,
        "source_url": "https://www.alibabacloud.com/",
        "notes": "Qwen3.7-Max 属于云端专有候选，不是本地开源基底；适合通过 API adapter 参与混合评审。",
    },
    {
        "model_id": "moonshotai/Kimi-K2-Instruct",
        "role": "chief_coordinator",
        "domain": "long_context_agentic_planning",
        "size_class": "MoE",
        "priority": 99,
        "notes": "长上下文与 Agentic 编排候选；适合超长项目资料、知识库和计划任务。",
    },
    {
        "model_id": "zai-org/GLM-4.5",
        "role": "chief_coordinator",
        "domain": "agentic_reasoning_tool_use",
        "size_class": "MoE",
        "priority": 98,
        "notes": "开源/开放权重 Agentic 推理候选，可作为 Qwen 之外的总控或专业 Agent 基底。",
    },
    {
        "model_id": "meta-llama/Llama-3.3-70B-Instruct",
        "role": "general_agent",
        "domain": "general_reasoning_dialogue",
        "size_class": "70B",
        "priority": 96,
        "notes": "通用开源基底候选，适合云端或大显存机器作为通用 Agent。",
    },
    {
        "model_id": "mistralai/Mistral-Large-Instruct-2411",
        "role": "general_agent",
        "domain": "general_reasoning_multilingual",
        "size_class": "large",
        "priority": 94,
        "notes": "通用推理和多语种候选，可作为非 Qwen 的服务端专业 Agent。",
    },
    {
        "model_id": "Qwen/Qwen3.6-35B-A3B-Instruct",
        "role": "general_agent",
        "domain": "local_or_cloud_open_weight_reasoning",
        "size_class": "35B-A3B",
        "priority": 93,
        "notes": "Qwen3.6 开源/开放权重候选；只作为混合矩阵一员，不作为唯一默认。",
        "architecture": "MoE",
        "parameter_hint_b": 36,
        "active_parameter_hint_b": 3,
        "quantization_profiles": ["Q4_K_M", "Q5_K_M", "FP8"],
        "local_role_policy": "scheduler_master_candidate",
    },
    {
        "model_id": "Qwen/Qwen3.6-27B",
        "role": "programming_agent",
        "domain": "code_game_ui_reasoning_specialist",
        "size_class": "27B",
        "priority": 91,
        "notes": "Qwen3.6 27B dense specialist candidate; use for coding/game/UI specialist roles only after local scheduler benchmarks pass.",
        "architecture": "dense",
        "parameter_hint_b": 27.3,
        "context_tokens": 262144,
        "supports_tools": True,
        "supports_vision": True,
        "quantization_profiles": ["Q4_K_M", "Q5_K_M", "FP8"],
        "local_role_policy": "27b_specialist_candidate",
    },
    {
        "model_id": "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "role": "chief_coordinator",
        "domain": "routing_planning_dialogue",
        "size_class": "30B-A3B",
        "priority": 92,
        "notes": "Qwen 系列总控候选；如你本地实测上下文不足，应降级为备选而不是唯一主模型。",
    },
    {
        "model_id": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
        "role": "programming_agent",
        "domain": "code_edit_debug_review",
        "size_class": "30B-A3B",
        "priority": 90,
        "notes": "编程 Agent 本地/开源候选；云端可用 Claude/GPT/DeepSeek/Kimi 交叉评审。",
    },
    {
        "model_id": "Qwen/Qwen3-VL-8B-Instruct",
        "role": "vision_agent",
        "domain": "screen_image_video_understanding",
        "size_class": "8B",
        "priority": 88,
        "notes": "视觉 Agent 候选；用于屏幕理解、图片/视频帧分析和 UI 状态解析。",
    },
    {
        "model_id": "Qwen/Qwen3-Embedding-8B",
        "role": "embedding",
        "domain": "rag_memory_workflow_recall",
        "size_class": "8B",
        "priority": 86,
        "notes": "RAG、长期记忆、workflow 召回的 embedding 候选。",
    },
    {
        "model_id": "Qwen/Qwen3-Reranker-8B",
        "role": "reranker",
        "domain": "rag_rerank_workflow_candidate_rerank",
        "size_class": "8B",
        "priority": 83,
        "notes": "知识库和 workflow 候选重排模型。",
    },
    {
        "model_id": "BAAI/bge-m3",
        "role": "embedding",
        "domain": "rag_memory_multilingual_embedding",
        "size_class": "embedding",
        "priority": 87,
        "notes": "成熟多语种 embedding 备选。",
    },
    {
        "model_id": "BAAI/bge-reranker-v2-m3",
        "role": "reranker",
        "domain": "rag_rerank",
        "size_class": "reranker",
        "priority": 85,
        "notes": "成熟 reranker 备选。",
    },
    {
        "model_id": "nomic-ai/nomic-embed-text-v1.5",
        "role": "embedding",
        "domain": "rag_memory_embedding",
        "size_class": "embedding",
        "priority": 79,
        "notes": "轻量 embedding 备选，适合本地知识库召回。",
    },
    {
        "model_id": "openai/whisper-large-v3-turbo",
        "role": "asr",
        "domain": "speech_to_text",
        "size_class": "asr",
        "priority": 78,
        "notes": "语音识别候选；用于 faster-whisper 或同类本地 ASR。",
    },
    {
        "model_id": "zai-org/AutoGLM-Phone-9B-Multilingual",
        "role": "gui_agent_reference",
        "domain": "mobile_gui_agent",
        "size_class": "9B",
        "priority": 64,
        "notes": "手机 GUI Agent 方向参考，不直接替代安全内核。",
    },
)


@dataclass(frozen=True)
class ModelCatalogEntry:
    model_id: str
    role: str
    domain: str
    priority: int
    size_class: str = ""
    provider: str = "huggingface"
    source_url: str = ""
    notes: str = ""
    refreshed_at: float = 0.0
    online: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "role": self.role,
            "domain": self.domain,
            "priority": self.priority,
            "size_class": self.size_class,
            "provider": self.provider,
            "source_url": self.source_url,
            "notes": self.notes,
            "refreshed_at": self.refreshed_at,
            "online": self.online,
            "metadata": dict(self.metadata),
        }


def resolve_model_catalog_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_MODEL_CATALOG_PATH", DEFAULT_MODEL_CATALOG_PATH, path)


def _curated_entry(raw: dict[str, Any], *, refreshed_at: float = 0.0) -> ModelCatalogEntry:
    model_id = str(raw.get("model_id") or "").strip()
    return ModelCatalogEntry(
        model_id=model_id,
        role=str(raw.get("role") or "general"),
        domain=str(raw.get("domain") or "general"),
        priority=int(raw.get("priority") or 50),
        size_class=str(raw.get("size_class") or ""),
        provider=str(raw.get("provider") or "huggingface"),
        source_url=str(raw.get("source_url") or (f"https://huggingface.co/{model_id}" if model_id else "")),
        notes=str(raw.get("notes") or ""),
        refreshed_at=refreshed_at,
        online=False,
        metadata={k: v for k, v in raw.items() if k not in {"model_id", "provider", "role", "domain", "priority", "size_class", "source_url", "notes"}},
    )


def bundled_model_catalog() -> dict[str, Any]:
    return {
        "schema_version": "spiritkin.model_catalog.v1",
        "source": "bundled",
        "updated_at": 0.0,
        "online": False,
        "models": [_curated_entry(item).snapshot() for item in CURATED_BASE_MODELS],
        "failures": [],
    }


def load_model_catalog(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_model_catalog_path(path)
    if not target.exists():
        return bundled_model_catalog()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return bundled_model_catalog()
    return payload if isinstance(payload, dict) else bundled_model_catalog()


def save_model_catalog(payload: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_model_catalog_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def fetch_huggingface_model_info(model_id: str, *, timeout: float = 8.0) -> dict[str, Any]:
    encoded = parse.quote(model_id, safe="")
    req = request.Request(HF_MODEL_API.format(model_id=encoded), headers={"User-Agent": "SpiritKinAI model catalog"})
    with request.urlopen(req, timeout=max(1.0, timeout)) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _merge_online_info(entry: ModelCatalogEntry, info: dict[str, Any], *, refreshed_at: float) -> ModelCatalogEntry:
    metadata = dict(entry.metadata)
    for key in (
        "id",
        "sha",
        "lastModified",
        "downloads",
        "likes",
        "pipeline_tag",
        "library_name",
        "tags",
        "private",
        "gated",
        "disabled",
    ):
        if key in info:
            metadata[key] = info.get(key)
    siblings = info.get("siblings")
    if isinstance(siblings, list):
        metadata["file_count"] = len(siblings)
        metadata["files"] = [str(item.get("rfilename") or "") for item in siblings[:16] if isinstance(item, dict)]
    return ModelCatalogEntry(
        model_id=entry.model_id,
        role=entry.role,
        domain=entry.domain,
        priority=entry.priority,
        size_class=entry.size_class,
        provider=entry.provider,
        source_url=entry.source_url,
        notes=entry.notes,
        refreshed_at=refreshed_at,
        online=True,
        metadata=metadata,
    )


def refresh_model_catalog(
    model_ids: list[str] | None = None,
    *,
    save: bool = True,
    path: str | os.PathLike[str] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    refreshed_at = time.time()
    selected_ids = {item.strip() for item in model_ids or [] if str(item).strip()}
    entries = [_curated_entry(item, refreshed_at=refreshed_at) for item in CURATED_BASE_MODELS]
    if selected_ids:
        known = {entry.model_id for entry in entries}
        entries = [entry for entry in entries if entry.model_id in selected_ids]
        for model_id in sorted(selected_ids - known):
            entries.append(
                ModelCatalogEntry(
                    model_id=model_id,
                    role="custom",
                    domain="custom",
                    priority=50,
                    source_url=f"https://huggingface.co/{model_id}",
                    refreshed_at=refreshed_at,
                    notes="用户指定的自定义模型。",
                )
            )

    refreshed: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for entry in entries:
        if entry.provider != "huggingface":
            refreshed.append(entry.snapshot())
            continue
        try:
            info = fetch_huggingface_model_info(entry.model_id, timeout=timeout)
            refreshed.append(_merge_online_info(entry, info, refreshed_at=refreshed_at).snapshot())
        except Exception as exc:
            failures.append({"model_id": entry.model_id, "error": f"{type(exc).__name__}: {exc}"})
            refreshed.append(entry.snapshot())

    payload = {
        "schema_version": "spiritkin.model_catalog.v1",
        "source": "huggingface_api",
        "updated_at": refreshed_at,
        "online": len(failures) < len(refreshed) if refreshed else False,
        "models": sorted(refreshed, key=lambda item: int(item.get("priority") or 0), reverse=True),
        "failures": failures,
    }
    if save:
        save_model_catalog(payload, path)
    return payload
