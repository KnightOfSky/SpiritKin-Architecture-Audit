using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Effects;

namespace SpiritKinDesktop;

internal static class UiDisplayText
{
    public static string Status(string status)
    {
        var value = (status ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "active" => "已启用",
            "enabled" => "已启用",
            "disabled" => "已关闭",
            "inactive" => "已关闭",
            "draft" => "草稿",
            "candidate" => "候选",
            "ready" => "就绪",
            "needs_attention" => "需关注",
            "blocked" => "阻塞",
            "running" => "运行中",
            "pending" => "待处理",
            "complete" => "已完成",
            "completed" => "已完成",
            "failed" => "失败",
            "failed_threshold" => "未达阈值",
            "not_configured" => "未配置",
            "passed" => "已通过",
            "rejected" => "已拒绝",
            "archived" => "已归档",
            "review_required" => "待审核",
            "approved" => "已通过",
            "missing" => "缺失",
            "skipped" => "已跳过",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Risk(string risk)
    {
        var value = (risk ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "high" => "高风险",
            "medium" => "中风险",
            "low" => "低风险",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Priority(string priority)
    {
        var value = (priority ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "high" => "高优先",
            "medium" => "中优先",
            "low" => "低优先",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Domain(string domain)
    {
        var value = (domain ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "general" => "通用",
            "programming" => "编程",
            "vision" => "视觉",
            "vision_model" => "视觉模型",
            "video" => "视频",
            "video_animation" => "视频动画",
            "game_development" => "游戏开发",
            "ecommerce" => "电商",
            "skill" => "Skill",
            "skill_runner" => "Skill Runner",
            "review" => "审核",
            "main_text" => "主文本",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Role(string role)
    {
        var value = (role ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "primary" => "主模型",
            "specialist" => "专家",
            "reviewer" => "评审",
            "planner" => "规划",
            "assistant" => "助手",
            "tool" => "工具",
            "primary_text" => "主文本",
            "local_model" => "本地模型",
            "cloud_api" => "云端模型",
            "vision" => "视觉",
            "coding" => "代码",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Provider(string provider)
    {
        var value = (provider ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "openai" => "OpenAI",
            "openai_compatible" => "OpenAI 兼容",
            "cloud_openai_compatible" => "自定义云模型",
            "anthropic" => "Anthropic",
            "gemini" => "Gemini",
            "deepseek" => "DeepSeek",
            "moonshot" => "Moonshot",
            "qwen" => "Qwen",
            "qwen/baai" => "Qwen / BAAI",
            "ollama" => "Ollama",
            "lmstudio" => "LM Studio",
            "lm-studio" => "LM Studio",
            "llamacpp" => "llama.cpp",
            "llama_cpp" => "llama.cpp",
            "llama.cpp" => "llama.cpp",
            "llama-cpp" => "llama.cpp",
            "hashing" => "本地占位 Embedding",
            "token_overlap" => "词元重叠重排",
            "duckduckgo" => "DuckDuckGo",
            "brave" => "Brave Search",
            "yundun" => "云顿兼容接口",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string ProviderList(string providers)
    {
        var values = (providers ?? "")
            .Split(new[] { ',', ';', '|' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(Provider)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
        return values.Length == 0 ? Provider(providers ?? "") : string.Join(" / ", values);
    }

    public static string Framework(string framework)
    {
        var value = (framework ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "native" => "Native",
            "spiritkin_native" => "SpiritKin Native",
            "codex_or_native" => "Codex / Native",
            "langgraph" => "LangGraph",
            "langgraph_candidate" => "LangGraph 候选",
            "crewai" => "CrewAI",
            "crewai_or_native" => "CrewAI / Native",
            "skill_runner" => "Skill Runner",
            "remote_or_api" => "Remote / API",
            "spiritkin_remote_worker" => "Remote Worker",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string SkillAssistMode(string mode)
    {
        var value = (mode ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "human_review" => "人工评审",
            "auto_suggest" => "自动建议",
            "auto_apply" => "自动应用",
            "disabled" => "关闭",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string ArtifactType(string artifactType)
    {
        var value = (artifactType ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "paper" => "论文",
            "video" => "视频",
            "trace" => "运行轨迹",
            "failure" => "失败样本",
            "dataset" => "训练样本",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string ServiceLevel(string sla)
    {
        var value = (sla ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "weekly review" => "每周复盘",
            "daily review" => "每日复盘",
            "release gate" => "发布门禁",
            "always-on" => "持续监控",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Criticality(string criticality)
    {
        var value = (criticality ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "critical" => "关键",
            "high" => "高重要",
            "medium" => "中重要",
            "low" => "低重要",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Maturity(string maturity)
    {
        var value = (maturity ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "emerging" => "建设中",
            "managed" => "已管理",
            "controlled" => "受控",
            "mature" => "成熟",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Posture(string posture)
    {
        var value = (posture ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "controlled" => "受控",
            "attention" => "需关注",
            "blocked" => "阻塞",
            _ => Status(value),
        };
    }

    public static string SearchStrength(string strength)
    {
        var value = (strength ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "",
            "tool_use" => "工具调用",
            "coding" => "代码",
            "multimodal" => "多模态",
            "reasoning" => "推理",
            "long_context" => "长上下文",
            "coding_review" => "代码评审",
            "planning" => "规划",
            "writing" => "写作",
            "video" => "视频",
            "repair" => "修复",
            "judge" => "裁判",
            "agentic_planning" => "Agent 规划",
            "local_open_weight" => "本地开源权重",
            "screen" => "屏幕理解",
            "video_frames" => "视频帧",
            "ui_understanding" => "界面理解",
            "embedding" => "向量召回",
            "multilingual_rag" => "多语种 RAG",
            "rerank" => "重排",
            "rag_quality" => "RAG 质量",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string KnowledgeBackend(string backend)
    {
        var value = (backend ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "keyword" => "关键词检索",
            "embedding" => "向量检索",
            "local" => "本地检索",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string RouteStrategy(string strategy)
    {
        var value = (strategy ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "primary_with_specialists" => "主模型 + 专家",
            "committee_review" => "多模型评审",
            "fallback_chain" => "降级链",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string SharedScope(string scope)
    {
        var value = (scope ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "agent" => "单 Agent",
            "domain" => "领域共享",
            "global" => "全局共享",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string SourceType(string source)
    {
        var value = (source ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "human" => "人工创建",
            "paper" => "论文导入",
            "video" => "视频导入",
            "runtime" => "运行沉淀",
            "model" => "模型生成",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string Kind(string kind)
    {
        var value = (kind ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" => "--",
            "native" => "Native",
            "framework" => "专业框架",
            "cli" => "命令行助手",
            "mcp" => "MCP 助手",
            "api" => "API 助手",
            "remote" => "远端 Worker",
            "desktop" => "桌面助手",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string ShortTechnical(string value, int max = 28)
    {
        var text = (value ?? "").Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return "--";
        }
        max = Math.Max(4, max);
        if (text.Length <= max)
        {
            return text;
        }
        return $"{text[..Math.Min(text.Length, Math.Max(1, max - 3))]}...";
    }

    public static string HumanizeIdentifier(string value, string fallback)
    {
        var text = (value ?? "").Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return fallback;
        }
        var tokens = Regex.Split(text, @"[._\-\s/]+")
            .Where(token => !string.IsNullOrWhiteSpace(token))
            .Select(token => token.ToLowerInvariant())
            .Select(token => token switch
            {
                "web" => "Web",
                "search" => "搜索",
                "knowledge" => "知识",
                "retrieval" => "检索",
                "embedding" => "Embedding",
                "reranker" => "Reranker",
                "provider" => "Provider",
                "model" => "模型",
                "agent" => "Agent",
                "assistant" => "助手",
                "assistants" => "助手",
                "route" => "路由",
                "profiles" => "组合",
                "targets" => "目标",
                "remote" => "远端",
                "skill" => "Skill",
                "runner" => "Runner",
                "core" => "核心",
                "review" => "审核",
                "gate" => "门禁",
                "workspace" => "工作区",
                "path" => "路径",
                "base" => "基础",
                "url" => "URL",
                "api" => "API",
                "key" => "Key",
                "openai" => "OpenAI",
                "compatible" => "兼容",
                "lmstudio" => "LM Studio",
                "ollama" => "Ollama",
                "qwen" => "Qwen",
                "baai" => "BAAI",
                "token" => "Token",
                "overlap" => "重叠",
                "hashing" => "Hashing",
                "rag" => "RAG",
                "kb" => "知识库",
                _ => CultureInfo.InvariantCulture.TextInfo.ToTitleCase(token),
            })
            .Where(token => !string.IsNullOrWhiteSpace(token))
            .ToArray();
        return tokens.Length == 0 ? text : string.Join(" ", tokens);
    }
}
