using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class ComposerController
{
    internal void OpenPermissionMenu(Control? placementTarget = null)
    {
        var selected = ComposerPermissionMode();
        var menu = new ContextMenu { PlacementTarget = placementTarget ?? ChatWorkspace.EmptyPermissionButton, Placement = PlacementMode.Bottom };
        AddContextMenuItem(menu, $"{(selected == "default" ? "✓ " : "")}Default permissions", (_, _) =>
        {
            SetSetting(PermissionModeSetting, "default");
            RenderComposerSelectorText(ActiveSession());
            _ = SaveStateAsync();
        });
        AddContextMenuItem(menu, $"{(selected == "full_access" ? "✓ " : "")}Full access", (_, _) =>
        {
            if (!EnsureFullAccessGranted())
            {
                return;
            }
            SetSetting(PermissionModeSetting, "full_access");
            RenderComposerSelectorText(ActiveSession());
            _ = SaveStateAsync();
        });
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal void OpenModelMenu(Control? placementTarget = null)
    {
        var selected = GetSettingString(ModelIdSetting);
        var menu = new ContextMenu { PlacementTarget = placementTarget ?? ChatWorkspace.EmptyModelButton, Placement = PlacementMode.Bottom };
        var automatic = AutomaticComposerModel();
        AddContextMenuItem(menu, $"{(string.IsNullOrWhiteSpace(selected) ? "✓ " : "")}{automatic.Display}", (_, _) => SelectComposerModel(automatic));
        var assistModels = _assistModels
            .Where(item => item.Enabled && item.Configured)
            .OrderByDescending(item => item.Priority)
            .ThenBy(item => item.DisplayName)
            .ToList();
        if (assistModels.Count > 0)
        {
            menu.Items.Add(CreateStyledSeparator());
            var localModels = assistModels.Where(IsLocalAssistModel).ToList();
            var cloudModels = assistModels.Where(item => !IsLocalAssistModel(item)).ToList();
            if (localModels.Count > 0)
            {
                AddDisabledMenuHeader(menu, "Local configured models");
                foreach (var item in localModels)
                {
                    var model = new ComposerModel(item.ModelId, item.DisplayName, item.Provider, "local_model", item.Model);
                    AddContextMenuItem(menu, $"{(selected == model.Id ? "✓ " : "")}{model.Display}", (_, _) => SelectComposerModel(model));
                }
            }
            if (cloudModels.Count > 0)
            {
                if (localModels.Count > 0)
                {
                    menu.Items.Add(CreateStyledSeparator());
                }
                AddDisabledMenuHeader(menu, "API configured models");
                foreach (var item in cloudModels)
                {
                    var model = new ComposerModel(item.ModelId, item.DisplayName, item.Provider, "cloud_api", item.Model);
                    AddContextMenuItem(menu, $"{(selected == model.Id ? "✓ " : "")}{model.Display}", (_, _) => SelectComposerModel(model));
                }
            }
        }
        menu.Items.Add(CreateStyledSeparator());
        AddContextMenuItem(menu, "Manage models...", (_, _) => OpenManagementPage("models"));
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal void SelectComposerModel(ComposerModel model)
    {
        SetSetting(ModelIdSetting, model.Id);
        SetSetting(ModelDisplaySetting, model.Display);
        SetSetting(ModelProviderSetting, model.Provider);
        SetSetting(ModelSourceSetting, model.Source);
        SetSetting(ModelNameSetting, model.ModelName);
        RenderComposerSelectorText(ActiveSession());
        _ = SaveStateAsync();
    }

    internal void OpenReasoningMenu(Control? placementTarget = null)
    {
        var selected = GetSettingString(ReasoningEffortSetting, "auto").ToLowerInvariant();
        var menu = new ContextMenu { PlacementTarget = placementTarget ?? ChatWorkspace.EmptyReasoningButton, Placement = PlacementMode.Bottom };
        foreach (var option in ReasoningOptions())
        {
            AddContextMenuItem(menu, $"{(selected == option.Id ? "✓ " : "")}{option.Display}", (_, _) => SelectReasoningEffort(option));
        }
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal void SelectReasoningEffort(ReasoningOption option)
    {
        SetSetting(ReasoningEffortSetting, option.Id);
        RenderComposerSelectorText(ActiveSession());
        _ = SaveStateAsync();
    }

    internal static bool IsLocalAssistModel(AssistModelViewModel model)
    {
        return model.Provider.Contains("ollama", StringComparison.OrdinalIgnoreCase)
            || model.Provider.Contains("lmstudio", StringComparison.OrdinalIgnoreCase)
            || model.Provider.Contains("llamacpp", StringComparison.OrdinalIgnoreCase)
            || model.Provider.Contains("llama_cpp", StringComparison.OrdinalIgnoreCase)
            || model.Provider.Contains("llama.cpp", StringComparison.OrdinalIgnoreCase)
            || model.Provider.Contains("llama-cpp", StringComparison.OrdinalIgnoreCase)
            || model.Provider.Contains("local", StringComparison.OrdinalIgnoreCase);
    }

    internal void OpenProjectMenu(Control? placementTarget = null)
    {
        var selectedId = ComposerProject()?.Id ?? "";
        var menu = new ContextMenu { PlacementTarget = placementTarget ?? ChatWorkspace.EmptyProjectButton, Placement = PlacementMode.Bottom };
        AddContextMenuItem(menu, $"{(string.IsNullOrWhiteSpace(selectedId) ? "✓ " : "")}Chats (no project)", async (_, _) => await SelectComposerProjectAsync(null));
        var projects = State.Projects
            .Where(project => !IsArchived(project.Status))
            .OrderByDescending(project => project.UpdatedAt)
            .ToList();
        if (projects.Count > 0)
        {
            menu.Items.Add(CreateStyledSeparator());
            AddDisabledMenuHeader(menu, "Projects");
            foreach (var project in projects)
            {
                AddContextMenuItem(menu, $"{(selectedId == project.Id ? "✓ " : "")}{project.Title}", async (_, _) => await SelectComposerProjectAsync(project));
            }
        }
        menu.Items.Add(CreateStyledSeparator());
        AddContextMenuItem(menu, "Create project...", async (_, _) => await CreateAndSelectComposerProjectAsync());
        AddContextMenuItem(menu, "Open project management...", (_, _) => OpenManagementPage("tasks", "projects"));
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal async Task SelectComposerProjectAsync(DesktopItem? project)
    {
        var session = ActiveSession();
        session.ProjectId = project?.Id;
        session.UpdatedAt = NowSeconds();
        SetSetting(ProjectIdSetting, project?.Id ?? "");
        if (project is not null)
        {
            _expandedProjectIds.Add(project.Id);
            SelectProjectInSidebar(project.Id);
            WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = project.Id;
            WorkspaceSidebar.ConnectionStatusText.Text = $"当前会话将归类到项目：{project.Title}";
        }
        else
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "当前会话将保留在 Chats。";
        }
        RenderState();
        await SaveStateAsync();
    }

    internal async Task CreateAndSelectComposerProjectAsync()
    {
        OpenManagementPage("tasks", "projects");
        WorkbenchShell.ManagementPanels.ProjectManagementStatusText.Text = "请点击“新增”并选择一个新的项目目录；不会再自动使用当前仓库路径创建项目。";
        WorkspaceSidebar.ConnectionStatusText.Text = "请从项目管理新增项目并选择目录。";
        await Task.CompletedTask;
    }

    internal void OpenRuntimeMenu(Control? placementTarget = null)
    {
        var selected = GetSettingString(RuntimeModeSetting, "local_edge");
        var menu = new ContextMenu { PlacementTarget = placementTarget ?? ChatWorkspace.EmptyRuntimeButton, Placement = PlacementMode.Bottom };
        AddDisabledMenuHeader(menu, "Start in");
        AddContextMenuItem(menu, $"{(selected == "local_edge" ? "✓ " : "")}Work locally", (_, _) =>
        {
            SetRuntimeMode("local_edge", "Work locally");
        });
        menu.Items.Add(CreateStyledSeparator());
        AddContextMenuItem(menu, "Open local Edge browser", (_, _) => LaunchLocalEdgeBrowser());
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal void SetRuntimeMode(string mode, string display)
    {
        SetSetting(RuntimeModeSetting, mode);
        SetSetting(RuntimeDisplaySetting, display);
        RenderComposerSelectorText(ActiveSession());
        _ = SaveStateAsync();
    }

    internal void OpenBranchMenu(Control? placementTarget = null)
    {
        var current = CurrentGitBranch(refresh: true);
        var selected = GetSettingString(BranchSetting, current);
        var branches = GitBranches();
        var menu = new ContextMenu { PlacementTarget = placementTarget ?? ChatWorkspace.EmptyBranchButton, Placement = PlacementMode.Bottom };
        AddDisabledMenuHeader(menu, "Branches");
        var dirty = GitDirtyCount();
        if (dirty > 0)
        {
            AddDisabledMenuHeader(menu, $"Uncommitted: {dirty} file{(dirty == 1 ? "" : "s")}");
        }
        foreach (var branch in branches)
        {
            AddContextMenuItem(menu, $"{(selected == branch ? "✓ " : "")}{branch}", async (_, _) => await SelectBranchAsync(branch));
        }
        menu.Items.Add(CreateStyledSeparator());
        AddContextMenuItem(menu, "Create and checkout new branch...", async (_, _) => await CreateAndCheckoutBranchAsync());
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal async Task SelectBranchAsync(string branch)
    {
        if (string.IsNullOrWhiteSpace(branch))
        {
            return;
        }
        var current = CurrentGitBranch(refresh: true);
        if (!string.Equals(current, branch, StringComparison.OrdinalIgnoreCase))
        {
            var dirty = GitDirtyCount();
            if (dirty > 0 && !ConfirmAction(
                    "切换分支",
                    $"当前工作区有 {dirty} 个未提交文件。Git 会保护冲突文件，但切换可能失败。\n\n从 {current} 切换到 {branch}？",
                    "继续切换"))
            {
                DesktopDiagnosticLog.Write(_rootDir, "git", "switch_branch", "cancelled", $"from={current}; to={branch}; dirty={dirty}");
                return;
            }
            WorkspaceSidebar.ConnectionStatusText.Text = $"正在切换分支：{current} → {branch}";
            DesktopDiagnosticLog.Write(_rootDir, "git", "switch_branch", "started", $"from={current}; to={branch}; dirty={dirty}");
            var result = await Task.Run(() => RunGit($"switch {QuoteArg(branch)}"));
            if (!result.Success)
            {
                WorkspaceSidebar.ConnectionStatusText.Text = $"切换分支失败：{result.Output}";
                DesktopDiagnosticLog.Write(_rootDir, "git", "switch_branch", "failed", $"from={current}; to={branch}; dirty={dirty}; result={result.Output}");
                ConfirmAction("切换分支失败", result.Output, "知道了");
                return;
            }
            DesktopDiagnosticLog.Write(_rootDir, "git", "switch_branch", "completed", $"from={current}; to={branch}; dirty={dirty}");
        }
        SetLastKnownBranch(branch);
        SetSetting(BranchSetting, branch);
        RenderComposerSelectorText(ActiveSession());
        WorkspaceSidebar.ConnectionStatusText.Text = $"当前分支：{branch}";
        await SaveStateAsync();
    }

    internal async Task CreateAndCheckoutBranchAsync()
    {
        var branch = PromptText("新建分支", "分支名称", $"codex/{DateTime.Now:yyyyMMdd-HHmm}");
        if (string.IsNullOrWhiteSpace(branch))
        {
            return;
        }
        var normalized = branch.Trim();
        WorkspaceSidebar.ConnectionStatusText.Text = $"正在创建分支：{normalized}";
        DesktopDiagnosticLog.Write(_rootDir, "git", "create_branch", "started", $"branch={normalized}");
        var result = await Task.Run(() => RunGit($"switch -c {QuoteArg(normalized)}"));
        if (!result.Success)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = $"创建分支失败：{result.Output}";
            DesktopDiagnosticLog.Write(_rootDir, "git", "create_branch", "failed", $"branch={normalized}; result={result.Output}");
            ConfirmAction("创建分支失败", result.Output, "知道了");
            return;
        }
        DesktopDiagnosticLog.Write(_rootDir, "git", "create_branch", "completed", $"branch={normalized}");
        SetLastKnownBranch(normalized);
        SetSetting(BranchSetting, normalized);
        RenderComposerSelectorText(ActiveSession());
        WorkspaceSidebar.ConnectionStatusText.Text = $"已创建并切换分支：{normalized}";
        await SaveStateAsync();
    }

    internal static ComposerModel AutomaticComposerModel() =>
        new("", "自动（主模型）", "", "runtime_route", "");

    internal static IReadOnlyList<ReasoningOption> ReasoningOptions() => new[]
    {
        new ReasoningOption("auto", "自动推理"),
        new ReasoningOption("none", "关闭推理"),
        new ReasoningOption("low", "低推理"),
        new ReasoningOption("medium", "中推理"),
        new ReasoningOption("high", "高推理"),
    };

    internal string ComposerPermissionMode() => GetSettingString(PermissionModeSetting, "full_access");

    internal bool FullAccessGranted() => GetSettingBool(FullAccessGrantedSetting);

    internal bool EnsureFullAccessGranted()
    {
        if (GetSettingBool(FullAccessGrantedSetting))
        {
            return true;
        }
        if (!ConfirmAction(
            "授权 Full access",
            "Full access 会允许当前项目会话通过运行时权限网关请求本地高权限操作。是否对本项目会话授权一次？",
            "授权"))
        {
            return false;
        }
        SetSetting(FullAccessGrantedSetting, true);
        return true;
    }

    internal DesktopItem? ComposerProject()
    {
        var active = ActiveSession();
        var projectId = active.ProjectId;
        return string.IsNullOrWhiteSpace(projectId)
            ? null
            : State.Projects.FirstOrDefault(project => string.Equals(project.Id, projectId, StringComparison.OrdinalIgnoreCase));
    }

    internal void RenderComposerSelectorText(DesktopSession active)
    {
        var selectedModelId = GetSettingString(ModelIdSetting);
        var legacyFakeModel = selectedModelId is "spiritkin-5.5-extra-high" or "gpt-5" or "claude-opus-4" or "gemini-2.5-pro" or "deepseek-reasoner";
        if (legacyFakeModel)
        {
            SelectDefaultComposerModel(persist: false);
        }
        if (string.IsNullOrWhiteSpace(GetSettingString(RuntimeModeSetting)))
        {
            SetSetting(RuntimeModeSetting, "local_edge");
            SetSetting(RuntimeDisplaySetting, "Work locally");
        }
        if (string.IsNullOrWhiteSpace(GetSettingString(BranchSetting)))
        {
            SetSetting(BranchSetting, CurrentGitBranch(refresh: false));
        }
        if (!string.IsNullOrWhiteSpace(active.ProjectId))
        {
            SetSetting(ProjectIdSetting, active.ProjectId);
        }

        var permission = ComposerPermissionMode();
        ChatWorkspace.PermissionText.Text = permission == "full_access" ? "完全访问" : "默认权限";
        ChatWorkspace.EmptyPermissionText.Text = permission == "full_access" ? "完全访问" : "默认权限";
        var permissionBrush = new SolidColorBrush(permission == "full_access" ? Color.FromRgb(249, 115, 22) : Color.FromRgb(183, 199, 217));
        ChatWorkspace.PermissionText.Foreground = permissionBrush;
        ChatWorkspace.EmptyPermissionText.Foreground = permissionBrush;
        var modelText = GetSettingString(ModelDisplaySetting, "自动（主模型）");
        ChatWorkspace.EmptyModelText.Text = modelText;
        ChatWorkspace.ModelText.Text = modelText;
        var reasoningId = GetSettingString(ReasoningEffortSetting, "auto").ToLowerInvariant();
        var reasoningText = ReasoningOptions().FirstOrDefault(item => item.Id == reasoningId)?.Display ?? "自动推理";
        ChatWorkspace.EmptyReasoningText.Text = reasoningText;
        ChatWorkspace.ReasoningText.Text = reasoningText;
        var project = ComposerProject();
        var projectText = project?.Title ?? "会话";
        ChatWorkspace.EmptyQuickChatTitleText.Text = "今天先处理哪件事？";
        ChatWorkspace.EmptyProjectHintText.Text = projectText;
        var runtimeText = GetSettingString(RuntimeDisplaySetting, "本地工作");
        if (string.Equals(runtimeText, "Work locally", StringComparison.OrdinalIgnoreCase))
        {
            runtimeText = "本地工作";
        }
        ChatWorkspace.EmptyRuntimeText.Text = runtimeText;
        var branchText = GetSettingString(BranchSetting, CurrentGitBranch(refresh: false));
        ChatWorkspace.EmptyBranchText.Text = branchText;
        RenderComposerAttachmentStatus();
    }

    internal sealed record ReasoningOption(string Id, string Display);

}
