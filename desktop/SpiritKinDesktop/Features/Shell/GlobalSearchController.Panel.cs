using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace SpiritKinDesktop;

internal sealed partial class GlobalSearchController
{
    private Button GlobalSearchCloseButton => GlobalSearchOverlay.GlobalSearchCloseButton;
    private TextBlock GlobalSearchSummaryText => GlobalSearchOverlay.GlobalSearchSummaryText;
    private TextBox GlobalSearchBox => GlobalSearchOverlay.GlobalSearchBox;
    private TextBlock GlobalSearchEmptyText => GlobalSearchOverlay.GlobalSearchEmptyText;
    private ListBox GlobalSearchResultsList => GlobalSearchOverlay.GlobalSearchResultsList;

    internal void OpenGlobalSearch(string query = "")
    {
        GlobalSearchOverlay.Visibility = Visibility.Visible;
        if (!string.IsNullOrWhiteSpace(query))
        {
            GlobalSearchBox.Text = query;
            GlobalSearchBox.SelectAll();
        }
        RenderGlobalSearchResults(GlobalSearchBox.Text);
        GlobalSearchBox.Focus();
    }

    internal void CloseGlobalSearch()
    {
        GlobalSearchOverlay.Visibility = Visibility.Collapsed;
        GlobalSearchBox.Clear();
        _globalSearchResults.Clear();
        GlobalSearchEmptyText.Text = "输入关键词开始搜索。";
    }

    internal void GlobalSearchOverlay_MouseDown(object sender, MouseButtonEventArgs e)
    {
        if (ReferenceEquals(e.OriginalSource, sender))
        {
            CloseGlobalSearch();
        }
    }

    internal void GlobalSearchPanel_MouseDown(object sender, MouseButtonEventArgs e)
    {
        e.Handled = true;
    }

