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

internal static class WorkflowDisplayText
{
    public static string WorkflowName(string workflowName, string displayName = "")
    {
        var name = (workflowName ?? "").Trim();
        var label = (displayName ?? "").Trim();
        if (!string.IsNullOrWhiteSpace(label) && !LooksTechnical(label, name))
        {
            return label;
        }
        return name.ToLowerInvariant() switch
        {
            "ecommerce.auto_listing.v1" => "电商自动上架",
            "content.video_generation.v1" => "视频生成",
            "workflow.free_composition.v1" => "自由组合工作流",
            _ when name.StartsWith("custom.workflow.", StringComparison.OrdinalIgnoreCase) => "自定义工作流",
            _ when name.Contains("ecommerce", StringComparison.OrdinalIgnoreCase) => "电商自动化工作流",
            _ when name.Contains("video", StringComparison.OrdinalIgnoreCase) => "视频工作流",
            _ => HumanizeIdentifier(name, "工作流"),
        };
    }

    public static string TechnicalLine(string id) =>
        string.IsNullOrWhiteSpace(id) ? "ID --" : $"ID {id.Trim()}";

    public static string ShortId(string id, int max = 24)
    {
        var text = (id ?? "").Trim();
        max = Math.Max(4, max);
        if (text.Length <= max)
        {
            return string.IsNullOrWhiteSpace(text) ? "--" : text;
        }
        return $"{text[..Math.Min(text.Length, Math.Max(1, max - 3))]}...";
    }

    public static string CategoryLabel(string category, string domain = "")
    {
        var value = string.IsNullOrWhiteSpace(category) ? domain : category;
        return value.ToLowerInvariant() switch
        {
            "ecommerce" => "电商",
            "video" => "视频",
            "content" => "内容",
            "custom" => "自定义",
            "workflow" => "流程",
            _ => HumanizeIdentifier(value, "流程"),
        };
    }

    public static string NodeTypeLabel(string nodeType) => (nodeType ?? "").Trim().ToLowerInvariant() switch
    {
        "agent_task" => "Agent 任务",
        "tool_call" => "工具调用",
        "skill_call" => "Skill 调用",
        "review_gate" => "审核门",
        "branch" => "条件分支",
        "subgraph" => "子工作流",
        "waiter" => "等待信号",
        "external_callback" => "外部回调",
        "workflow.android_step" => "Android 步骤",
        "automation.android_step" => "Android 步骤",
        _ => HumanizeIdentifier(nodeType ?? "", "节点"),
    };

    public static string StatusLabel(string status) => (status ?? "").Trim().ToLowerInvariant() switch
    {
        "pending" => "待执行",
        "runnable" => "可执行",
        "running" => "运行中",
        "waiting" => "等待中",
        "waiting_review" => "待审核",
        "succeeded" => "已完成",
        "failed" => "失败",
        "blocked" => "阻塞",
        "ready" => "就绪",
        "needs_attention" => "注意",
        "candidate" => "候选",
        "agent_task" => "Agent 任务",
        "tool_call" => "工具调用",
        "skill_call" => "Skill 调用",
        "review_gate" => "审核门",
        "branch" => "条件分支",
        "subgraph" => "子工作流",
        "waiter" => "等待信号",
        "external_callback" => "外部回调",
        "workflow.android_step" => "Android 步骤",
        "automation.android_step" => "Android 步骤",
        _ => HumanizeIdentifier(status ?? "", "--"),
    };

    public static string ActorLabel(string actor)
    {
        var value = (actor ?? "").Trim();
        return value.ToLowerInvariant() switch
        {
            "" or "--" => "--",
            "ecommerce" => "电商 Agent",
            "vision_model" => "视觉 Agent",
            "video_animation" => "视频 Agent",
            "programming" => "编程 Agent",
            "main_text" => "Spirit",
            "skill_runner" => "Skill Runner",
            "external_reviewer" => "外部评审",
            "core_review" => "核心审核",
            "human_review" => "人工审核",
            "ecommerce.task_queue.ingest_mobile_links" => "手机链接入队",
            "ecommerce.browser_extension_productdata.workflow" => "接收扩展 productData",
            _ => HumanizeIdentifier(value, value),
        };
    }

    public static string NodeActor(JsonElement node, string fallback = "--")
    {
        var actor = ReadString(node, "assigned_agent");
        if (string.IsNullOrWhiteSpace(actor))
        {
            actor = ReadString(node, "tool_name");
        }
        if (string.IsNullOrWhiteSpace(actor))
        {
            actor = ReadString(node, "skill_name");
        }
        if (string.IsNullOrWhiteSpace(actor))
        {
            actor = ReadString(node, "review_gate");
        }
        return string.IsNullOrWhiteSpace(actor) ? fallback : actor;
    }

    public static string HumanizeIdentifier(string value, string fallback)
    {
        var text = (value ?? "").Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return fallback;
        }
        var tokens = Regex.Split(text, @"[._\-\s]+")
            .Where(token => !string.IsNullOrWhiteSpace(token))
            .Select(token => token.ToLowerInvariant())
            .Select(token => token switch
            {
                "ecommerce" => "电商",
                "auto" => "自动",
                "listing" => "上架",
                "content" => "内容",
                "video" => "视频",
                "generation" => "生成",
                "workflow" => "工作流",
                "custom" => "自定义",
                "productdata" => "ProductData",
                "miniapp" => "小程序",
                "task" => "任务",
                "queue" => "队列",
                "review" => "审核",
                "gate" => "门禁",
                "agent" => "Agent",
                "skill" => "Skill",
                "tool" => "工具",
                "call" => "调用",
                "v1" => "",
                _ => CultureInfo.InvariantCulture.TextInfo.ToTitleCase(token),
            })
            .Where(token => !string.IsNullOrWhiteSpace(token))
            .ToArray();
        return tokens.Length == 0 ? text : string.Join(" ", tokens);
    }

    private static bool LooksTechnical(string label, string workflowName) =>
        string.Equals(label, workflowName, StringComparison.OrdinalIgnoreCase)
        || (label.Contains('.', StringComparison.Ordinal) && Regex.IsMatch(label, "^[a-zA-Z0-9_.-]+$"));

    private static string ReadString(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return "";
        }
        return value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";
    }
}
