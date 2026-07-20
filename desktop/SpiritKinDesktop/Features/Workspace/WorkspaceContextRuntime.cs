using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace SpiritKinDesktop;

internal sealed partial class WorkspaceController
{
    internal DesktopSession ActiveSession()
    {
        var session = _state.Sessions.FirstOrDefault(s => s.Id == _state.ActiveSessionId) ?? _state.Sessions.FirstOrDefault();
        if (session is not null)
        {
            _state.ActiveSessionId = session.Id;
            return session;
        }
        session = DesktopState.DefaultSession();
        _state.Sessions.Add(session);
        _state.ActiveSessionId = session.Id;
        return session;
    }

    internal DesktopItem? SelectedProject()
    {
        var id = WorkspaceSidebar.ProjectsList.SelectedValue as string;
        return string.IsNullOrWhiteSpace(id) ? _state.Projects.OrderByDescending(item => item.UpdatedAt).FirstOrDefault() : _state.Projects.FirstOrDefault(item => item.Id == id);
    }

    internal DesktopItem? ProjectForSession(DesktopSession session)
    {
        return string.IsNullOrWhiteSpace(session.ProjectId)
            ? null
            : _state.Projects.FirstOrDefault(project => string.Equals(project.Id, session.ProjectId, StringComparison.OrdinalIgnoreCase));
    }

    private DesktopItem? SelectedProjectForWorkspace()
    {
        if (WorkbenchShell.ManagementPanels.RightProjectsList.SelectedItem is ProjectViewModel rightProject)
        {
            return _state.Projects.FirstOrDefault(project => string.Equals(project.Id, rightProject.ProjectId, StringComparison.OrdinalIgnoreCase));
        }
        if (WorkspaceSidebar.ProjectsList.SelectedItem is ProjectViewModel leftProject)
        {
            return _state.Projects.FirstOrDefault(project => string.Equals(project.Id, leftProject.ProjectId, StringComparison.OrdinalIgnoreCase));
        }
        return null;
    }

    internal DesktopItem? CurrentWorkspaceProject()
    {
        return ResolveWorkspaceProjectContext(_state.Projects, ActiveSession(), _workspaceProjectContextId);
    }

    internal static DesktopItem? ResolveWorkspaceProjectContext(
        IEnumerable<DesktopItem> projects,
        DesktopSession activeSession,
        string contextProjectId)
    {
        var projectId = string.IsNullOrWhiteSpace(contextProjectId)
            ? activeSession.ProjectId
            : contextProjectId;
        return string.IsNullOrWhiteSpace(projectId)
            ? null
            : projects.FirstOrDefault(project => string.Equals(project.Id, projectId, StringComparison.OrdinalIgnoreCase));
    }

    internal string ActiveWorkspaceRoot()
    {
        return ResolveProjectWorkspace(CurrentWorkspaceProject()) ?? _rootDir;
    }

    internal ProjectRuntimeProfile ActiveProjectRuntimeProfile()
    {
        var project = CurrentWorkspaceProject();
        var workspace = ResolveProjectWorkspace(project) ?? _rootDir;
        return BuildProjectRuntimeProfile(project, workspace);
    }

    internal ProjectRuntimeProfile BuildProjectRuntimeProfile(DesktopItem? project, string workspace)
    {
        var envFile = ResolveProjectRelativeFile(workspace, project?.EnvFilePath);
        var dependencyFile = ResolveProjectRelativeFile(workspace, project?.DependencyFilePath);
        var packageManager = string.IsNullOrWhiteSpace(project?.PackageManager) ? DetectPackageManager(workspace) : project.PackageManager!.Trim();
        if (string.Equals(packageManager, "auto", StringComparison.OrdinalIgnoreCase))
        {
            packageManager = DetectPackageManager(workspace);
        }
        return new ProjectRuntimeProfile(
            project?.Id ?? "",
            project?.Title ?? "默认工作区",
            workspace,
            envFile,
            dependencyFile,
            string.IsNullOrWhiteSpace(packageManager) ? "auto" : packageManager,
            project?.StartCommand?.Trim() ?? "");
    }

    private string ResolveProjectRelativeFile(string workspace, string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return "";
        }
        var expanded = Environment.ExpandEnvironmentVariables(raw.Trim().Trim('"'));
        return Path.GetFullPath(Path.IsPathRooted(expanded) ? expanded : Path.Combine(workspace, expanded));
    }

    internal Dictionary<string, string> BuildProjectRuntimeEnvironment(ProjectRuntimeProfile runtime)
    {
        var env = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["SPIRITKIN_PROJECT_ID"] = runtime.ProjectId,
            ["SPIRITKIN_PROJECT_TITLE"] = runtime.ProjectTitle,
            ["SPIRITKIN_PROJECT_WORKSPACE"] = runtime.WorkspacePath,
            ["SPIRITKIN_PROJECT_PACKAGE_MANAGER"] = runtime.PackageManager,
        };
        if (!string.IsNullOrWhiteSpace(runtime.DependencyFilePath))
        {
            env["SPIRITKIN_PROJECT_DEPENDENCY_FILE"] = runtime.DependencyFilePath;
        }
        if (!string.IsNullOrWhiteSpace(runtime.StartCommand))
        {
            env["SPIRITKIN_PROJECT_START_COMMAND"] = runtime.StartCommand;
        }
        foreach (var item in ReadEnvFile(runtime.EnvFilePath))
        {
            env[item.Key] = item.Value;
        }
        return env;
    }

    internal static Dictionary<string, string> ReadEnvFile(string path)
    {
        var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
        {
            return values;
        }
        foreach (var rawLine in File.ReadLines(path))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
            {
                continue;
            }
            if (line.StartsWith("export ", StringComparison.OrdinalIgnoreCase))
            {
                line = line[7..].Trim();
            }
            var equals = line.IndexOf('=');
            if (equals <= 0)
            {
                continue;
            }
            var key = line[..equals].Trim();
            var value = line[(equals + 1)..].Trim().Trim('"').Trim('\'');
            if (!string.IsNullOrWhiteSpace(key))
            {
                values[key] = value;
            }
        }
        return values;
    }

    internal static void ApplyEnvironment(ProcessStartInfo startInfo, IReadOnlyDictionary<string, string> env)
    {
        foreach (var item in env)
        {
            if (!string.IsNullOrWhiteSpace(item.Key))
            {
                startInfo.Environment[item.Key] = item.Value ?? "";
            }
        }
    }

}