    internal void GlobalSearchBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        RenderGlobalSearchResults(GlobalSearchBox.Text);
    }

    internal void GlobalSearchBox_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape)
        {
            CloseGlobalSearch();
            e.Handled = true;
            return;
        }
        if (e.Key == Key.Down && _globalSearchResults.Count > 0)
        {
            GlobalSearchResultsList.Focus();
            GlobalSearchResultsList.SelectedIndex = Math.Max(0, GlobalSearchResultsList.SelectedIndex);
            e.Handled = true;
            return;
        }
        if (e.Key == Key.Enter)
        {
            if (GlobalSearchResultsList.SelectedItem is not GlobalSearchResultViewModel && _globalSearchResults.Count > 0)
            {
                GlobalSearchResultsList.SelectedIndex = 0;
            }
            OpenSelectedGlobalSearchResult();
            e.Handled = true;
        }
    }

    internal void GlobalSearchResultsList_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape)
        {
            CloseGlobalSearch();
            e.Handled = true;
            return;
        }
        if (e.Key == Key.Enter)
        {
            OpenSelectedGlobalSearchResult();
            e.Handled = true;
        }
    }

    internal void GlobalSearchResultsList_MouseDoubleClick(object sender, MouseButtonEventArgs e)
    {
        OpenSelectedGlobalSearchResult();
    }

    private void OpenSelectedGlobalSearchResult()
    {
        var result = GlobalSearchResultsList.SelectedItem as GlobalSearchResultViewModel ?? _globalSearchResults.FirstOrDefault();
        if (result is null)
        {
            return;
        }
        CloseGlobalSearch();
        NavigateGlobalSearchResult(result);
    }

    private void RenderGlobalSearchResults(string query)
    {
        _globalSearchResults.Clear();
        var terms = TokenizeSearchQuery(query);
        if (terms.Length == 0)
        {
            GlobalSearchEmptyText.Text = "输入关键词开始搜索。";
            GlobalSearchEmptyText.Visibility = Visibility.Visible;
            return;
        }
        foreach (var result in BuildGlobalSearchResults(terms).Take(80))
        {
            _globalSearchResults.Add(result);
        }
        GlobalSearchEmptyText.Text = _globalSearchResults.Count == 0 ? "没有匹配结果。" : "";
        GlobalSearchEmptyText.Visibility = _globalSearchResults.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        GlobalSearchSummaryText.Text = _globalSearchResults.Count == 0
            ? "没有匹配结果。"
            : $"找到 {_globalSearchResults.Count} 个结果，Enter 定位，Esc 关闭。";
        if (_globalSearchResults.Count > 0 && GlobalSearchResultsList.SelectedIndex < 0)
        {
            GlobalSearchResultsList.SelectedIndex = 0;
        }
    }

    private IEnumerable<GlobalSearchResultViewModel> BuildGlobalSearchResults(string[] terms)
    {
        var results = new List<GlobalSearchResultViewModel>();
        void Add(string scope, string title, string detail, string targetKind, string targetId, string page = "", string subPage = "", int priority = 50)
        {
            var haystack = $"{scope} {title} {detail} {targetKind} {targetId} {page} {subPage}";
            var score = SearchScore(haystack, terms, priority);
            if (score > 0)
            {
                results.Add(new GlobalSearchResultViewModel(scope, title, detail, targetKind, targetId, page, subPage, score));
            }
        }

        Add("功能", "快速会话", "打开聊天输入和当前会话", "workspace", "chat", priority: 70);
        Add("功能", "管理模块", "进入桌面端管理中心", "workspace", "management", priority: 68);
        Add("功能", "任务管理", "管理会话、项目和任务", "management", "tasks", "tasks", priority: 72);
        Add("功能", "工作流", "编辑工作流定义、节点蓝图、运行实例", "management", "workflows", "workflows", priority: 78);
        Add("功能", "工作流设计器", "编辑节点、依赖、端口、参数和 Agent", "workflow_designer", Workflows.ActiveWorkflowName(), "workflows", priority: 82);
        Add("功能", "搜索检索配置", "管理联网搜索、RAG、Embedding 和重排配置", "management", "search", "search", priority: 76);
        Add("功能", "Skills 管理", "管理技能、触发条件、工具权限和验证标准", "management", "skills", "skills", priority: 72);
        Add("功能", "Agent 集群", "管理 Agent、外部助手、知识库、路由和远端目标", "management", "agents", "agents", priority: 72);
        Add("功能", "日志", "查看、打开、归档和删除项目错误日志", "management", "logs", "logs", priority: 68);
        Add("功能", "诊断", "检查服务和依赖并运行自修复", "management", "diagnostics", "diagnostics", priority: 68);
        Add("功能", "模型管理", "协助模型、Provider 和本地模型同步", "management", "models", "models", priority: 68);
        Add("功能", "快速指令", "管理聊天输入框下拉菜单中的常用指令", "management", "commands", "commands", priority: 66);

        foreach (var session in _sessions)
        {
            Add("会话", session.Title, $"{session.Subtitle} · {session.StatusLabel}", "session", session.Id, priority: 54);
        }
        foreach (var project in _projects)
        {
            var kind = project.IsSession ? "project_session" : "project";
            var id = project.IsSession ? project.SessionId : project.ProjectId;
            Add(project.IsSession ? "项目会话" : "项目", project.Title, $"{project.Subtitle} · {project.StatusLabel}", kind, id, priority: project.IsSession ? 52 : 56);
        }
        foreach (var task in _tasks)
        {
            Add("任务", task.Title, $"{task.StatusLabel} · {task.Detail}", "task", task.Id, "tasks", "tasks", priority: 56);
        }
        foreach (var workflow in Workflows.Definitions)
        {
            Add("工作流", workflow.DisplayName, $"{workflow.Name} · {workflow.Meta} · {workflow.Description}", "workflow", workflow.Name, "workflows", priority: 64);
        }
        foreach (var node in Workflows.EditNodes)
        {
            Add("工作流节点", node.Title, $"{node.NodeId} · {node.NodeType} · Agent {node.AssignedAgent} · 依赖 {node.DependsOnText}", "workflow_node", node.NodeId, "workflows", priority: 60);
        }
        foreach (var run in Workflows.Runs)
        {
            Add("工作流运行", run.Title, $"{run.Id} · {run.StatusLabel} · {run.Detail}", "workflow_run", run.Id, "workflows", priority: 54);
        }
        foreach (var skill in Skills.Skills)
        {
            Add("Skill", skill.Name, $"{skill.Description} · {skill.Meta}", "skill", skill.Name, "skills", priority: 60);
        }
        foreach (var agent in Agents.Agents)
        {
            Add("Agent", agent.Label, $"{agent.AgentId} · {agent.Meta} · {agent.Notes}", "agent", agent.AgentId, "agents", "agents", priority: 60);
        }
        foreach (var assistant in Agents.ExternalAssistants)
        {
            Add("外部助手", assistant.Label, $"{assistant.AssistantId} · {assistant.Meta}", "external_assistant", assistant.AssistantId, "agents", "assistants", priority: 56);
        }
        foreach (var knowledge in Agents.KnowledgeBases)
        {
            Add("知识库", knowledge.Label, $"{knowledge.KnowledgeBaseId} · {knowledge.Meta} · {knowledge.Notes}", "knowledge_base", knowledge.KnowledgeBaseId, "agents", "knowledge", priority: 54);
        }
        foreach (var route in Agents.RouteProfiles)
        {
            Add("路由组合", route.Label, $"{route.ProfileId} · {route.Meta} · {route.Notes}", "route_profile", route.ProfileId, "agents", "routes", priority: 52);
        }
        foreach (var target in Agents.RemoteTargets)
        {
            Add("远端目标", target.Label, $"{target.TargetId} · {target.Meta}", "remote_target", target.TargetId, "agents", "remote", priority: 52);
        }
        foreach (var model in Learning.AssistModels)
        {
            Add("模型", model.DisplayName, $"{model.ModelId} · {model.Meta} · {model.Notes}", "assist_model", model.ModelId, "models", priority: 52);
        }
        foreach (var module in Modules.Modules)
        {
            Add("模块", module.Label, $"{module.ModuleId} · {module.Meta}", "module", module.ModuleId, "modules", priority: 52);
        }
        foreach (var service in Services.Services)
        {
            Add("服务", service.Label, $"{service.ServiceId} · {service.StatusLabel} · {service.Meta}", "service", service.ServiceId, "services", priority: 50);
        }
        foreach (var log in _logs)
        {
            Add("日志", log.Label, $"{log.LogId} · {log.Meta}", "log", log.LogId, "logs", priority: 50);
        }
        foreach (var command in _quickCommands)
        {
            Add("指令", command.Title, command.Command, "quick_command", command.Id, "commands", priority: 48);
        }

        return results
            .OrderByDescending(item => item.Score)
            .ThenBy(item => item.Scope, StringComparer.OrdinalIgnoreCase)
            .ThenBy(item => item.Title, StringComparer.OrdinalIgnoreCase);
    }

    internal static string[] TokenizeSearchQuery(string query) =>
        Regex.Split(query.Trim(), @"\s+")
            .Where(term => !string.IsNullOrWhiteSpace(term))
            .Select(term => term.Trim())
            .ToArray();

    internal static int SearchScore(string haystack, string[] terms, int priority)
    {
        if (terms.Length == 0)
        {
            return 0;
        }
        var score = priority;
        foreach (var term in terms)
        {
            if (haystack.Contains(term, StringComparison.OrdinalIgnoreCase))
            {
                score += 20;
                continue;
            }
            var compactTerm = term.Replace(" ", "");
            if (!string.IsNullOrWhiteSpace(compactTerm)
                && haystack.Replace(" ", "").Contains(compactTerm, StringComparison.OrdinalIgnoreCase))
            {
                score += 10;
                continue;
            }
            return 0;
        }
        return score;
    }

    private void NavigateGlobalSearchResult(GlobalSearchResultViewModel result)
    {
        switch (result.TargetKind)
        {
            case "workspace":
                Workspace.ShowWorkspacePage(result.TargetId);
                break;
            case "management":
                Workspace.OpenManagementPage(result.TargetId, result.SubPage);
                break;
            case "workflow_designer":
                Workspace.OpenManagementPage("workflows");
                Workflows.JumpToWorkflowDesigner();
                break;
            case "session":
            case "project_session":
                Navigation.ActivateSessionFromSidebar(result.TargetId);
                break;
            case "project":
                Workspace.OpenManagementPage("tasks", "projects");
                WorkspaceSidebar.ProjectsList.SelectedValue = result.TargetId;
                WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = result.TargetId;
                Workspace.RenderEditors();
                break;
            case "task":
                Workspace.OpenManagementPage("tasks", "tasks");
                WorkspaceSidebar.TasksList.SelectedValue = result.TargetId;
                WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue = result.TargetId;
                Workspace.RenderEditors();
                break;
            case "workflow":
                Workspace.OpenManagementPage("workflows");
                WorkbenchShell.ManagementPanels.WorkflowDefinitionCatalogList.SelectedValue = result.TargetId;
                break;
            case "workflow_node":
                Workspace.OpenManagementPage("workflows");
                Workflows.SelectSingleWorkflowGraphNode(result.TargetId);
                Workflows.JumpToWorkflowBlueprint();
                break;
            case "workflow_run":
                Workspace.OpenManagementPage("workflows");
                WorkbenchShell.ManagementPanels.WorkflowRunsList.SelectedValue = result.TargetId;
                break;
            case "skill":
                Workspace.OpenManagementPage("skills");
                WorkbenchShell.ManagementPanels.SkillsList.SelectedValue = result.TargetId;
                Skills.RenderSelectedSkillEditor();
                break;
            case "agent":
                Workspace.OpenManagementPage("agents", "agents");
                WorkbenchShell.ManagementPanels.AgentsList.SelectedValue = result.TargetId;
                Agents.RenderSelectedAgentEditor();
                break;
            case "external_assistant":
                Workspace.OpenManagementPage("agents", "assistants");
                WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue = result.TargetId;
                Agents.RenderSelectedExternalAssistantEditor();
                break;
            case "knowledge_base":
                Workspace.OpenManagementPage("agents", "knowledge");
                WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue = result.TargetId;
                Agents.RenderSelectedKnowledgeBaseEditor();
                break;
            case "route_profile":
                Workspace.OpenManagementPage("agents", "routes");
                WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedValue = result.TargetId;
                Agents.RenderSelectedRouteProfileEditor();
                break;
            case "remote_target":
                Workspace.OpenManagementPage("agents", "remote");
                WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedValue = result.TargetId;
                Agents.RenderSelectedRemoteTargetEditor();
                break;
            case "assist_model":
                Workspace.OpenManagementPage("models");
                WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = result.TargetId;
                Learning.RenderSelectedAssistModelEditor();
                break;
            case "module":
                Workspace.OpenManagementPage("modules");
                WorkbenchShell.ManagementPanels.ModuleManagementModulesList.SelectedValue = result.TargetId;
                Modules.UpdateActionText();
                break;
            case "service":
                Workspace.OpenManagementPage("services");
                WorkbenchShell.ManagementPanels.ServicesList.SelectedValue = result.TargetId;
                break;
            case "log":
                Workspace.OpenManagementPage("logs");
                WorkbenchShell.ManagementPanels.LogsList.SelectedValue = result.TargetId;
                _ = Runtime.LoadLogsAsync(result.TargetId);
                break;
            case "quick_command":
                Workspace.OpenManagementPage("commands");
                WorkbenchShell.ManagementPanels.QuickCommandsList.SelectedValue = result.TargetId;
                Navigation.RenderSelectedQuickCommand();
                break;
        }
        _setConnectionStatus($"已定位：{result.Scope} · {result.Title}");
    }
}
