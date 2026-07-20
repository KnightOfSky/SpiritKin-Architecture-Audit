using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkbenchController
{
    internal string CurrentGitBranch(bool refresh)
    {
        EnsureGitWorkspaceContext();
        if (!refresh && !string.IsNullOrWhiteSpace(_lastKnownBranch))
        {
            return _lastKnownBranch;
        }
        var result = RunGit("branch --show-current");
        _lastKnownBranch = result.Success && !string.IsNullOrWhiteSpace(result.Output)
            ? result.Output.Trim()
            : string.IsNullOrWhiteSpace(_lastKnownBranch) ? "master" : _lastKnownBranch;
        return _lastKnownBranch;
    }

    private string EnsureGitWorkspaceContext()
    {
        var workspace = ResolveGitWorkingDirectory(ActiveWorkspaceRoot(), _rootDir);
        if (!string.Equals(_gitWorkspacePath, workspace, StringComparison.OrdinalIgnoreCase))
        {
            _gitWorkspacePath = workspace;
            _lastKnownBranch = "";
            _gitChangesLoaded = false;
            _cachedGitDirtyCount = 0;
        }
        return workspace;
    }

    internal List<string> GitBranches()
    {
        var result = RunGit("branch --format=%(refname:short)");
        if (!result.Success)
        {
            return new List<string> { CurrentGitBranch(refresh: false) };
        }
        var branches = result.Output
            .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(item => item.Trim().TrimStart('*').Trim())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderByDescending(item => string.Equals(item, CurrentGitBranch(refresh: false), StringComparison.OrdinalIgnoreCase))
            .ThenBy(item => item)
            .ToList();
        if (branches.Count == 0)
        {
            branches.Add(CurrentGitBranch(refresh: false));
        }
        return branches;
    }

    internal int GitDirtyCount()
    {
        var result = RunGit("status --short");
        return result.Success
            ? result.Output.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries).Length
            : 0;
    }

    internal void RenderWorkbenchStatus(DesktopSession active)
    {
        var state = State;
        var branch = CurrentGitBranch(refresh: false);
        WorkbenchShell.BranchEnvironmentText.Text = branch;
        WorkbenchShell.LocalEnvironmentText.Text = RuntimeDisplay();
        WorkbenchShell.WebPreviewStatusText.Text = "Desktop status";
        ChatWorkspace.InlineWebPreviewStatusText.Text = $"{FrontendBaseUrl()}/desktop_console.html";
        var workspace = ActiveWorkspaceRoot();
        WorkbenchShell.EnvironmentStatusText.Text = $"{Path.GetFileName(workspace)} · {branch} · changes {_cachedGitDirtyCount} · {state.Sessions.Count} chats · {state.Projects.Count} projects";
        WorkbenchShell.GitHubCliStatusText.Text = _cachedGithubCliStatus;
        RenderInstrumentSummary(branch);
        var workStarted = AssistantWorkStartedAt();
        var workDuration = workStarted > 0 ? Math.Max(0, NowSeconds() - workStarted) : AssistantWorkDuration();
        ChatWorkspace.InlineWorkedStatusText.Text = workStarted > 0 ? $"Working for {FormatDuration(workDuration)}" : $"Worked for {FormatDuration(workDuration)}";
        var pendingInfo = PendingInfo(state.Pending);
        var latestWork = active.Messages
            .Where(message => string.Equals(message.Kind, "work", StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(message => message.UpdatedAt)
            .FirstOrDefault();
        var workEventCount = latestWork?.Steps?.Count ?? 0;
        var workRunning = string.Equals(latestWork?.Subtitle, "running", StringComparison.OrdinalIgnoreCase);
        ChatWorkspace.InlineThinkingStatusText.Text = pendingInfo is not null ? "WAIT" : workRunning ? "LIVE" : "READY";
        ChatWorkspace.InlineCompactStatusText.Text = workEventCount > 0
            ? $"{workEventCount} 个真实运行事件 · {(workRunning ? "正在接收" : "已完成")}"
            : "当前会话尚无运行事件";

        _workbenchProgress.Clear();
        if (pendingInfo is not null)
        {
            _workbenchProgress.Add(new EventViewModel("等待确认", $"{pendingInfo.Target}.{pendingInfo.Operation} · {pendingInfo.RiskLevel}"));
        }
        foreach (var task in state.Tasks.OrderByDescending(item => item.UpdatedAt).Take(4))
        {
            _workbenchProgress.Add(new EventViewModel(task.Title, $"{task.Status} · {FormatTime(task.UpdatedAt)}"));
        }
        foreach (var ev in state.Events.TakeLast(5).Reverse())
        {
            _workbenchProgress.Add(new EventViewModel(ev.Type, ev.Time));
        }
        if (_workbenchProgress.Count == 0)
        {
            _workbenchProgress.Add(new EventViewModel("空闲", "当前没有正在跟踪的执行事件。"));
        }

        _sources.Clear();
        if (HasPendingAttachments())
        {
            _sources.Add(new EventViewModel("Attachments", PendingAttachmentNames(5)));
        }
        WorkbenchShell.SourcesEmptyText.Visibility = _sources.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        WorkbenchShell.SourcesList.Visibility = _sources.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
        var showArtifactStrip = active.Messages.Count > 0;
        ChatWorkspace.ConversationArtifactsPanel.Visibility = showArtifactStrip ? Visibility.Visible : Visibility.Collapsed;
        ChatWorkspace.ChatArtifactsRow.Height = showArtifactStrip ? GridLength.Auto : new GridLength(0);
        if (active.Messages.Any() && !_gitChangesLoaded && !_gitChangesLoading)
        {
            _ = RefreshGitChangesAsync();
        }
    }

    internal void RenderInstrumentSummary(string branch)
    {
        WorkbenchShell.InstrumentChangesRun.Text = _cachedGitDirtyCount.ToString(CultureInfo.InvariantCulture);
        WorkbenchShell.InstrumentBranchRun.Text = string.IsNullOrWhiteSpace(branch) ? "master" : branch;
        var ghAvailable = _cachedGithubCliStatus.Contains("已安装", StringComparison.OrdinalIgnoreCase);
        WorkbenchShell.InstrumentGhRun.Text = ghAvailable ? "✓ gh" : "× gh";
    }

    internal void RefreshGitChanges()
    {
        var workspace = EnsureGitWorkspaceContext();
        var previous = WorkbenchShell.GitChangesList.SelectedValue as string;
        _gitChanges.Clear();
        var status = RunGit("status --porcelain=v1 --branch");
        if (!status.Success)
        {
            _gitChanges.Add(new GitChangeViewModel("", "git status failed", status.Output, ""));
            WorkbenchShell.ChangedFileDiffBox.Text = status.Output;
            return;
        }
        _cachedGithubCliStatus = GitHubCliStatus();

        var numstat = RunGit("diff --numstat HEAD --");
        var deltas = ParseNumstat(numstat.Success ? numstat.Output : "");
        foreach (var line in status.Output.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries))
        {
            if (line.StartsWith("## ", StringComparison.Ordinal))
            {
                continue;
            }
            if (line.Length < 4)
            {
                continue;
            }
            var code = line[..2].Trim();
            var path = NormalizeGitStatusPath(line[3..]);
            var delta = deltas.TryGetValue(path, out var value) ? value : "";
            _gitChanges.Add(new GitChangeViewModel(path, code.Length == 0 ? "modified" : code, delta, path));
        }
        if (!string.IsNullOrWhiteSpace(previous) && _gitChanges.Any(item => item.Path == previous))
        {
            WorkbenchShell.GitChangesList.SelectedValue = previous;
        }
        else if (_gitChanges.Count > 0)
        {
            WorkbenchShell.GitChangesList.SelectedIndex = 0;
        }
        else
        {
            WorkbenchShell.ChangedFileDiffBox.Text = "No local changes.";
        }
        _cachedGitDirtyCount = _gitChanges.Count;
        WorkbenchShell.EnvironmentStatusText.Text = $"{Path.GetFileName(workspace)} · {CurrentGitBranch(refresh: false)} · changes {_cachedGitDirtyCount}";
        RenderInstrumentSummary(CurrentGitBranch(refresh: false));
    }

    internal async Task RefreshGitChangesAsync(bool selectFirst = false, bool preferInline = false)
    {
        if (_gitChangesLoading)
        {
            return;
        }
        var previous = (preferInline ? ChatWorkspace.InlineGitChangesList.SelectedValue : WorkbenchShell.GitChangesList.SelectedValue) as string;
        var workspace = EnsureGitWorkspaceContext();
        var workspaceChanged = false;
        _gitChangesLoading = true;
        WorkbenchShell.RefreshGitChangesButton.IsEnabled = false;
        ChatWorkspace.InlineReviewChangesButton.IsEnabled = false;
        WorkbenchShell.ReviewChangesButton.IsEnabled = false;
        ChatWorkspace.InlineChangesTitleText.Text = "Scanning files...";
        try
        {
            var snapshot = await Task.Run(() => BuildGitChangeSnapshot(workspace));
            workspaceChanged = !string.Equals(
                workspace,
                ResolveGitWorkingDirectory(ActiveWorkspaceRoot(), _rootDir),
                StringComparison.OrdinalIgnoreCase);
            if (!workspaceChanged)
            {
                _gitChanges.Clear();
                foreach (var change in snapshot.Changes)
                {
                    _gitChanges.Add(change);
                }
                _cachedGitDirtyCount = _gitChanges.Count;
                _cachedGithubCliStatus = snapshot.GithubCliStatus;
                WorkbenchShell.GitHubCliStatusText.Text = _cachedGithubCliStatus;
                WorkbenchShell.EnvironmentStatusText.Text = $"{Path.GetFileName(workspace)} · {snapshot.BranchStatus} · changes {_cachedGitDirtyCount}";
                RenderInstrumentSummary(CurrentGitBranch(refresh: false));
                ChatWorkspace.InlineChangesTitleText.Text = $"Edited {_cachedGitDirtyCount} files";
                ChatWorkspace.InlineChangesMetaText.Text = snapshot.DeltaSummary;
                _gitChangesLoaded = true;
                if (!string.IsNullOrWhiteSpace(previous) && _gitChanges.Any(item => item.Path == previous))
                {
                    WorkbenchShell.GitChangesList.SelectedValue = previous;
                    ChatWorkspace.InlineGitChangesList.SelectedValue = previous;
                }
                else if (selectFirst && _gitChanges.Count > 0)
                {
                    if (preferInline)
                    {
                        ChatWorkspace.InlineGitChangesList.SelectedIndex = 0;
                        WorkbenchShell.GitChangesList.SelectedItem = ChatWorkspace.InlineGitChangesList.SelectedItem;
                    }
                    else
                    {
                        WorkbenchShell.GitChangesList.SelectedIndex = 0;
                        ChatWorkspace.InlineGitChangesList.SelectedItem = WorkbenchShell.GitChangesList.SelectedItem;
                    }
                }
                if (_gitChanges.Count == 0)
                {
                    WorkbenchShell.ChangedFileDiffBox.Text = "No local changes.";
                    ChatWorkspace.InlineChangedFileDiffBox.Text = "";
                    ChatWorkspace.InlineChangedFileDiffBox.Visibility = Visibility.Collapsed;
                }
            }
        }
        finally
        {
            WorkbenchShell.RefreshGitChangesButton.IsEnabled = true;
            ChatWorkspace.InlineReviewChangesButton.IsEnabled = true;
            WorkbenchShell.ReviewChangesButton.IsEnabled = true;
            _gitChangesLoading = false;
            if (workspaceChanged)
            {
                _ = RefreshGitChangesAsync(selectFirst, preferInline);
            }
        }
    }

    internal GitChangeSnapshot BuildGitChangeSnapshot(string? workspace = null)
    {
        var changes = new List<GitChangeViewModel>();
        var status = RunGit("status --porcelain=v1 --branch", workspace);
        if (!status.Success)
        {
            changes.Add(new GitChangeViewModel("", "git status failed", "", status.Output));
            return new GitChangeSnapshot(changes, "git status failed", GitHubCliStatus(), "git unavailable");
        }
        var numstat = RunGit("diff --numstat HEAD --", workspace);
        var deltas = ParseNumstat(numstat.Success ? numstat.Output : "");
        var added = 0;
        var removed = 0;
        foreach (var line in status.Output.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries))
        {
            if (line.StartsWith("## ", StringComparison.Ordinal))
            {
                continue;
            }
            if (line.Length < 4)
            {
                continue;
            }
            var code = line[..2].Trim();
            var path = NormalizeGitStatusPath(line[3..]);
            var delta = deltas.TryGetValue(path, out var value) ? value : "";
            AccumulateDelta(delta, ref added, ref removed);
            changes.Add(new GitChangeViewModel(path, code.Length == 0 ? "modified" : code, delta, path));
        }
        var branchStatus = GitBranchStatusFromPorcelain(status.Output);
        var summary = changes.Count == 0 ? $"{branchStatus} · +0 -0" : $"{branchStatus} · +{added} -{removed}";
        return new GitChangeSnapshot(changes, summary, GitHubCliStatus(), branchStatus);
    }

    internal string GitHubCliStatus()
    {
        if (ResolveCommandPath("gh") is null)
        {
            var remote = RunGit("remote get-url origin");
            return remote.Success && !string.IsNullOrWhiteSpace(remote.Output)
                ? "GitHub CLI 未安装 · 本地 Git/remote 可用"
                : "GitHub CLI 未安装 · 仅本地 Git 可用";
        }
        return "GitHub CLI 已安装 · 授权状态未验证";
    }

    internal string GitBranchStatusFromPorcelain(string statusOutput)
    {
        var branchLine = statusOutput
            .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries)
            .FirstOrDefault(line => line.StartsWith("## ", StringComparison.Ordinal));
        if (string.IsNullOrWhiteSpace(branchLine))
        {
            return CurrentGitBranch(refresh: false);
        }
        var text = branchLine[3..].Trim();
        var branch = text.Split("...", 2, StringSplitOptions.None)[0].Trim();
        var upstream = text.Contains("...", StringComparison.Ordinal) ? text.Split("...", 2, StringSplitOptions.None)[1].Split(" [", 2, StringSplitOptions.None)[0].Trim() : "";
        var ahead = ParseGitCounter(text, "ahead");
        var behind = ParseGitCounter(text, "behind");
        var sync = upstream.Length == 0
            ? "no upstream"
            : ahead == 0 && behind == 0
                ? $"synced with {upstream}"
                : $"{(ahead > 0 ? $"ahead {ahead}" : "")}{(ahead > 0 && behind > 0 ? ", " : "")}{(behind > 0 ? $"behind {behind}" : "")}";
        return $"{branch} · {sync}";
    }

    internal static int ParseGitCounter(string text, string key)
    {
        var marker = $"{key} ";
        if (!text.Contains(marker, StringComparison.OrdinalIgnoreCase))
        {
            return 0;
        }
        var tail = text.Split(marker, 2, StringSplitOptions.None)[1];
        var raw = tail.Split(new[] { ",", "]" }, StringSplitOptions.None)[0].Trim();
        return int.TryParse(raw, out var parsed) ? parsed : 0;
    }

    internal static void AccumulateDelta(string delta, ref int added, ref int removed)
    {
        foreach (var part in delta.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            if (part.StartsWith("+", StringComparison.Ordinal) && int.TryParse(part[1..], out var add))
            {
                added += add;
            }
            else if (part.StartsWith("-", StringComparison.Ordinal) && int.TryParse(part[1..], out var remove))
            {
                removed += remove;
            }
        }
    }

    internal static Dictionary<string, string> ParseNumstat(string text)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var line in text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries))
        {
            var parts = line.Split('\t');
            if (parts.Length < 3)
            {
                continue;
            }
            var added = parts[0] == "-" ? "?" : parts[0];
            var removed = parts[1] == "-" ? "?" : parts[1];
            result[NormalizeGitStatusPath(parts[^1])] = $"+{added} -{removed}";
        }
        return result;
    }

    internal static string NormalizeGitStatusPath(string value)
    {
        var path = value.Trim();
        var arrow = path.IndexOf(" -> ", StringComparison.Ordinal);
        if (arrow >= 0)
        {
            path = path[(arrow + 4)..].Trim();
        }
        return path.Trim('"').Replace('\\', '/');
    }

}


