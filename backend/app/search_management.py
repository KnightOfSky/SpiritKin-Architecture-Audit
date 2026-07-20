from __future__ import annotations

import os
import time
from typing import Any

from backend.app.knowledge_base_management import build_knowledge_base_snapshot, index_all_knowledge_bases
from backend.app.model_catalog import load_model_catalog
from backend.app.settings import (
    resolve_embedding_base_url,
    resolve_embedding_model,
    resolve_embedding_provider,
    resolve_knowledge_backend,
    resolve_reranker_base_url,
    resolve_reranker_model,
    resolve_reranker_provider,
    resolve_web_search_provider,
)
from backend.knowledge.embedding import (
    embedding_services_snapshot,
    hashing_embeddings_enabled,
    reset_embedding_services,
)
from backend.knowledge.embedding_eval import load_latest_embedding_eval_report
from backend.search.providers import build_default_search_provider

SCHEMA_VERSION = "spiritkin.search_management.v1"


MAINSTREAM_CAPABILITY_MATRIX: tuple[dict[str, Any], ...] = (
    {"model": "GPT-5", "provider": "openai", "strengths": ("tool_use", "coding", "multimodal", "reasoning"), "best_for": "总控、工具使用、代码评审、复杂任务执行", "local": False},
    {"model": "Claude Opus 4", "provider": "anthropic", "strengths": ("long_context", "coding_review", "planning", "writing"), "best_for": "长上下文规划、架构评审、代码审查", "local": False},
    {"model": "Gemini 2.5 Pro", "provider": "gemini", "strengths": ("long_context", "multimodal", "video", "reasoning"), "best_for": "长视频/图片/文档混合理解", "local": False},
    {"model": "DeepSeek Reasoner", "provider": "deepseek", "strengths": ("reasoning", "repair", "judge"), "best_for": "推理裁判、失败修复建议", "local": False},
    {"model": "Kimi K2", "provider": "moonshot", "strengths": ("long_context", "agentic_planning"), "best_for": "长项目资料、Agentic 编排", "local": False},
    {"model": "Qwen3 Coder 30B", "provider": "qwen", "strengths": ("coding", "local_open_weight", "tool_use"), "best_for": "本地编程 Agent、代码生成", "local": True},
    {"model": "Qwen3/Qwen2.5 VL", "provider": "qwen", "strengths": ("vision", "screen", "video_frames", "ui_understanding"), "best_for": "屏幕理解、视频帧到 Skill", "local": True},
    {"model": "Qwen3 Embedding / bge-m3", "provider": "qwen/baai", "strengths": ("embedding", "multilingual_rag"), "best_for": "知识库召回、长期记忆", "local": True},
    {"model": "Qwen3 Reranker / bge-reranker", "provider": "qwen/baai", "strengths": ("rerank", "rag_quality"), "best_for": "知识库重排、候选 Skill 排序", "local": True},
)


