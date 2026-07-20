using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;

namespace SpiritKinDesktop;

internal sealed partial class ServicesController
{
    internal static bool TryGetProjectPortProfile(JsonElement servicePorts, string profileId, out JsonElement profile)
    {
        profile = default;
        if (!servicePorts.TryGetProperty("config", out var config) || config.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        if (!config.TryGetProperty("profiles", out var profiles) || profiles.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        if (profiles.TryGetProperty(profileId, out profile) && profile.ValueKind == JsonValueKind.Object)
        {
            return true;
        }
        return false;
    }

    internal static bool ProjectPortProfileMatchesCurrent(JsonElement servicePorts, JsonElement profile)
    {
        var current = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        if (servicePorts.TryGetProperty("config", out var config) && config.ValueKind == JsonValueKind.Object && config.TryGetProperty("overrides", out var overrides) && overrides.ValueKind == JsonValueKind.Object)
        {
            foreach (var item in overrides.EnumerateObject())
            {
                if (item.Value.ValueKind == JsonValueKind.Number && item.Value.TryGetInt32(out var port))
                {
                    current[item.Name] = port;
                }
            }
        }
        var expected = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        if (profile.TryGetProperty("overrides", out var profileOverrides) && profileOverrides.ValueKind == JsonValueKind.Object)
        {
            foreach (var item in profileOverrides.EnumerateObject())
            {
                if (item.Value.ValueKind == JsonValueKind.Number && item.Value.TryGetInt32(out var port))
                {
                    expected[item.Name] = port;
                }
            }
        }
        return current.Count == expected.Count && expected.All(item => current.TryGetValue(item.Key, out var port) && port == item.Value);
    }

    internal static string NormalizeLocalProfileId(string value)
    {
        var parts = new List<string>();
        var current = new StringBuilder();
        foreach (var ch in (value ?? "").Trim())
        {
            if (char.IsLetterOrDigit(ch))
            {
                current.Append(char.ToLowerInvariant(ch));
            }
            else if (current.Length > 0)
            {
                parts.Add(current.ToString());
                current.Clear();
            }
        }
        if (current.Length > 0)
        {
            parts.Add(current.ToString());
        }
        return string.Join("_", parts);
    }

    internal async Task SaveProjectPortProfileAsync()
    {
        var project = CurrentPortProfileProject();
        if (project is null)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = "请先选择或打开一个项目，再保存端口 Profile。";
            return;
        }
        var workspace = ResolveProjectWorkspace(project) ?? project.WorkspacePath ?? ActiveWorkspaceRoot();
        await ServicePortActionAsync(new
        {
            action = "save_profile",
            profile_id = project.Id,
            label = $"{project.Title} 端口 Profile",
            project_id = project.Id,
            workspace_path = workspace,
            actor = "wpf_desktop",
        });
    }

    internal async Task ApplyProjectPortProfileAsync()
    {
        var project = CurrentPortProfileProject();
        if (project is null)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = "请先选择或打开一个项目，再应用端口 Profile。";
            return;
        }
        await ServicePortActionAsync(new { action = "apply_profile", profile_id = project.Id, actor = "wpf_desktop" });
    }

    internal async Task DeleteProjectPortProfileAsync()
    {
        var project = CurrentPortProfileProject();
        if (project is null)
        {
            WorkbenchShell.ManagementPanels.ServiceActionText.Text = "请先选择或打开一个项目，再删除端口 Profile。";
            return;
        }
        if (!ConfirmDestructiveAction("删除项目端口 Profile", $"确定要删除“{project.Title}”的端口 Profile 吗？当前端口配置不会被清空。"))
        {
            return;
        }
        await ServicePortActionAsync(new { action = "delete_profile", profile_id = project.Id, actor = "wpf_desktop" });
    }

    private DesktopItem? CurrentPortProfileProject()
    {
        return ProjectForSession(ActiveSession()) ?? SelectedProject();
    }

}

