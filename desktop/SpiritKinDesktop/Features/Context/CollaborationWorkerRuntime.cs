using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed partial class ContextController
{
    private static readonly string[] CollaborationWorkerAllAgents = ["claude_code", "codex", "cloud_model"];
    private static readonly string[] CollaborationWorkerBuiltInLocalAgents = ["main_text", "programming", "vision_model", "video_animation", "game_development", "ecommerce"];
    // 用户主动停止过的 worker 不参与自动重启，直到再次手动/自动启动。
    private readonly HashSet<string> _userStoppedCollaborationAgents = new(StringComparer.OrdinalIgnoreCase);
    // 自动重启窗口限次：防止启动即崩的 worker 进入无限重启循环。
    private readonly Dictionary<string, List<DateTime>> _collaborationWorkerRestartHistory = new(StringComparer.OrdinalIgnoreCase);
    private const int CollaborationWorkerRestartWindowSeconds = 120;
    private const int CollaborationWorkerRestartMaxInWindow = 3;

    internal Task StartCollaborationWorkerAsync()
    {
        var agents = SelectedCollaborationWorkerAgents();
        if (agents.Count == 0)
        {
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = "请选择要启动的协作 Agent。";
            return Task.CompletedTask;
        }

        var started = new List<string>();
        var skipped = new List<string>();
        foreach (var agent in agents)
        {
            if (StartCollaborationWorker(agent, WorkbenchShell.ManagementPanels.CollaborationWorkerThreadBox.Text.Trim(), WorkbenchShell.ManagementPanels.CollaborationWorkerDryRunBox.IsChecked == true))
            {
                started.Add(agent);
            }
            else
            {
                skipped.Add(agent);
            }
        }
        SyncCollaborationWorkerControls();

        if (started.Count > 0)
        {
            var suffix = skipped.Count > 0 ? $"；已运行：{string.Join(", ", skipped)}" : "";
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"worker 已启动：{string.Join(", ", started)}{suffix}";
        }
        else
        {
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"worker 已在运行：{string.Join(", ", skipped)}";
        }
        return Task.CompletedTask;
    }

    private bool StartCollaborationWorker(string agent, string threadId = "", bool dryRun = false)
    {
        agent = NormalizeCollaborationWorkerAgent(agent);
        if (string.IsNullOrWhiteSpace(agent))
        {
            return false;
        }
        var scope = (threadId ?? "").Trim();
        lock (_collaborationWorkerLock)
        {
            if (_collaborationWorkerProcesses.TryGetValue(agent, out var existing) && !existing.HasExited)
            {
                var existingScope = _collaborationWorkerScopes.TryGetValue(agent, out var value) ? value : "";
                if (string.IsNullOrWhiteSpace(scope) && !string.IsNullOrWhiteSpace(existingScope))
                {
                    try
                    {
                        existing.Kill(entireProcessTree: true);
                    }
                    catch
                    {
                        // Best effort replacement: auto collaboration workers must consume every thread.
                    }
                    _collaborationWorkerProcesses.Remove(agent);
                    _collaborationWorkerScopes.Remove(agent);
                    existing.Dispose();
                }
                else
                {
                    return false;
                }
            }
            if (existing is not null)
            {
                _collaborationWorkerProcesses.Remove(agent);
                _collaborationWorkerScopes.Remove(agent);
                existing.Dispose();
            }
        }

        var scriptPath = Path.Combine(_rootDir, "scripts", "collaboration_agent_worker.py");
        if (!File.Exists(scriptPath))
        {
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"找不到 worker 脚本：{scriptPath}";
            return false;
        }

        var logDir = Path.Combine(_rootDir, "state", "logs");
        Directory.CreateDirectory(logDir);
        var logKey = SafeFileName(agent);
        var outPath = Path.Combine(logDir, $"collaboration_worker_{logKey}.out.log");
        var errPath = Path.Combine(logDir, $"collaboration_worker_{logKey}.err.log");
        AppendCollaborationWorkerLog(outPath, string.IsNullOrWhiteSpace(scope)
            ? $"[{DateTime.Now:g}] starting {agent} scope=all_threads"
            : $"[{DateTime.Now:g}] starting {agent} scope={scope}");

        var startInfo = new ProcessStartInfo("python")
        {
            WorkingDirectory = _rootDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        startInfo.ArgumentList.Add("-u");
        startInfo.ArgumentList.Add(scriptPath);
        startInfo.ArgumentList.Add("--api");
        startInfo.ArgumentList.Add(_workspaceController.ApiBase());
        startInfo.ArgumentList.Add("--agent");
        startInfo.ArgumentList.Add(agent);
        startInfo.ArgumentList.Add("--transport");
        startInfo.ArgumentList.Add("route_bus");
        startInfo.ArgumentList.Add("--interval");
        startInfo.ArgumentList.Add("5");

        if (!string.IsNullOrWhiteSpace(scope))
        {
            startInfo.ArgumentList.Add("--thread-id");
            startInfo.ArgumentList.Add(scope);
        }
        if (dryRun)
        {
            startInfo.ArgumentList.Add("--dry-run");
        }

        var runtime = _workspaceController.ActiveProjectRuntimeProfile();
        WorkspaceController.ApplyEnvironment(startInfo, _workspaceController.BuildProjectRuntimeEnvironment(runtime));
        startInfo.Environment["SPIRITKIN_DESKTOP_API"] = _workspaceController.ApiBase();
        var sessionToken = _workspaceController.SessionToken();
        if (!string.IsNullOrWhiteSpace(sessionToken))
        {
            startInfo.Environment["SPIRITKIN_MOBILE_TOKEN"] = sessionToken;
        }
        startInfo.Environment["PYTHONPATH"] = string.Join(
            Path.PathSeparator,
            new[] { _rootDir, startInfo.Environment.TryGetValue("PYTHONPATH", out var existingPythonPath) ? existingPythonPath : "" }
                .Where(path => !string.IsNullOrWhiteSpace(path)));
        startInfo.Environment["SPIRITKIN_COLLABORATION_SELF_HEAL"] = CollaborationSelfHealEnabled() ? "1" : "0";

        var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, args) =>
        {
            if (args.Data is not null)
            {
                AppendCollaborationWorkerLog(outPath, args.Data);
            }
        };
        process.ErrorDataReceived += (_, args) =>
        {
            if (args.Data is not null)
            {
                AppendCollaborationWorkerLog(errPath, args.Data);
            }
        };
        process.Exited += (_, _) =>
        {
            var exitCode = SafeExitCode(process);
            AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] exited {agent} code={exitCode}");
            if (Dispatcher.HasShutdownStarted || Dispatcher.HasShutdownFinished)
            {
                process.Dispose();
                return;
            }
            _ = Dispatcher.BeginInvoke(new Action(() =>
            {
                HandleCollaborationWorkerExitedOnDispatcher(agent, scope, dryRun, exitCode, outPath, process);
            }));
        };

        lock (_collaborationWorkerLock)
        {
            _collaborationWorkerProcesses[agent] = process;
            _collaborationWorkerScopes[agent] = scope;
        }
        try
        {
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }
        catch (Exception ex)
        {
            lock (_collaborationWorkerLock)
            {
                _collaborationWorkerProcesses.Remove(agent);
                _collaborationWorkerScopes.Remove(agent);
            }
            process.Dispose();
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"worker 启动失败：{agent} · {ex.Message}";
            return false;
        }
        _userStoppedCollaborationAgents.Remove(agent);
        RegisterCollaborationWorkerPid(agent, process);
        WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"worker 运行中：{agent}。日志：{ShortWorkspacePath(outPath)}";
        return true;
    }

    private void HandleCollaborationWorkerExitedOnDispatcher(string agent, string scope, bool dryRun, int exitCode, string outPath, Process process)
    {
        AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] exit-dispatch start {agent}");
        var disposed = false;
        try
        {
            lock (_collaborationWorkerLock)
            {
                if (_collaborationWorkerProcesses.TryGetValue(agent, out var current) && ReferenceEquals(current, process))
                {
                    _collaborationWorkerProcesses.Remove(agent);
                    _collaborationWorkerScopes.Remove(agent);
                }
            }
            AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] exit-dispatch unregistered memory {agent}");
            UnregisterCollaborationWorkerPid(agent);
            AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] exit-dispatch unregistered pid {agent}");
            try
            {
                SyncCollaborationWorkerControls();
                AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] exit-dispatch synced controls {agent}");
            }
            catch (Exception ex)
            {
                AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] exit-dispatch sync controls failed {agent}: {ex.GetType().Name}: {ex.Message}");
            }
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"worker 已退出：{agent}，退出码 {exitCode}。日志：{ShortWorkspacePath(outPath)}";
            process.Dispose();
            disposed = true;
            TryAutoRestartCollaborationWorker(agent, scope, dryRun, exitCode, outPath);
            AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] exit-dispatch auto-restart evaluated {agent}");
        }
        catch (Exception ex)
        {
            AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] exit-dispatch failed {agent}: {ex.GetType().Name}: {ex.Message}");
            try
            {
                UnregisterCollaborationWorkerPid(agent);
            }
            catch
            {
                // Best effort; diagnostic log above is the signal.
            }
            if (!disposed)
            {
                try
                {
                    process.Dispose();
                }
                catch
                {
                    // Disposal must not block auto-restart.
                }
            }
            TryAutoRestartCollaborationWorker(agent, scope, dryRun, exitCode, outPath);
        }
    }

    // ── worker 自愈：异常退出自动重启（窗口限次）+ PID 登记/孤儿清理 ──

    private void TryAutoRestartCollaborationWorker(string agent, string scope, bool dryRun, int exitCode, string outPath)
    {
        if (exitCode == 0 || _userStoppedCollaborationAgents.Contains(agent))
        {
            AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] auto-restart skipped for {agent}: exit={exitCode}, userStopped={_userStoppedCollaborationAgents.Contains(agent)}");
            return;
        }
        var now = DateTime.UtcNow;
        if (!_collaborationWorkerRestartHistory.TryGetValue(agent, out var history))
        {
            history = new List<DateTime>();
            _collaborationWorkerRestartHistory[agent] = history;
        }
        history.RemoveAll(item => (now - item).TotalSeconds > CollaborationWorkerRestartWindowSeconds);
        if (history.Count >= CollaborationWorkerRestartMaxInWindow)
        {
            AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] auto-restart suppressed for {agent}: {history.Count} restarts within {CollaborationWorkerRestartWindowSeconds}s");
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"worker {agent} 短时间内反复崩溃，已暂停自动重启。请查看日志：{ShortWorkspacePath(outPath)}";
            return;
        }
        history.Add(now);
        var delaySeconds = Math.Min(15, Math.Pow(2, history.Count));
        AppendCollaborationWorkerLog(outPath, $"[{DateTime.Now:g}] auto-restart {agent} in {delaySeconds:F0}s (attempt {history.Count}/{CollaborationWorkerRestartMaxInWindow}, exit {exitCode})");
        _ = Dispatcher.InvokeAsync(async () =>
        {
            await Task.Delay(TimeSpan.FromSeconds(delaySeconds));
            if (_userStoppedCollaborationAgents.Contains(agent) || Dispatcher.HasShutdownStarted)
            {
                return;
            }
            if (StartCollaborationWorker(agent, scope, dryRun))
            {
                WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"worker {agent} 异常退出（{exitCode}），已自动重启。";
            }
        });
    }

    private string CollaborationWorkerPidFilePath() => Path.Combine(_rootDir, "state", "collaboration", "worker_pids.json");

    private readonly object _collaborationWorkerPidFileLock = new();

    private void RegisterCollaborationWorkerPid(string agent, Process process)
    {
        try
        {
            lock (_collaborationWorkerPidFileLock)
            {
                var pids = ReadCollaborationWorkerPidFile();
                pids[agent] = process.Id;
                WriteCollaborationWorkerPidFile(pids);
            }
        }
        catch
        {
            // PID 登记只服务孤儿清理，失败不影响 worker 正常运行。
        }
    }

    private void UnregisterCollaborationWorkerPid(string agent)
    {
        try
        {
            lock (_collaborationWorkerPidFileLock)
            {
                var pids = ReadCollaborationWorkerPidFile();
                if (pids.Remove(agent))
                {
                    WriteCollaborationWorkerPidFile(pids);
                }
            }
        }
        catch
        {
        }
    }

    private Dictionary<string, int> ReadCollaborationWorkerPidFile()
    {
        var path = CollaborationWorkerPidFilePath();
        if (!File.Exists(path))
        {
            return new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        }
        try
        {
            var data = System.Text.Json.JsonSerializer.Deserialize<Dictionary<string, int>>(File.ReadAllText(path));
            return data is null
                ? new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
                : new Dictionary<string, int>(data, StringComparer.OrdinalIgnoreCase);
        }
        catch
        {
            return new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        }
    }

    private void WriteCollaborationWorkerPidFile(Dictionary<string, int> pids)
    {
        var path = CollaborationWorkerPidFilePath();
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, System.Text.Json.JsonSerializer.Serialize(pids));
    }

    // 桌面启动时清理上一次实例遗留的孤儿 worker：只 kill PID 文件里登记、且命令行确实是
    // collaboration_agent_worker 的进程，绝不按进程名匹配（避免误杀 bridge/gateway/frontend）。
    internal void CleanOrphanCollaborationWorkers()
    {
        Dictionary<string, int> pids;
        try
        {
            lock (_collaborationWorkerPidFileLock)
            {
                pids = ReadCollaborationWorkerPidFile();
            }
        }
        catch
        {
            return;
        }
        if (pids.Count == 0)
        {
            return;
        }
        var ownPids = new HashSet<int>();
        lock (_collaborationWorkerLock)
        {
            foreach (var process in _collaborationWorkerProcesses.Values)
            {
                try
                {
                    ownPids.Add(process.Id);
                }
                catch
                {
                }
            }
        }
        var killed = new List<string>();
        foreach (var (agent, pid) in pids.ToList())
        {
            if (ownPids.Contains(pid))
            {
                continue;
            }
            try
            {
                using var process = Process.GetProcessById(pid);
                if (!IsCollaborationWorkerCommandLine(pid))
                {
                    continue;
                }
                process.Kill(entireProcessTree: true);
                killed.Add($"{agent}(pid {pid})");
            }
            catch
            {
                // 进程已不存在或无权限：视为已清理。
            }
        }
        try
        {
            lock (_collaborationWorkerPidFileLock)
            {
                var current = ReadCollaborationWorkerPidFile();
                foreach (var (agent, pid) in pids)
                {
                    if (!ownPids.Contains(pid) && current.TryGetValue(agent, out var recorded) && recorded == pid)
                    {
                        current.Remove(agent);
                    }
                }
                WriteCollaborationWorkerPidFile(current);
            }
        }
        catch
        {
        }
        if (killed.Count > 0)
        {
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"已清理上次遗留的协作 worker：{string.Join(", ", killed)}";
        }
    }

    private bool CollaborationSelfHealEnabled()
    {
        try
        {
            return WorkbenchShell.ManagementPanels.CollaborationSelfHealBox.IsChecked == true;
        }
        catch
        {
            return false;
        }
    }

    private static bool IsCollaborationWorkerCommandLine(int pid)
    {
        try
        {
            using var searcher = new System.Management.ManagementObjectSearcher(
                $"SELECT CommandLine FROM Win32_Process WHERE ProcessId = {pid}");
            foreach (var item in searcher.Get())
            {
                var commandLine = item["CommandLine"]?.ToString() ?? "";
                return commandLine.Contains("collaboration_agent_worker", StringComparison.OrdinalIgnoreCase);
            }
        }
        catch
        {
        }
        return false;
    }

    internal void EnsureCollaborationWorkersForAgents(IEnumerable<string> agents, string threadId = "")
    {
        var targets = CollaborationWorkerTargetsForAgents(agents).ToList();
        if (targets.Count == 0)
        {
            return;
        }
        var started = new List<string>();
        foreach (var agent in targets)
        {
            if (StartCollaborationWorker(agent, threadId: "", dryRun: false))
            {
                started.Add(agent);
            }
        }
        SyncCollaborationWorkerControls();
        if (started.Count > 0)
        {
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"已自动启动协作 worker：{string.Join(", ", started)}";
        }
    }

    internal void StopSelectedCollaborationWorker()
    {
        var agents = SelectedCollaborationWorkerAgents();
        if (agents.Count == 0)
        {
            agents = RunningCollaborationWorkerAgents();
        }
        StopCollaborationWorkers(agents, killOnly: false);
    }

    internal void StopCollaborationWorkers(IEnumerable<string>? agents = null, bool killOnly = false)
    {
        var targets = (agents?.Select(NormalizeCollaborationWorkerAgent).Where(agent => !string.IsNullOrWhiteSpace(agent)).Distinct(StringComparer.OrdinalIgnoreCase).ToList())
            ?? RunningCollaborationWorkerAgents();
        var stopped = new List<string>();
        foreach (var agent in targets)
        {
            Process? process = null;
            lock (_collaborationWorkerLock)
            {
                if (_collaborationWorkerProcesses.TryGetValue(agent, out var current))
                {
                    process = current;
                    _collaborationWorkerProcesses.Remove(agent);
                    _collaborationWorkerScopes.Remove(agent);
                }
            }
            UnregisterCollaborationWorkerPid(agent);
            if (process is null)
            {
                continue;
            }
            // 只有真正停掉了进程才算“用户主动停止”，否则会永久压制自动重启。
            _userStoppedCollaborationAgents.Add(agent);
            try
            {
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);
                }
                stopped.Add(agent);
            }
            catch
            {
                // Best effort shutdown for background collaboration workers.
            }
        }
        SyncCollaborationWorkerControls();
        if (!killOnly)
        {
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = stopped.Count == 0
                ? "没有正在运行的 worker。"
                : $"已请求停止 worker：{string.Join(", ", stopped)}";
        }
    }

    private List<string> SelectedCollaborationWorkerAgents()
    {
        var selected = NormalizeCollaborationWorkerAgent(ComboText(WorkbenchShell.ManagementPanels.CollaborationWorkerAgentBox));
        if (string.Equals(selected, "all", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(selected))
        {
            return CollaborationWorkerAutoAgents().ToList();
        }
        return [selected];
    }

    private IEnumerable<string> CollaborationWorkerTargetsForAgents(IEnumerable<string> agents)
    {
        var targets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var raw in agents)
        {
            var agent = NormalizeCollaborationWorkerAgent(raw);
            if (string.Equals(agent, "all", StringComparison.OrdinalIgnoreCase))
            {
                foreach (var item in CollaborationWorkerAutoAgents())
                {
                    targets.Add(item);
                }
            }
            else if (CollaborationWorkerCanChat(agent))
            {
                targets.Add(agent);
            }
        }
        return targets.OrderBy(item => item, StringComparer.OrdinalIgnoreCase);
    }

    private IEnumerable<string> CollaborationWorkerAutoAgents()
    {
        var dynamicAgents = _collaborationParticipantOptions
            .Where(item => item.CanChat && !string.Equals(item.Kind, "worker", StringComparison.OrdinalIgnoreCase))
            .Select(item => item.ParticipantId)
            .Where(item => !string.IsNullOrWhiteSpace(item));
        return CollaborationWorkerAllAgents.Concat(CollaborationWorkerBuiltInLocalAgents).Concat(dynamicAgents).Distinct(StringComparer.OrdinalIgnoreCase);
    }

    private bool CollaborationWorkerCanChat(string agent)
    {
        if (CollaborationWorkerAllAgents.Contains(agent, StringComparer.OrdinalIgnoreCase))
        {
            return true;
        }
        if (CollaborationWorkerBuiltInLocalAgents.Contains(agent, StringComparer.OrdinalIgnoreCase))
        {
            return true;
        }
        return _collaborationParticipantOptions.Any(item =>
            item.CanChat
            && !string.Equals(item.Kind, "worker", StringComparison.OrdinalIgnoreCase)
            && string.Equals(item.ParticipantId, agent, StringComparison.OrdinalIgnoreCase));
    }

    private List<string> RunningCollaborationWorkerAgents()
    {
        lock (_collaborationWorkerLock)
        {
            return _collaborationWorkerProcesses
                .Where(item => !item.Value.HasExited)
                .Select(item => item.Key)
                .OrderBy(item => item, StringComparer.OrdinalIgnoreCase)
                .ToList();
        }
    }

    internal void SyncCollaborationWorkerControls()
    {
        var running = RunningCollaborationWorkerAgents();
        var hasRunning = running.Count > 0;
        WorkbenchShell.ManagementPanels.StartCollaborationWorkerButton.IsEnabled = SelectedCollaborationWorkerAgents().Any(agent => !running.Contains(agent, StringComparer.OrdinalIgnoreCase));
        WorkbenchShell.ManagementPanels.StopCollaborationWorkerButton.IsEnabled = hasRunning;
        if (hasRunning)
        {
            WorkbenchShell.ManagementPanels.CollaborationWorkerStatusText.Text = $"worker 运行中：{string.Join(", ", running)}";
        }
    }

    internal static string NormalizeCollaborationWorkerAgent(string value)
    {
        var text = (value ?? "").Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return "";
        }
        var key = new string(text.ToLowerInvariant().Where(char.IsLetterOrDigit).ToArray());
        return key switch
        {
            "all" or "全部协作agent" or "全部" => "all",
            "claudecode" or "claude" => "claude_code",
            "codexcli" or "codex" => "codex",
            "gpt" or "openai" or "cloudmodel" or "云端模型" => "cloud_model",
            "programming" or "编程agent" or "编程" => "programming",
            "visionmodel" or "视觉agent" or "视觉" => "vision_model",
            "gamedevelopment" or "游戏agent" or "游戏开发" => "game_development",
            "ecommerce" or "电商agent" or "电商" => "ecommerce",
            _ => text.ToLowerInvariant().Replace("-", "_"),
        };
    }

    private static int SafeExitCode(Process process)
    {
        try
        {
            return process.ExitCode;
        }
        catch
        {
            return -1;
        }
    }

    private static void AppendCollaborationWorkerLog(string path, string line)
    {
        try
        {
            File.AppendAllText(path, line + Environment.NewLine, Encoding.UTF8);
        }
        catch
        {
            // Logging is diagnostic only; do not crash the desktop shell on file IO races.
        }
    }

    private string ShortWorkspacePath(string path)
    {
        try
        {
            var relative = Path.GetRelativePath(_rootDir, path);
            return relative.StartsWith("..", StringComparison.Ordinal) ? path : relative;
        }
        catch
        {
            return path;
        }
    }
}
