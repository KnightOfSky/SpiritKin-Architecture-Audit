using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class NavigationController
{
    internal void SkillsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        RenderSelectedSkillEditor();
    }

    internal void AgentsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Agents.RenderSelectedAgentEditor();
    }

    internal void AgentModelSelectBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Agents.AgentModelSelectBox_SelectionChanged(sender, e);
    }

    internal void ExternalAssistantsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Agents.RenderSelectedExternalAssistantEditor();
    }

    internal void AgentAdaptersList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Agents.RenderSelectedAgentAdapterEditor();
    }

    internal void KnowledgeBasesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Agents.RenderSelectedKnowledgeBaseEditor();
    }

    internal void KnowledgeSourcesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Agents.RenderSelectedKnowledgeSourceEditor();
    }

    internal void RouteProfilesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Agents.RenderSelectedRouteProfileEditor();
    }

    internal void RemoteTargetsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Agents.RenderSelectedRemoteTargetEditor();
    }

    internal void DiagnosticIssuesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (WorkbenchShell.ManagementPanels.DiagnosticIssuesList.SelectedItem is ActionItemViewModel item)
        {
            WorkbenchShell.ManagementPanels.DiagnosticActionText.Text = string.IsNullOrWhiteSpace(item.Command)
                ? "该问题没有可复制命令，请按说明手动处理。"
                : $"可复制命令：{item.Command}";
        }
    }

    internal void DailyItemsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (WorkbenchShell.ManagementPanels.DailyItemsList.SelectedItem is ActionItemViewModel item)
        {
            WorkbenchShell.ManagementPanels.DailyActionText.Text = item.Kind switch
            {
                "task" => "可定位到任务管理。",
                "log_error" => "可定位到日志页。",
                "service" => "可定位到服务页。",
                _ => "可查看对应管理页或详情。",
            };
        }
    }

    internal void ContextSuggestionsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (WorkbenchShell.ManagementPanels.ContextSuggestionsList.SelectedItem is ActionItemViewModel item)
        {
            WorkbenchShell.ManagementPanels.ContextSuggestionActionText.Text = string.IsNullOrWhiteSpace(item.Command)
                ? "该建议没有命令，复制时会复制说明。"
                : $"可复制命令：{item.Command}";
        }
    }

    internal void ModuleManagementModulesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Modules.UpdateActionText();
    }

    internal void ModuleManagementActionsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        Modules.UpdateActionText();
    }

    internal void AssistModelsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        Learning.RenderSelectedAssistModelEditor();
    }

    internal void RightNavList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var page = (WorkbenchShell.RightNavList.SelectedItem as ListBoxItem)?.Tag as string ?? "tasks";
        WorkbenchShell.ManagementPanels.TasksPanel.Visibility = page == "tasks" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.TracePanel.Visibility = page == "trace" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.SyncPanel.Visibility = page == "sync" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.ServicesPanel.Visibility = page == "services" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.LogsPanel.Visibility = page == "logs" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.DailyPanel.Visibility = page == "daily" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.DiagnosticsPanel.Visibility = page == "diagnostics" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.ModuleManagementPanel.Visibility = page == "modules" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.GovernancePanel.Visibility = page == "governance" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.WorkflowsPanel.Visibility = page == "workflows" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.SkillsPanel.Visibility = page == "skills" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.McpManagementPanel.Visibility = page == "mcp" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.MobileManagementPanel.Visibility = page == "mobile" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.QuickCommandsPanel.Visibility = page == "commands" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.ModelsPanel.Visibility = page == "models" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.LearningPanel.Visibility = page == "learning" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.MemoryManagementPanel.Visibility = page == "memory" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.EvolutionPanel.Visibility = page == "evolution" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.SearchManagementPanel.Visibility = page == "search" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.ContextPanel.Visibility = page == "context" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.ProjectOverviewPanel.Visibility = page == "overview" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.CollaborationPanel.Visibility = page == "collaboration" ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.ManagementPanels.AgentManagementPanel.Visibility = page == "agents" ? Visibility.Visible : Visibility.Collapsed;
        if (page == "modules" && Modules.Modules.Count == 0)
        {
            _ = LoadModuleManagementAsync();
        }
        if (page == "workflows" && Workflows.DefinitionNodes.Count == 0 && Workflows.Runs.Count == 0)
        {
            _ = Workflows.LoadWorkflowsAsync();
        }
        if (page == "trace")
        {
            if (!Workflows.HasSnapshot)
            {
                _ = Workflows.LoadWorkflowsAsync();
            }
            else
            {
                RenderTracePanel();
            }
        }
        if (page == "evolution" && Evolution.LoopSteps.Count == 0)
        {
            _ = Evolution.LoadAsync();
        }
        if (page == "governance")
        {
            _ = _loadGovernanceAsync();
        }
        if (page == "memory" && !Memory.HasLoaded)
        {
            _ = Memory.LoadAsync();
        }
        if (page == "search" && Search.ModelCapabilities.Count == 0 && Search.Gaps.Count == 0)
        {
            _ = Search.LoadAsync();
        }
        if (page == "mcp" && Mcp.Servers.Count == 0 && Mcp.ToolMappings.Count == 0)
        {
            _ = Mcp.LoadAsync();
        }
        if (page == "mobile" && string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobileAndroidReceiverUrlBox.Text))
        {
            _ = Mobile.LoadMobileManagementAsync();
        }
        if (page == "collaboration" && Context.CollaborationTasks.Count == 0 && Context.CollaborationClaims.Count == 0)
        {
            _ = Context.LoadCollaborationAsync();
        }

        WorkbenchShell.RightModuleTitleText.Text = page switch
        {
            "tasks" => "工作空间",
            "modules" => "功能模块",
            "governance" => "授权与调度",
            "workflows" => "工作流",
            "trace" => "事件追踪",
            "sync" => "状态同步",
            "services" => "服务与端口",
            "logs" => "运行日志",
            "daily" => "每日事项",
            "diagnostics" => "诊断与修复",
            "skills" => "Skills 管理",
            "mcp" => "MCP 管理",
            "mobile" => "移动端 Bridge",
            "commands" => "快速指令",
            "models" => "模型管理",
            "learning" => "学习与纠错",
            "memory" => "记忆复核",
            "evolution" => "进化闭环",
            "search" => "搜索与知识",
            "context" => "上下文策略",
            "overview" => "项目总览",
            "collaboration" => "模型协作",
            "agents" => "Agent 集群",
            _ => "管理模块",
        };
    }
}

