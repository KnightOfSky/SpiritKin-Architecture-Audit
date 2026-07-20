using SpiritKinDesktop.Controls;
using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;

namespace SpiritKinDesktop;

internal sealed class ModuleManagementController
{
    private readonly ManagementPanelsView _panels;
    private readonly Action<string, string?> _openManagementPage;

    public ObservableCollection<ModuleManagementViewModel> Modules { get; } = new();

    public ObservableCollection<ActionItemViewModel> Actions { get; } = new();

    public ModuleManagementController(
        ManagementPanelsView panels,
        Action<string, string?> openManagementPage)
    {
        _panels = panels;
        _openManagementPage = openManagementPage;
    }

    public void UpdateActionText()
    {
        if (_panels.ModuleManagementActionsList.SelectedItem is ActionItemViewModel action)
        {
            _panels.ModuleManagementActionText.Text = $"{action.PriorityLabel}优先 · {action.Type}{Environment.NewLine}{action.ModuleDisplay} · {action.GovernanceMeta}{Environment.NewLine}{action.Meta}{Environment.NewLine}{action.OperatorDisplay}".Trim();
            return;
        }
        if (_panels.ModuleManagementModulesList.SelectedItem is ModuleManagementViewModel module)
        {
            _panels.ModuleManagementActionText.Text = $"{module.Label} · {module.StatusLabel} · 健康 {module.HealthScore}{Environment.NewLine}{module.OwnerLine}{Environment.NewLine}{module.RiskSummary}{Environment.NewLine}{module.Description}".Trim();
            return;
        }
        _panels.ModuleManagementActionText.Text = Actions.Count == 0 ? "当前没有待处理事项。" : "--";
    }

    public void OpenSelectedModule()
    {
        if (_panels.ModuleManagementModulesList.SelectedItem is ModuleManagementViewModel module)
        {
            OpenDestination(module.ModuleId, module.DesktopPage, module.Endpoint);
            return;
        }
        _panels.ModuleManagementActionText.Text = "请先选择一个模块。";
    }

    public void OpenSelectedAction()
    {
        if (_panels.ModuleManagementActionsList.SelectedItem is ActionItemViewModel action)
        {
            OpenDestination(action.Target, "", action.Command);
            return;
        }
        OpenSelectedModule();
    }