def build_search_management_snapshot() -> dict[str, Any]:
    web_provider = build_default_search_provider()
    kb = build_knowledge_base_snapshot()
    catalog = load_model_catalog()
    knowledge_backend = resolve_knowledge_backend()
    web_preferred = resolve_web_search_provider()
    embedding_provider = resolve_embedding_provider()
    embedding_model = resolve_embedding_model()
    embedding_base_url = resolve_embedding_base_url()
    hashing_allowed = hashing_embeddings_enabled()
    reranker = resolve_reranker_provider()
    reranker_model = resolve_reranker_model()
    reranker_base_url = resolve_reranker_base_url()
    openai_compatible_local = {
        "openai",
        "openai_compatible",
        "lmstudio",
        "lm-studio",
        "llamacpp",
        "llama_cpp",
        "llama.cpp",
        "llama-cpp",
    }
    configured_embedding = embedding_provider.lower() in openai_compatible_local and bool(embedding_model) and bool(embedding_base_url)
    configured_reranker = reranker.lower() in (openai_compatible_local | {"llm"}) and bool(reranker_model) and bool(reranker_base_url)
    model_roles = _catalog_roles(catalog)
    gaps = _search_gaps(knowledge_backend, embedding_provider, reranker, configured_embedding, configured_reranker, kb)
    knowledge_jobs = dict(kb.get("job_history") or {})
    failed_job_count = int(knowledge_jobs.get("failed_count") or 0)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "status": "needs_attention" if gaps or failed_job_count else "ready",
        "web_search": {
            "provider": getattr(web_provider, "name", web_provider.__class__.__name__),
            "preferred": web_preferred,
            "brave_configured": bool(os.getenv("BRAVE_SEARCH_API_KEY")),
        },
        "knowledge_retrieval": {
            "backend": knowledge_backend,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "embedding_base_url": embedding_base_url,
            "embedding_configured": configured_embedding,
            "embedding_dev_fallback_allowed": hashing_allowed,
            "reranker": reranker,
            "reranker_model": reranker_model,
            "reranker_base_url": reranker_base_url,
            "reranker_configured": configured_reranker,
            "knowledge_base_count": int(kb.get("count") or 0),
            "embedding_runtime": embedding_services_snapshot(),
            "embedding_evaluation": load_latest_embedding_eval_report(),
        },
        "model_capability_matrix": [dict(item) for item in MAINSTREAM_CAPABILITY_MATRIX],
        "catalog_roles": model_roles,
        "knowledge_jobs": knowledge_jobs,
        "missing_capabilities": gaps,
        "recommendations": _recommendations(gaps),
    }


def handle_search_management_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh"}:
        return {"ok": True, "search_management": build_search_management_snapshot()}
    if action in {"index_all_knowledge", "rebuild_knowledge_indexes", "index_unindexed_knowledge"}:
        index_result = index_all_knowledge_bases(only_unindexed=action == "index_unindexed_knowledge")
        return {"ok": bool(index_result.get("ok", True)), "indexing": index_result, "search_management": build_search_management_snapshot()}
    if action == "save_runtime_config":
        updates = _runtime_config_updates(payload)
        return {"ok": True, "updated": updates, "search_management": build_search_management_snapshot()}
    raise ValueError(f"unsupported search management action: {action}")


def _runtime_config_updates(payload: dict[str, Any]) -> dict[str, str]:
    allowed = {
        "web_search_provider": "SPIRITKIN_WEB_SEARCH_PROVIDER",
        "knowledge_backend": "SPIRIT_KNOWLEDGE_BACKEND",
        "embedding_provider": "SPIRITKIN_EMBEDDING_PROVIDER",
        "embedding_model": "SPIRITKIN_EMBEDDING_MODEL",
        "embedding_base_url": "SPIRITKIN_EMBEDDING_BASE_URL",
        "embedding_api_key": "SPIRITKIN_EMBEDDING_API_KEY",
        "embedding_timeout_seconds": "SPIRITKIN_EMBEDDING_TIMEOUT_SECONDS",
        "embedding_query_prefix": "SPIRITKIN_EMBEDDING_QUERY_PREFIX",
        "embedding_document_prefix": "SPIRITKIN_EMBEDDING_DOCUMENT_PREFIX",
        "reranker": "SPIRITKIN_RERANKER_PROVIDER",
        "reranker_model": "SPIRITKIN_RERANKER_MODEL",
        "reranker_base_url": "SPIRITKIN_RERANKER_BASE_URL",
        "reranker_api_key": "SPIRITKIN_RERANKER_API_KEY",
    }
    updated: dict[str, str] = {}
    for key, env_key in allowed.items():
        if key not in payload:
            continue
        value = str(payload.get(key) or "").strip()
        if value:
            os.environ[env_key] = value
        else:
            os.environ.pop(env_key, None)
        updated[env_key] = "***" if "KEY" in env_key and value else value
    if any(key.startswith("embedding_") for key in payload):
        reset_embedding_services()
    return updated


