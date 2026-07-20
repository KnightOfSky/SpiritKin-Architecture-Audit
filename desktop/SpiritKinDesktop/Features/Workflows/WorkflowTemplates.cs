using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    internal void EnsureWorkflowNodeTemplates()
    {
        if (_workflowNodeTemplates.Count > 0)
        {
            return;
        }

        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "agent_task",
            "Agent 任务",
            "agent_task",
            "agent_task",
            "Agent 处理",
            "",
            "",
            "",
            "",
            "{}",
            "需要某个 Agent 认领或完成的人工/智能体节点。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "tool_call_mobile_link",
            "手机链接入队",
            "mobile_link_intake",
            "tool_call",
            "手机链接入队",
            "",
            "ecommerce.task_queue.ingest_mobile_links",
            "",
            "",
            "{\n  \"include_latest\": \"{{include_latest}}\",\n  \"project_root\": \"{{project_root}}\",\n  \"links_jsonl\": \"{{links_jsonl}}\"\n}",
            "读取手机分享队列并生成可处理任务。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "skill_call_extension_productdata",
            "接收扩展 productData",
            "productdata_build",
            "skill_call",
            "接收扩展 productData",
            "",
            "",
            "ecommerce.browser_extension_productdata.workflow",
            "",
            "{\n  \"project_root\": \"{{project_root}}\",\n  \"state_dir\": \"{{ecommerce_state_dir}}\",\n  \"task_id\": \"{{task_id}}\",\n  \"product_data_json\": \"{{product_data_json}}\",\n  \"control_plane_artifact_id\": \"{{control_plane_artifact_id}}\"\n}",
            "接收登录态浏览器扩展生成的 productData Artifact。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "review_gate_core",
            "核心审核门",
            "review_gate",
            "review_gate",
            "核心审核门",
            "",
            "",
            "",
            "core_review",
            "{}",
            "需要人工或核心规则通过后继续执行。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "review_gate_human",
            "发布前人工审核",
            "publish_review",
            "review_gate",
            "发布前审核",
            "",
            "",
            "",
            "human_review",
            "{}",
            "发布、提交或高风险动作之前的人工审核。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "branch_condition",
            "条件分支",
            "branch",
            "branch",
            "条件分支",
            "",
            "",
            "",
            "",
            "{\n  \"condition\": \"{{condition}}\",\n  \"routes\": {\n    \"true\": [],\n    \"false\": []\n  }\n}",
            "根据 condition 或 route 记录选路，供下游节点和 replay 使用。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "waiter_signal",
            "等待外部信号",
            "wait_for_signal",
            "waiter",
            "等待外部信号",
            "",
            "",
            "",
            "",
            "{\n  \"wait_for\": \"external_signal\"\n}",
            "等待文件、任务、设备或人工信号；收到后用 signal_node 继续。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "external_callback",
            "外部回调",
            "callback",
            "external_callback",
            "外部回调",
            "",
            "",
            "",
            "",
            "{\n  \"callback_id\": \"callback_id\"\n}",
            "等待外部系统回调；回调 payload 会进入节点输出和 replay。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "workflow_android_step_launch",
            "Android 步骤",
            "android_step",
            "workflow.android_step",
            "Android 控制",
            "",
            "",
            "",
            "",
            "{\n  \"device_id\": \"{{android_device_id}}\",\n  \"operation\": \"app.launch\",\n  \"params\": {\n    \"app_name\": \"{{app_name}}\"\n  }\n}",
            "向 Android Bridge 投递受控命令，等待 heartbeat command_result 后继续工作流。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "custom_static_ip_rotation",
            "静态 IP / 代理配置变动",
            "static_ip_rotation",
            "custom.static_ip_rotation",
            "静态 IP 变动",
            "ecommerce",
            "",
            "",
            "",
            "{\n  \"executor\": \"external_callback\",\n  \"callback_id\": \"{{ip_change_callback_id}}\",\n  \"proxy_profile\": \"{{proxy_profile}}\",\n  \"static_ip_pool\": \"{{static_ip_pool}}\",\n  \"rotation_policy\": {\n    \"mode\": \"sticky\",\n    \"ttl_minutes\": 120\n  },\n  \"apply_scope\": \"{{apply_scope}}\"\n}",
            "开放自动化节点：记录或请求代理/IP 配置变动，外部系统完成后用 signal_node 回写。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "subgraph",
            "子工作流",
            "subgraph",
            "subgraph",
            "子工作流",
            "",
            "",
            "",
            "",
            "{\n  \"workflow_name\": \"child.workflow.v1\",\n  \"inputs\": {}\n}",
            "请求一个子工作流并等待 signal_node 写回结果。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "agent_task_ecommerce",
            "电商 Agent 节点",
            "ecommerce_task",
            "agent_task",
            "电商处理",
            "ecommerce",
            "",
            "",
            "",
            "{}",
            "电商 Agent 执行选品、上架草稿、发布保持等任务。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "agent_task_vision",
            "视觉 Agent 节点",
            "vision_task",
            "agent_task",
            "视觉处理",
            "vision_model",
            "",
            "",
            "",
            "{}",
            "视觉/OCR/素材检查类节点。"));
        _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
            "agent_task_video",
            "视频 Agent 节点",
            "video_task",
            "agent_task",
            "视频处理",
            "video_animation",
            "",
            "",
            "",
            "{}",
            "脚本、分镜、生成配置、交付打包类节点。"));

        WorkbenchShell.ManagementPanels.WorkflowNodeTemplateBox.SelectedIndex = 0;
    }

    internal void RefreshWorkflowNodeTemplatesFromCatalog(JsonElement workflows)
    {
        if (workflows.ValueKind != JsonValueKind.Object
            || !workflows.TryGetProperty("node_catalog", out var nodeCatalog)
            || nodeCatalog.ValueKind != JsonValueKind.Object
            || !nodeCatalog.TryGetProperty("catalog", out var catalog)
            || catalog.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        var existing = _workflowNodeTemplates
            .Select(item => item.TemplateId)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var item in catalog.EnumerateArray())
        {
            var group = ReadSafeJsonString(item, "group");
            if (group is not ("tool" or "skill"))
            {
                continue;
            }
            var catalogId = ReadSafeJsonString(item, "catalog_id");
            if (string.IsNullOrWhiteSpace(catalogId) || existing.Contains(catalogId))
            {
                continue;
            }
            var nodeType = ReadSafeJsonString(item, "node_type", group == "skill" ? "skill_call" : "tool_call");
            var label = ReadSafeJsonString(item, "label", catalogId);
            var baseNodeId = SafeWorkflowTemplateNodeId(label);
            var argumentsJson = WorkflowTemplateArgumentsJson(item);
            _workflowNodeTemplates.Add(new WorkflowNodeTemplateViewModel(
                catalogId,
                group == "skill" ? $"Skill · {label}" : $"工具 · {label}",
                baseNodeId,
                nodeType,
                label,
                "",
                group == "tool" ? ReadSafeJsonString(item, "tool_name", label) : "",
                group == "skill" ? ReadSafeJsonString(item, "skill_name", label) : "",
                "",
                argumentsJson,
                ReadSafeJsonString(item, "description", group == "skill" ? "来自后端 Skill 目录。" : "来自后端工具目录。")));
            existing.Add(catalogId);
        }
    }

    private static string SafeWorkflowTemplateNodeId(string value)
    {
        var text = Regex.Replace((value ?? "").Trim().ToLowerInvariant(), @"[^a-z0-9_]+", "_").Trim('_');
        return string.IsNullOrWhiteSpace(text) ? "catalog_node" : text.Length > 48 ? text[..48].Trim('_') : text;
    }

    internal static string WorkflowTemplateArgumentsJson(JsonElement catalogItem)
    {
        if (!catalogItem.TryGetProperty("parameters", out var parameters) || parameters.ValueKind != JsonValueKind.Object)
        {
            return "{}";
        }
        var definitions = parameters.TryGetProperty("properties", out var properties)
            && properties.ValueKind == JsonValueKind.Object
            ? properties
            : parameters;
        var arguments = new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase);
        foreach (var property in definitions.EnumerateObject())
        {
            var key = property.Name;
            if (string.IsNullOrWhiteSpace(key))
            {
                continue;
            }
            var type = property.Value.ValueKind switch
            {
                JsonValueKind.String => property.Value.GetString() ?? "string",
                JsonValueKind.Object when property.Value.TryGetProperty("type", out var typeProperty)
                    && typeProperty.ValueKind == JsonValueKind.String => typeProperty.GetString() ?? "string",
                JsonValueKind.Object => "string",
                _ => "",
            };
            if (string.IsNullOrWhiteSpace(type))
            {
                continue;
            }
            arguments[key] = type.Trim().ToLowerInvariant() switch
            {
                "boolean" or "bool" => false,
                "number" or "integer" or "int" => 0,
                "array" or "list" => Array.Empty<object>(),
                "object" or "dict" or "json" => new Dictionary<string, object?>(),
                _ => $"{{{{{key}}}}}",
            };
        }
        return arguments.Count == 0
            ? "{}"
            : JsonSerializer.Serialize(arguments, new JsonSerializerOptions { WriteIndented = true });
    }
}
