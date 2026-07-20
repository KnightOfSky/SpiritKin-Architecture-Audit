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
    internal async Task RefreshSelectedChangeDiffAsync(bool forceFirst = false, bool preferInline = false)
    {
        var list = preferInline ? ChatWorkspace.InlineGitChangesList : WorkbenchShell.GitChangesList;
        if (_syncingGitSelection)
        {
            return;
        }
        if (forceFirst && list.SelectedItem is null && _gitChanges.Count > 0)
        {
            list.SelectedIndex = 0;
        }
        if (list.SelectedItem is not GitChangeViewModel selected || string.IsNullOrWhiteSpace(selected.Path))
        {
            WorkbenchShell.ChangedFileDiffBox.Text = "Select a changed file to preview diff.";
            ChatWorkspace.InlineChangedFileDiffBox.Text = WorkbenchShell.ChangedFileDiffBox.Text;
            ChatWorkspace.InlineChangedFileDiffBox.Visibility = Visibility.Collapsed;
            return;
        }
        _syncingGitSelection = true;
        try
        {
            if (preferInline)
            {
                WorkbenchShell.GitChangesList.SelectedItem = selected;
            }
            else
            {
                ChatWorkspace.InlineGitChangesList.SelectedItem = selected;
            }
        }
        finally
        {
            _syncingGitSelection = false;
        }
        WorkbenchShell.ChangedFileDiffBox.Text = $"Loading diff for {selected.Path}...";
        ChatWorkspace.InlineChangedFileDiffBox.Text = WorkbenchShell.ChangedFileDiffBox.Text;
        ChatWorkspace.InlineChangedFileDiffBox.Visibility = Visibility.Visible;
        var diffText = await Task.Run(() => BuildGitDiffText(selected.Path));
        WorkbenchShell.ChangedFileDiffBox.Text = diffText;
        ChatWorkspace.InlineChangedFileDiffBox.Text = diffText;
        ChatWorkspace.InlineChangedFileDiffBox.Visibility = Visibility.Visible;
    }

    private string BuildGitDiffText(string path)
    {
        var diff = RunGit($"diff -- {QuoteArg(path)}");
        if (diff.Success && !string.IsNullOrWhiteSpace(diff.Output))
        {
            return diff.Output;
        }
        var staged = RunGit($"diff --cached -- {QuoteArg(path)}");
        return staged.Success && !string.IsNullOrWhiteSpace(staged.Output)
            ? staged.Output
            : $"No textual diff for {path}.";
    }

    internal async void GitChangeItem_MouseEnter(object sender, MouseEventArgs e)
    {
        if (sender is not FrameworkElement { Tag: string path } || string.IsNullOrWhiteSpace(path))
        {
            return;
        }
        var match = _gitChanges.FirstOrDefault(item => string.Equals(item.Path, path, StringComparison.OrdinalIgnoreCase));
        if (match is null)
        {
            return;
        }
        WorkbenchShell.GitChangesList.SelectedItem = match;
        ChatWorkspace.InlineGitChangesList.SelectedItem = match;
        await RefreshSelectedChangeDiffAsync(preferInline: true);
    }

    internal async Task UndoSelectedGitChangeAsync(bool preferInline = false)
    {
        var list = preferInline ? ChatWorkspace.InlineGitChangesList : WorkbenchShell.GitChangesList;
        if (list.SelectedItem is not GitChangeViewModel selected || string.IsNullOrWhiteSpace(selected.Path))
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "请先选择要撤销的文件。";
            return;
        }
        if (!ConfirmDestructiveAction("撤销文件改动", $"确定要撤销这个文件的本地改动吗？{Environment.NewLine}{selected.Path}"))
        {
            return;
        }
        var result = RunGit($"restore --staged --worktree -- {QuoteArg(selected.Path)}");
        if (!result.Success)
        {
            result = RunGit($"checkout -- {QuoteArg(selected.Path)}");
        }
        WorkspaceSidebar.ConnectionStatusText.Text = result.Success ? $"已撤销：{selected.Path}" : $"撤销失败：{result.Output}";
        await RefreshGitChangesAsync(selectFirst: true, preferInline);
        await SaveStateAsync();
    }

    internal void OpenCommitPushMenu()
    {
        var menu = new ContextMenu { PlacementTarget = WorkbenchShell.CommitPushButton, Placement = PlacementMode.Bottom };
        AddDisabledMenuHeader(menu, $"Git: {ChatWorkspace.InlineChangesMetaText.Text}");
        AddContextMenuItem(menu, "Refresh changes", async (_, _) => await RefreshGitChangesAsync(selectFirst: true));
        menu.Items.Add(CreateStyledSeparator());
        AddContextMenuItem(menu, "Commit changes...", async (_, _) => await CommitGitChangesAsync());
        AddContextMenuItem(menu, "Push current branch", async (_, _) => await PushCurrentBranchAsync());
        AddContextMenuItem(menu, "Copy git status", (_, _) =>
        {
            var result = RunGit("status --short --branch");
            Clipboard.SetText(result.Output);
            WorkspaceSidebar.ConnectionStatusText.Text = "已复制 git status。";
        });
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    private async Task CommitGitChangesAsync()
    {
        await RefreshGitChangesAsync(selectFirst: true);
        if (_gitChanges.Count == 0)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "没有可提交的变更。";
            return;
        }
        var message = PromptText("提交变更", "提交说明", $"桌面端更新 {DateTime.Now:yyyy-MM-dd HH:mm}");
        if (string.IsNullOrWhiteSpace(message))
        {
            return;
        }
        var add = RunGit("add -A");
        var commit = add.Success ? RunGit($"commit -m {QuoteArg(message.Trim())}") : add;
        WorkspaceSidebar.ConnectionStatusText.Text = commit.Success ? "提交完成。" : $"提交失败：{commit.Output}";
        await RefreshGitChangesAsync(selectFirst: true);
        await SaveStateAsync();
    }

    private async Task PushCurrentBranchAsync()
    {
        var branch = CurrentGitBranch(refresh: true);
        if (!ConfirmDestructiveAction("推送当前分支", $"确定要推送当前分支吗？{Environment.NewLine}{branch}"))
        {
            return;
        }
        var result = RunGit($"push -u origin {QuoteArg(branch)}");
        WorkspaceSidebar.ConnectionStatusText.Text = result.Success ? $"已推送：{branch}" : $"推送失败：{result.Output}";
        await SaveStateAsync();
    }

    internal GitCommandResult RunGit(string arguments) => RunGit(arguments, null);

    internal GitCommandResult RunGit(string arguments, string? workspace)
    {
        try
        {
            var workingDirectory = ResolveGitWorkingDirectory(workspace ?? ActiveWorkspaceRoot(), _rootDir);
            var startInfo = new ProcessStartInfo("git", arguments)
            {
                WorkingDirectory = workingDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var process = Process.Start(startInfo);
            if (process is null)
            {
                return new GitCommandResult(false, "git process did not start");
            }
            var outputTask = process.StandardOutput.ReadToEndAsync();
            var errorTask = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(5000))
            {
                try
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(1000);
                }
                catch
                {
                }
                return new GitCommandResult(false, "git command timed out after 5 seconds");
            }
            Task.WaitAll(new Task[] { outputTask, errorTask }, 1000);
            var output = outputTask.IsCompletedSuccessfully ? outputTask.Result : "";
            var error = errorTask.IsCompletedSuccessfully ? errorTask.Result : "";
            var text = string.IsNullOrWhiteSpace(output) ? error.Trim() : output.Trim();
            return new GitCommandResult(process.ExitCode == 0, text);
        }
        catch (Exception ex)
        {
            return new GitCommandResult(false, ex.Message);
        }
    }
}