    private void OpenDestination(string moduleId, string desktopPage, string endpoint)
    {
        var destination = NormalizeDesktopDestination(moduleId, desktopPage, endpoint);
        var parts = destination.Split(':', 2, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        var page = parts.Length > 0 ? parts[0] : "modules";
        var subPage = parts.Length > 1 ? parts[1] : null;
        _openManagementPage(page, subPage);
    }

    internal static string NormalizeDesktopDestination(string moduleId, string desktopPage, string endpoint)
    {
        var destination = (desktopPage ?? "").Trim();
        var page = destination.Split(':', 2, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .FirstOrDefault() ?? "";
        return page.ToLowerInvariant() switch
        {
            "operations" => "services",
            "project_runtime" => "tasks:projects",
            "resource-registry" or "resource_registry" => "modules",
            "state_maintenance" or "state-maintenance" => "mobile",
            "action_log" or "action-log" => "logs",
            "tasks" or "workflows" or "modules" or "skills" or "mcp" or "mobile" or "commands"
                or "trace" or "sync" or "services" or "logs" or "daily" or "diagnostics" or "models"
                or "learning" or "evolution" or "search" or "context" or "overview" or "collaboration"
                or "agents" or "memory" => destination,
            _ => ModulePageFor(moduleId, endpoint),
        };
    }

    public static string BuildSummary(JsonElement state)
    {
        if (!state.TryGetProperty("overview", out var overview) || overview.ValueKind != JsonValueKind.Object)
        {
            return "模块快照已加载。";
        }
        var status = ModuleStatusLabel(JsonHelpers.ReadString(overview, "status", "--"));
        var portfolioText = "";
        if (state.TryGetProperty("portfolio", out var portfolio) && portfolio.ValueKind == JsonValueKind.Object)
        {
            var risks = portfolio.TryGetProperty("risk_counts", out var riskCounts) && riskCounts.ValueKind == JsonValueKind.Object
                ? $" · 风险 高/中/低 {JsonHelpers.ReadInt(riskCounts, "high")}/{JsonHelpers.ReadInt(riskCounts, "medium")}/{JsonHelpers.ReadInt(riskCounts, "low")}"
                : "";
            portfolioText = $" · 健康 {JsonHelpers.ReadInt(portfolio, "health_score")} · 就绪率 {JsonHelpers.ReadInt(portfolio, "readiness_percent")}% · 高优先 {JsonHelpers.ReadInt(portfolio, "high_action_count")}{risks}";
        }
        return $"状态 {status} · 模块 {JsonHelpers.ReadInt(overview, "module_count")} · 就绪 {JsonHelpers.ReadInt(overview, "ready_count")} · 注意 {JsonHelpers.ReadInt(overview, "attention_count")} · 阻塞 {JsonHelpers.ReadInt(overview, "blocked_count")} · 事项 {JsonHelpers.ReadInt(overview, "action_count")}{portfolioText}";
    }

    public static string BuildPortfolioText(JsonElement state)
    {
        if (!state.TryGetProperty("portfolio", out var portfolio) || portfolio.ValueKind != JsonValueKind.Object)
        {
            return "等待组合快照。";
        }
        return $"健康 {JsonHelpers.ReadInt(portfolio, "health_score")} · 就绪率 {JsonHelpers.ReadInt(portfolio, "readiness_percent")}% · {UiDisplayText.Posture(JsonHelpers.ReadString(portfolio, "operator_posture", "--"))}";
    }

    public static string BuildRiskText(JsonElement state)
    {
        if (!state.TryGetProperty("portfolio", out var portfolio) || portfolio.ValueKind != JsonValueKind.Object)
        {
            return "等待风险汇总。";
        }
        var high = 0;
        var medium = 0;
        var low = 0;
        if (portfolio.TryGetProperty("risk_counts", out var risks) && risks.ValueKind == JsonValueKind.Object)
        {
            high = JsonHelpers.ReadInt(risks, "high");
            medium = JsonHelpers.ReadInt(risks, "medium");
            low = JsonHelpers.ReadInt(risks, "low");
        }
        return $"高/中/低风险 {high}/{medium}/{low} · 高优先事项 {JsonHelpers.ReadInt(portfolio, "high_action_count")} · 关键高风险 {JsonHelpers.ReadInt(portfolio, "critical_high_risk_count")}";
    }

    public static string BuildGovernanceText(JsonElement state)
    {
        if (!state.TryGetProperty("overview", out var overview) || overview.ValueKind != JsonValueKind.Object)
        {
            return "等待治理快照。";
        }
        return $"{ModuleStatusLabel(JsonHelpers.ReadString(overview, "status", "--"))} · 待处理事项 {JsonHelpers.ReadInt(overview, "action_count")} · 阻塞模块 {JsonHelpers.ReadInt(overview, "blocked_count")}";
    }

    public static string ModuleStatusLabel(string status) => status.ToLowerInvariant() switch
    {
        "ready" => "就绪",
        "needs_attention" => "注意",
        "blocked" => "阻塞",
        _ => status,
    };

    public static string ModuleLabel(string moduleId) => moduleId switch
    {
        "skills" => "技能",
        "workflows" => "工作流",
        "agents" => "智能体集群",
        "knowledge_base" => "知识库",
        "search_management" => "搜索检索",
        "models" => "模型管理",
        "code_jury" => "Code/UI Jury",
        "evolution" => "进化闭环",
        "module_governance" => "模块治理",
        _ => string.IsNullOrWhiteSpace(moduleId) ? "模块" : moduleId,
    };

    public static string ModulePageFor(string moduleId, string endpoint = "")
    {
        if (endpoint.Contains("workflows", StringComparison.OrdinalIgnoreCase) || moduleId == "workflows")
        {
            return "workflows";
        }
        if (endpoint.Contains("skills", StringComparison.OrdinalIgnoreCase) || moduleId == "skills")
        {
            return "skills";
        }
        if (endpoint.Contains("agent-management", StringComparison.OrdinalIgnoreCase) || moduleId == "agents")
        {
            return "agents";
        }
        if (endpoint.Contains("knowledge", StringComparison.OrdinalIgnoreCase) || moduleId == "knowledge_base")
        {
            return "agents:knowledge";
        }
        if (endpoint.Contains("search-management", StringComparison.OrdinalIgnoreCase) || moduleId == "search_management")
        {
            return "search";
        }
        if (endpoint.Contains("mcp-management", StringComparison.OrdinalIgnoreCase) || moduleId == "mcp_management")
        {
            return "mcp";
        }
        if (endpoint.Contains("mobile-management", StringComparison.OrdinalIgnoreCase) || moduleId == "mobile_management")
        {
            return "mobile";
        }
        if (endpoint.Contains("model-catalog", StringComparison.OrdinalIgnoreCase) || moduleId == "models")
        {
            return "models";
        }
        if (endpoint.Contains("code-jury", StringComparison.OrdinalIgnoreCase) || moduleId == "code_jury")
        {
            return "models";
        }
        if (endpoint.Contains("learning", StringComparison.OrdinalIgnoreCase))
        {
            return "learning";
        }
        if (endpoint.Contains("memory", StringComparison.OrdinalIgnoreCase) || moduleId.Contains("memory", StringComparison.OrdinalIgnoreCase))
        {
            return "memory";
        }
        if (endpoint.Contains("evolution", StringComparison.OrdinalIgnoreCase) || moduleId == "evolution")
        {
            return "evolution";
        }
        if (endpoint.Contains("ecosystem-review", StringComparison.OrdinalIgnoreCase) || moduleId == "module_governance")
        {
            return "overview";
        }
        return "modules";
    }

    internal static class JsonHelpers
    {
        public static string ReadString(JsonElement element, string key)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return "";
            }
            return value.ValueKind switch
            {
                JsonValueKind.String => value.GetString() ?? "",
                JsonValueKind.Number => value.GetRawText(),
                JsonValueKind.True => "true",
                JsonValueKind.False => "false",
                JsonValueKind.Null => "",
                _ => value.GetRawText(),
            };
        }

        public static string ReadString(JsonElement element, string key, string fallback)
        {
            var value = ReadString(element, key);
            return string.IsNullOrWhiteSpace(value) ? fallback : value;
        }

        public static int ReadInt(JsonElement element, string key)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return 0;
            }
            if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
            {
                return number;
            }
            return int.TryParse(ReadString(element, key), out var parsed) ? parsed : 0;
        }
    }
}