def _catalog_roles(catalog: dict[str, Any]) -> dict[str, int]:
    roles: dict[str, int] = {}
    for item in catalog.get("models") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "unknown")
        roles[role] = roles.get(role, 0) + 1
    return roles


def _search_gaps(
    knowledge_backend: str,
    embedding_provider: str,
    reranker: str,
    configured_embedding: bool,
    configured_reranker: bool,
    kb: dict[str, Any],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if knowledge_backend != "embedding":
        gaps.append({"gap_id": "rag_backend_keyword", "priority": "high", "title": "知识库仍使用 keyword 检索", "detail": "切到 SPIRIT_KNOWLEDGE_BACKEND=embedding 后才能使用向量召回。"})
    if embedding_provider.lower() in {"hashing", ""}:
        fallback_detail = (
            "当前显式允许 hashing 开发降级；它不是语义向量检索。"
            if hashing_embeddings_enabled()
            else "当前未允许 hashing 开发降级，向量 provider 构建会拒绝启动。"
        )
        gaps.append({"gap_id": "embedding_hashing", "priority": "high", "title": "Embedding 仍是 hashing 占位", "detail": f"{fallback_detail} 配置 SPIRITKIN_EMBEDDING_PROVIDER=llamacpp/openai_compatible 和真实 embedding 模型。"})
    elif not configured_embedding:
        gaps.append({"gap_id": "embedding_not_configured", "priority": "medium", "title": "Embedding Provider 未完整配置", "detail": "缺少 SPIRITKIN_EMBEDDING_MODEL 或 base_url。"})
    if reranker.lower() in {"token_overlap", "overlap", ""}:
        gaps.append({"gap_id": "reranker_token_overlap", "priority": "medium", "title": "Reranker 仍是 token-overlap", "detail": "配置 SPIRITKIN_RERANKER_PROVIDER=llamacpp/openai_compatible 和 reranker/chat 模型。"})
    elif not configured_reranker:
        gaps.append({"gap_id": "reranker_not_configured", "priority": "medium", "title": "Reranker 未完整配置", "detail": "缺少 SPIRITKIN_RERANKER_MODEL 或 base_url。"})
    unindexed = [
        item
        for item in kb.get("knowledge_bases") or []
        if isinstance(item, dict) and bool(item.get("enabled", True)) and not dict(item.get("last_index") or {}).get("updated_at")
    ]
    if unindexed:
        gaps.append({"gap_id": "kb_unindexed", "priority": "medium", "title": "部分知识库未索引", "detail": f"{len(unindexed)} 个知识库缺少最近索引记录。"})
    job_history = dict(kb.get("job_history") or {})
    failed_count = int(job_history.get("failed_count") or 0)
    if failed_count:
        last_error = str(job_history.get("last_error") or "查看索引/同步任务历史获取失败详情。")
        gaps.append({"gap_id": "kb_job_failures", "priority": "medium", "title": "知识索引/同步任务存在失败", "detail": f"{failed_count} 个历史任务失败。最近错误：{last_error}"})
    return gaps


def _recommendations(gaps: list[dict[str, Any]]) -> list[str]:
    if not gaps:
        return ["Search/RAG 已具备基础可用配置。"]
    return [
        "设置 SPIRIT_KNOWLEDGE_BACKEND=embedding，把 AgentCluster 的 KB 检索切到向量召回。",
        "启动 llama.cpp embedding 服务，并设置 SPIRITKIN_EMBEDDING_PROVIDER=llamacpp、SPIRITKIN_EMBEDDING_MODEL。",
        "配置 SPIRITKIN_RERANKER_PROVIDER=llamacpp 和 reranker/chat 模型，用于候选重排。",
        "保留 Brave/DuckDuckGo 作为 Web search provider，Web 搜索不是模型能力。",
    ]
