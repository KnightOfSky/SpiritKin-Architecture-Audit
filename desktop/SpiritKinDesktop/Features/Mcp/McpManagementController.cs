using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed class McpManagementController
{
    private readonly ManagementPanelsView _panels;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string> _apiBase;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly Func<Task> _loadModuleManagementAsync;
    private readonly Func<string, string, bool> _confirmDestructiveAction;
    private readonly Func<bool> _isRendering;
    private readonly Action<bool> _setRendering;

    public ObservableCollection<McpServerViewModel> Servers { get; } = new();

    public ObservableCollection<McpToolMappingViewModel> ToolMappings { get; } = new();

    public ObservableCollection<EventViewModel> AuditEvents { get; } = new();

    public McpManagementController(
        ManagementPanelsView panels,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string> apiBase,
        JsonSerializerOptions jsonOptions,
        Func<Task> loadModuleManagementAsync,
        Func<string, string, bool> confirmDestructiveAction,
        Func<bool> isRendering,
        Action<bool> setRendering)
    {
        _panels = panels;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _apiBase = apiBase;
        _jsonOptions = jsonOptions;
        _loadModuleManagementAsync = loadModuleManagementAsync;
        _confirmDestructiveAction = confirmDestructiveAction;
        _isRendering = isRendering;
        _setRendering = setRendering;
    }

    public async Task LoadAsync()
    {
        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/desktop/mcp-management");
            Render(doc.RootElement.GetProperty("mcp_management"));
        }
        catch (Exception ex)
        {
            Servers.Clear();
            ToolMappings.Clear();
            AuditEvents.Clear();
            _panels.McpManagementSummaryText.Text = $"MCP 管理加载失败：{ex.Message}";
            _panels.McpServerActionText.Text = "请确认 command gateway 正在运行并支持 /desktop/mcp-management。";
        }
    }

    public void Render(JsonElement state)
    {
        _setRendering(true);
        try
        {
            var previous = _panels.McpServersList.SelectedValue as string;
            Servers.Clear();
            if (state.TryGetProperty("servers", out var servers) && servers.ValueKind == JsonValueKind.Array)
            {
                foreach (var server in servers.EnumerateArray())
                {
                    Servers.Add(McpServerViewModel.FromJson(server));
                }
            }
            ToolMappings.Clear();
            if (state.TryGetProperty("tool_mappings", out var mappings) && mappings.ValueKind == JsonValueKind.Array)
            {
                foreach (var mapping in mappings.EnumerateArray())
                {
                    ToolMappings.Add(McpToolMappingViewModel.FromJson(mapping));
                }
            }
            AuditEvents.Clear();
            if (state.TryGetProperty("audit_log", out var auditLog) && auditLog.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in auditLog.EnumerateArray().Reverse().Take(30))
                {
                    var action = JsonHelpers.ReadString(item, "action", "--");
                    var serverId = JsonHelpers.ReadString(item, "server_id", "--");
                    var actor = JsonHelpers.ReadString(item, "actor", "--");
                    var message = JsonHelpers.ReadString(item, "message");
                    AuditEvents.Add(new EventViewModel(
                        $"{FormatTime(JsonHelpers.ReadDouble(item, "at"))} · {action}",
                        $"{serverId} · {actor}{Environment.NewLine}{message}".Trim()));
                }
            }
            if (AuditEvents.Count == 0)
            {
                AuditEvents.Add(new EventViewModel("Registry 审计", "暂无 MCP registry 动作记录。"));
            }
            _panels.McpServersList.SelectedValue = SelectExistingId(previous, Servers.Select(item => item.ServerId))
                ?? Servers.FirstOrDefault()?.ServerId;
            _panels.McpManagementSummaryText.Text = $"Server {JsonHelpers.ReadInt(state, "server_count")} · enabled {JsonHelpers.ReadInt(state, "enabled_count")} · ready {JsonHelpers.ReadInt(state, "ready_count")} · attention {JsonHelpers.ReadInt(state, "attention_count")} · mappings {JsonHelpers.ReadInt(state, "ready_mapping_count")}";
            _panels.McpPolicyText.Text = BuildPolicyText(state);
        }
        finally
        {
            _setRendering(false);
        }
        RenderSelectedServerEditor();
    }

    public static string BuildPolicyText(JsonElement state)
    {
        if (!state.TryGetProperty("policy", out var policy) || policy.ValueKind != JsonValueKind.Object)
        {
            return "策略：MCP 工具必须先审核并启用后才会导出。";
        }
        var launch = JsonHelpers.ReadBool(policy, "external_launch_enabled", false) ? "允许启动" : "不启动外部进程";
        var exportGate = JsonHelpers.ReadBool(policy, "requires_review_before_tool_export", true) ? "审核后导出" : "未要求审核";
        var allowlist = JsonHelpers.ReadBool(policy, "requires_agent_allowlist", true) ? "需要 Agent allowlist" : "未要求 allowlist";
        var mode = JsonHelpers.ReadString(policy, "tool_execution_mode", "proxy_pending_execution");
        return $"策略：{launch} · {exportGate} · {allowlist} · {mode}";
    }

    public void NewServer()
    {
        var serverId = UniqueId("mcp", Servers.Select(item => item.ServerId));
        var server = new McpServerViewModel(
            serverId,
            serverId,
            "stdio",
            "",
            "",
            Array.Empty<string>(),
            false,
            "candidate",
            "untrusted",
            "project",
            Array.Empty<string>(),
            Array.Empty<string>(),
            Array.Empty<string>(),
            Array.Empty<string>(),
            Array.Empty<string>(),
            Array.Empty<string>(),
            "[]",
            "",
            "disabled",
            Array.Empty<string>());
        Servers.Add(server);
        _panels.McpServersList.SelectedValue = serverId;
        RenderSelectedServerEditor();
        _panels.McpServerActionText.Text = "已新建 MCP Server 草稿；保存后进入候选 registry。";
    }

    public async Task SaveServerAsync()
    {
        try
        {
            var payload = BuildServerPayload("save_server");
            if (payload is null)
            {
                return;
            }
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/mcp-management", payload);
            EnsureOkResponse(doc.RootElement, "MCP Server 保存失败");
            Render(doc.RootElement.GetProperty("mcp_management"));
            _panels.McpServerActionText.Text = $"MCP Server 已保存：{_panels.McpServerIdBox.Text.Trim()}";
            await _loadModuleManagementAsync();
        }
        catch (Exception ex)
        {
            _panels.McpServerActionText.Text = $"MCP Server 保存失败：{ex.Message}";
        }
    }

    public async Task ServerActionAsync(string action)
    {
        var serverId = SelectedServerId();
        if (string.IsNullOrWhiteSpace(serverId))
        {
            _panels.McpServerActionText.Text = "请先选择或填写 Server ID。";
            return;
        }
        try
        {
            object payload = action switch
            {
                "approve_server" => new { action, server_id = serverId, reviewer = "wpf_desktop", decision = "approved" },
                "reject_server" => new { action, server_id = serverId, reviewer = "wpf_desktop", decision = "rejected" },
                _ => new { action, server_id = serverId, reviewer = "wpf_desktop" },
            };
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/mcp-management", payload);
            EnsureOkResponse(doc.RootElement, $"MCP 动作失败：{action}");
            Render(doc.RootElement.GetProperty("mcp_management"));
            _panels.McpServerActionText.Text = $"MCP 动作完成：{action} · {serverId}";
            await _loadModuleManagementAsync();
        }
        catch (Exception ex)
        {
            _panels.McpServerActionText.Text = $"MCP 动作失败：{ex.Message}";
        }
    }

    public async Task DeleteServerAsync()
    {
        var serverId = SelectedServerId();
        if (string.IsNullOrWhiteSpace(serverId))
        {
            _panels.McpServerActionText.Text = "请先选择或填写 Server ID。";
            return;
        }
        if (!_confirmDestructiveAction("删除 MCP Server", $"确定要删除 MCP Server“{serverId}”吗？"))
        {
            return;
        }
        try
        {
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/mcp-management", new
            {
                action = "delete_server",
                server_id = serverId,
                reviewer = "wpf_desktop",
            });
            EnsureOkResponse(doc.RootElement, "MCP Server 删除失败");
            Render(doc.RootElement.GetProperty("mcp_management"));
            _panels.McpServerActionText.Text = $"已删除 MCP Server：{serverId}";
            await _loadModuleManagementAsync();
        }
        catch (Exception ex)
        {
            _panels.McpServerActionText.Text = $"MCP Server 删除失败：{ex.Message}";
        }
    }

    public void OnServerSelectionChanged()
    {
        if (_isRendering())
        {
            return;
        }
        RenderSelectedServerEditor();
    }

    private Dictionary<string, object?>? BuildServerPayload(string action)
    {
        var serverId = _panels.McpServerIdBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(serverId))
        {
            _panels.McpServerActionText.Text = "请填写 Server ID。";
            return null;
        }
        var transport = ComboText(_panels.McpTransportBox);
        var command = _panels.McpServerCommandBox.Text.Trim();
        var url = _panels.McpServerUrlBox.Text.Trim();
        if (string.Equals(transport, "stdio", StringComparison.OrdinalIgnoreCase) && string.IsNullOrWhiteSpace(command))
        {
            _panels.McpServerActionText.Text = "stdio MCP Server 需要命令。";
            return null;
        }
        if ((string.Equals(transport, "sse", StringComparison.OrdinalIgnoreCase) || string.Equals(transport, "http", StringComparison.OrdinalIgnoreCase)) && string.IsNullOrWhiteSpace(url))
        {
            _panels.McpServerActionText.Text = $"{transport} MCP Server 需要 URL。";
            return null;
        }
        object? tools;
        try
        {
            using var toolsDoc = JsonDocument.Parse(string.IsNullOrWhiteSpace(_panels.McpToolsJsonBox.Text) ? "[]" : _panels.McpToolsJsonBox.Text);
            if (toolsDoc.RootElement.ValueKind != JsonValueKind.Array)
            {
                _panels.McpServerActionText.Text = "工具声明 JSON 必须是数组。";
                return null;
            }
            tools = JsonSerializer.Deserialize<object>(toolsDoc.RootElement.GetRawText(), _jsonOptions);
        }
        catch (Exception ex)
        {
            _panels.McpServerActionText.Text = $"工具声明 JSON 无效：{ex.Message}";
            return null;
        }
        return new Dictionary<string, object?>
        {
            ["action"] = action,
            ["server_id"] = serverId,
            ["label"] = string.IsNullOrWhiteSpace(_panels.McpServerLabelBox.Text) ? serverId : _panels.McpServerLabelBox.Text.Trim(),
            ["transport"] = transport,
            ["command"] = command,
            ["args"] = SplitLines(_panels.McpServerArgsBox.Text),
            ["url"] = url,
            ["enabled"] = _panels.McpServerEnabledBox.IsChecked == true,
            ["review_state"] = ComboText(_panels.McpReviewStateBox),
            ["trust_level"] = ComboText(_panels.McpTrustLevelBox),
            ["workspace_scope"] = ComboText(_panels.McpWorkspaceScopeBox),
            ["owner_agent_ids"] = SplitLines(_panels.McpOwnerAgentsBox.Text),
            ["env_refs"] = SplitLines(_panels.McpServerEnvRefsBox.Text),
            ["filesystem_scopes"] = SplitLines(_panels.McpFilesystemScopesBox.Text),
            ["network_scopes"] = SplitLines(_panels.McpNetworkScopesBox.Text),
            ["resources"] = SplitLines(_panels.McpResourcesBox.Text),
            ["prompts"] = SplitLines(_panels.McpPromptsBox.Text),
            ["tools"] = tools,
            ["notes"] = _panels.McpServerNotesBox.Text.Trim(),
            ["reviewer"] = "wpf_desktop",
        };
    }

    private string SelectedServerId()
    {
        return ((_panels.McpServersList.SelectedValue as string) ?? _panels.McpServerIdBox.Text).Trim();
    }

    private void RenderSelectedServerEditor()
    {
        var selected = _panels.McpServersList.SelectedValue as string;
        var server = Servers.FirstOrDefault(item => string.Equals(item.ServerId, selected, StringComparison.OrdinalIgnoreCase));
        if (server is null)
        {
            _panels.McpServerIdBox.Clear();
            _panels.McpServerLabelBox.Clear();
            SetComboText(_panels.McpTransportBox, "stdio");
            _panels.McpServerCommandBox.Clear();
            _panels.McpServerUrlBox.Clear();
            _panels.McpServerArgsBox.Clear();
            _panels.McpServerEnvRefsBox.Clear();
            SetComboText(_panels.McpReviewStateBox, "candidate");
            SetComboText(_panels.McpTrustLevelBox, "untrusted");
            SetComboText(_panels.McpWorkspaceScopeBox, "project");
            _panels.McpServerEnabledBox.IsChecked = false;
            _panels.McpOwnerAgentsBox.Clear();
            _panels.McpFilesystemScopesBox.Clear();
            _panels.McpNetworkScopesBox.Clear();
            _panels.McpResourcesBox.Clear();
            _panels.McpPromptsBox.Clear();
            _panels.McpToolsJsonBox.Text = "[]";
            _panels.McpServerNotesBox.Clear();
            _panels.McpServerActionText.Text = "暂无 MCP Server。";
            return;
        }
        _panels.McpServerIdBox.Text = server.ServerId;
        _panels.McpServerLabelBox.Text = server.Label;
        SetComboText(_panels.McpTransportBox, server.Transport);
        _panels.McpServerCommandBox.Text = server.Command;
        _panels.McpServerUrlBox.Text = server.Url;
        _panels.McpServerArgsBox.Text = string.Join(Environment.NewLine, server.Args);
        _panels.McpServerEnvRefsBox.Text = string.Join(Environment.NewLine, server.EnvRefs);
        SetComboText(_panels.McpReviewStateBox, server.ReviewState);
        SetComboText(_panels.McpTrustLevelBox, server.TrustLevel);
        SetComboText(_panels.McpWorkspaceScopeBox, server.WorkspaceScope);
        _panels.McpServerEnabledBox.IsChecked = server.Enabled;
        _panels.McpOwnerAgentsBox.Text = string.Join(Environment.NewLine, server.OwnerAgentIds);
        _panels.McpFilesystemScopesBox.Text = string.Join(Environment.NewLine, server.FilesystemScopes);
        _panels.McpNetworkScopesBox.Text = string.Join(Environment.NewLine, server.NetworkScopes);
        _panels.McpResourcesBox.Text = string.Join(Environment.NewLine, server.Resources);
        _panels.McpPromptsBox.Text = string.Join(Environment.NewLine, server.Prompts);
        _panels.McpToolsJsonBox.Text = server.ToolsJson;
        _panels.McpServerNotesBox.Text = server.Notes;
        _panels.McpServerActionText.Text = $"{server.StatusLabel} · {server.HealthLine}{Environment.NewLine}{server.EndpointLine}".Trim();
    }

    private static string FormatTime(double seconds) => seconds <= 0 ? "--" : DateTimeOffset.FromUnixTimeSeconds((long)seconds).LocalDateTime.ToString("g");

    private static string? SelectExistingId(string? preferred, IEnumerable<string> candidates)
    {
        if (string.IsNullOrWhiteSpace(preferred))
        {
            return null;
        }
        return candidates.FirstOrDefault(candidate => string.Equals(candidate, preferred, StringComparison.OrdinalIgnoreCase));
    }

    private static string UniqueId(string prefix, IEnumerable<string> existingIds)
    {
        var existing = existingIds.Where(id => !string.IsNullOrWhiteSpace(id)).ToHashSet(StringComparer.OrdinalIgnoreCase);
        for (var index = 1; index < 1000; index++)
        {
            var candidate = $"{prefix}_{index:00}";
            if (!existing.Contains(candidate))
            {
                return candidate;
            }
        }
        return $"{prefix}_{Guid.NewGuid():N}";
    }

    private static string[] SplitLines(string text)
    {
        return text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    }

    private static string ComboText(ComboBox combo)
    {
        if (combo.SelectedItem is ComboBoxItem item)
        {
            return item.Tag?.ToString()?.Trim()
                ?? item.Content?.ToString()?.Trim()
                ?? "";
        }
        if (combo.SelectedValue is string selectedValue && !string.IsNullOrWhiteSpace(selectedValue))
        {
            return selectedValue;
        }
        return combo.Text;
    }

    private static void SetComboText(ComboBox combo, string value)
    {
        var text = (value ?? "").Trim();
        foreach (var item in combo.Items)
        {
            if (item is ComboBoxItem comboBoxItem
                && string.Equals(comboBoxItem.Content?.ToString(), text, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = comboBoxItem;
                combo.Text = text;
                return;
            }
        }
        combo.SelectedItem = null;
        combo.Text = text;
    }

    private static void EnsureOkResponse(JsonElement root, string actionLabel) => JsonResponseHelpers.EnsureOkResponse(root, actionLabel);

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

        public static double ReadDouble(JsonElement element, string key)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return 0;
            }
            if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
            {
                return number;
            }
            return double.TryParse(ReadString(element, key), out var parsed) ? parsed : 0;
        }

        public static bool ReadBool(JsonElement element, string key, bool fallback = false)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return fallback;
            }
            return value.ValueKind switch
            {
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.String => bool.TryParse(value.GetString(), out var parsed) ? parsed : fallback,
                _ => fallback,
            };
        }
    }
}
