using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;

namespace SpiritKinDesktop;

/// <summary>
/// G1 服务看门狗 + G2 自检横幅（2026-07-07 自愈批次）。
/// 背景：LocalServiceRuntime.EnsureLocalServices() 早已实现"健康检查→抢占坏端口→重启服务"，
/// 但此前全项目零调用（只有启动时跑一次）——后端中途死掉桌面只会静默空白。
/// 本看门狗每 60s 探测网关健康，连续 2 次不健康即后台自动恢复；
/// 10 分钟窗口内恢复 ≥3 次仍失败则停手红字报警，防拉起风暴。
/// </summary>
public partial class MainWindow
{
    private readonly DispatcherTimer _serviceWatchdogTimer = new() { Interval = TimeSpan.FromSeconds(60) };
    private readonly List<DateTime> _serviceRecoveryAttempts = new();
    private readonly List<DateTime> _modelRecoveryAttempts = new();
    private int _serviceWatchdogUnhealthyStreak;
    private int _modelWatchdogUnhealthyStreak;
    private bool _serviceRecoveryInProgress;
    private bool _modelRecoveryInProgress;
    private bool _serviceRecoverySuspended;
    private bool _serviceHealthBannerIsTokenHint;
    private const int ServiceRecoveryWindowMinutes = 10;
    private const int ServiceRecoveryMaxInWindow = 3;
    private const int UnauthorizedStreakBannerThreshold = 3;

    private static readonly Brush ServiceBannerWarnBrush = new SolidColorBrush(Color.FromRgb(0x8A, 0x6D, 0x1A));
    private static readonly Brush ServiceBannerErrorBrush = new SolidColorBrush(Color.FromRgb(0xB4, 0x23, 0x18));

    private void StartServiceHealthWatchdog()
    {
        ServiceHealthBannerCloseButton.Click += (_, _) => HideServiceHealthBanner();
        ServiceHealthBannerRecoverButton.Click += async (_, _) => await RecoverDesktopSessionAsync();
        // 401 连击信号：统一请求入口埋点（DesktopApiRuntime），连续 401 = token 不匹配而非服务离线。
        ServiceHealthSignals.UnauthorizedStreakChanged += streak => Dispatcher.BeginInvoke(new Action(() => OnUnauthorizedStreakChanged(streak)));
        _serviceWatchdogTimer.Tick += async (_, _) => await ServiceWatchdogTickAsync();
        _serviceWatchdogTimer.Start();
    }

    private void StopServiceHealthWatchdog() => _serviceWatchdogTimer.Stop();

    private async Task ServiceWatchdogTickAsync()
    {
        if (_serviceRecoveryInProgress || _modelRecoveryInProgress || _serviceRecoverySuspended)
        {
            return;
        }
        bool healthy;
        try
        {
            healthy = await Task.Run(_workspaceController.CommandGatewayHealthy);
        }
        catch
        {
            healthy = false;
        }
        if (healthy)
        {
            _serviceWatchdogUnhealthyStreak = 0;
            if (ModelSelfHealingEnabled())
            {
                var modelHealthy = await _learningController.IsLlamaCppChatHealthyAsync();
                if (!modelHealthy)
                {
                    _modelWatchdogUnhealthyStreak++;
                    if (_modelWatchdogUnhealthyStreak >= 3)
                    {
                        await TryRecoverLocalModelAsync();
                    }
                    return;
                }
                _modelWatchdogUnhealthyStreak = 0;
            }
            // 只收掉"离线/恢复中"横幅；token 提示横幅由 401 信号自己清。
            if (ServiceHealthBannerElement.Visibility == Visibility.Visible && !_serviceHealthBannerIsTokenHint)
            {
                HideServiceHealthBanner();
            }
            return;
        }
        _serviceWatchdogUnhealthyStreak++;
        if (_serviceWatchdogUnhealthyStreak < 2)
        {
            // 单次探测失败可能只是瞬时抖动，等下一轮确认再动手。
            return;
        }
        await TryRecoverLocalServicesAsync();
    }

    private static bool ModelSelfHealingEnabled()
    {
        var enabled = Environment.GetEnvironmentVariable("SPIRITKIN_MODEL_SELF_HEAL");
        if (string.Equals(enabled, "0", StringComparison.OrdinalIgnoreCase)
            || string.Equals(enabled, "false", StringComparison.OrdinalIgnoreCase)
            || string.Equals(enabled, "off", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        var provider = Environment.GetEnvironmentVariable("SPIRITKIN_TEXT_PROVIDER")?.Trim();
        return string.IsNullOrWhiteSpace(provider)
            || provider.Equals("llamacpp", StringComparison.OrdinalIgnoreCase)
            || provider.Equals("llama_cpp", StringComparison.OrdinalIgnoreCase)
            || provider.Equals("llama.cpp", StringComparison.OrdinalIgnoreCase)
            || provider.Equals("llama-cpp", StringComparison.OrdinalIgnoreCase);
    }

    private async Task TryRecoverLocalModelAsync()
    {
        var now = DateTime.UtcNow;
        _modelRecoveryAttempts.RemoveAll(t => (now - t).TotalMinutes > ServiceRecoveryWindowMinutes);
        if (_modelRecoveryAttempts.Count >= ServiceRecoveryMaxInWindow)
        {
            ShowServiceHealthBanner(
                $"本地模型连续不可用，{ServiceRecoveryWindowMinutes} 分钟内已重启 {ServiceRecoveryMaxInWindow} 次，已停止自动恢复。请检查 state/llama.cpp/chat.log。",
                error: true);
            return;
        }

        _modelRecoveryAttempts.Add(now);
        var attempt = _modelRecoveryAttempts.Count;
        _modelRecoveryInProgress = true;
        ShowServiceHealthBanner($"检测到本地模型不可用，正在重启 llama.cpp（第 {attempt} 次）…");
        WorkspaceSidebar.ConnectionStatusText.Text = "模型无响应，正在自动恢复 llama.cpp…";
        DesktopDiagnosticLog.Write(_rootDir, "model", "self_heal_llamacpp", "started", $"attempt={attempt}");
        try
        {
            _learningController.RestartLlamaCppAfterHealthFailure();
            var recovered = false;
            for (var probe = 0; probe < 24; probe++)
            {
                await Task.Delay(TimeSpan.FromSeconds(5));
                if (await _learningController.IsLlamaCppChatHealthyAsync())
                {
                    recovered = true;
                    break;
                }
            }
            if (recovered)
            {
                _modelWatchdogUnhealthyStreak = 0;
                WorkspaceSidebar.ConnectionStatusText.Text = "本地模型已自动恢复。";
                ShowServiceHealthBanner("本地模型已恢复。");
                DesktopDiagnosticLog.Write(_rootDir, "model", "self_heal_llamacpp", "completed", $"attempt={attempt}");
                _ = AutoHideServiceHealthBannerAsync("本地模型已恢复。");
            }
            else
            {
                ShowServiceHealthBanner($"llama.cpp 已重启，但模型在 120 秒内仍未就绪（第 {attempt} 次）。", error: true);
                DesktopDiagnosticLog.Write(_rootDir, "model", "self_heal_llamacpp", "failed", $"attempt={attempt}; timeout=120s");
            }
        }
        catch (Exception ex)
        {
            ShowServiceHealthBanner($"模型自动恢复失败：{ex.Message}", error: true);
            DesktopDiagnosticLog.Write(_rootDir, "model", "self_heal_llamacpp", "failed", ex.Message);
        }
        finally
        {
            _modelRecoveryInProgress = false;
        }
    }

    private async Task TryRecoverLocalServicesAsync()
    {
        var now = DateTime.UtcNow;
        _serviceRecoveryAttempts.RemoveAll(t => (now - t).TotalMinutes > ServiceRecoveryWindowMinutes);
        if (_serviceRecoveryAttempts.Count >= ServiceRecoveryMaxInWindow)
        {
            _serviceRecoverySuspended = true;
            ShowServiceHealthBanner(
                $"后端服务离线，{ServiceRecoveryWindowMinutes} 分钟内自动恢复已达 {ServiceRecoveryMaxInWindow} 次仍失败，已停止尝试——请查看 state/logs 日志或用 启动桌面.bat 重启。",
                error: true);
            return;
        }
        _serviceRecoveryAttempts.Add(now);
        var attempt = _serviceRecoveryAttempts.Count;
        ShowServiceHealthBanner($"检测到后端服务离线，正在自动恢复…（第 {attempt} 次）");
        WorkspaceSidebar.ConnectionStatusText.Text = "检测到后端服务离线，正在自动恢复…";
        _serviceRecoveryInProgress = true;
        try
        {
            // EnsureLocalServices 内部有 Thread.Sleep / 同步健康探测，禁止在 UI 线程直接调（禁改区注意事项）。
            await Task.Run(_workspaceController.EnsureLocalServices);
            var recovered = await Task.Run(_workspaceController.CommandGatewayHealthy);
            if (recovered)
            {
                _serviceWatchdogUnhealthyStreak = 0;
                WorkspaceSidebar.ConnectionStatusText.Text = "后端服务已自动恢复。";
                ShowServiceHealthBanner("后端服务已恢复。");
                _ = AutoHideServiceHealthBannerAsync("后端服务已恢复。");
                // 恢复后联动：刷新服务面板；worker 依赖网关推送，同步一次控件状态，
                // 死掉的 worker 由既有 EnsureCollaborationWorkersForAgents 在下一条协作消息时重新拉起。
                await _servicesController.LoadServicesAsync();
                _contextController.SyncCollaborationWorkerControls();
            }
            else
            {
                ShowServiceHealthBanner($"自动恢复未成功（第 {attempt} 次），将在下个探测周期重试。", error: true);
            }
        }
        catch (Exception ex)
        {
            ShowServiceHealthBanner($"自动恢复失败：{ex.Message}", error: true);
        }
        finally
        {
            _serviceRecoveryInProgress = false;
        }
    }

    private void OnUnauthorizedStreakChanged(int streak)
    {
        if (streak >= UnauthorizedStreakBannerThreshold)
        {
            _serviceHealthBannerIsTokenHint = true;
            ShowServiceHealthBanner($"网关连续返回 401（token 不匹配，已 {streak} 次）。可直接恢复当前窗口会话。", error: true, tokenHint: true);
            return;
        }
        if (streak == 0 && _serviceHealthBannerIsTokenHint)
        {
            HideServiceHealthBanner();
        }
    }

    private void ShowServiceHealthBanner(string text, bool error = false, bool tokenHint = false)
    {
        _serviceHealthBannerIsTokenHint = tokenHint;
        ServiceHealthBannerText.Text = text;
        ServiceHealthBannerText.Foreground = error ? ServiceBannerErrorBrush : ServiceBannerWarnBrush;
        ServiceHealthBannerRecoverButton.Visibility = error || tokenHint ? Visibility.Visible : Visibility.Collapsed;
        ServiceHealthBannerElement.Visibility = Visibility.Visible;
    }

    private void HideServiceHealthBanner()
    {
        _serviceHealthBannerIsTokenHint = false;
        ServiceHealthBannerElement.Visibility = Visibility.Collapsed;
    }

    private async Task RecoverDesktopSessionAsync()
    {
        if (_serviceRecoveryInProgress)
        {
            return;
        }
        _serviceRecoveryInProgress = true;
        _serviceRecoverySuspended = false;
        _serviceRecoveryAttempts.Clear();
        ServiceHealthBannerRecoverButton.IsEnabled = false;
        ShowServiceHealthBanner("正在恢复当前窗口会话…", tokenHint: true);
        DesktopDiagnosticLog.Write(_rootDir, "session", "recover_window_session", "started");
        try
        {
            var launchStatePath = Path.Combine(_rootDir, "state", "run", "desktop_console.json");
            if (File.Exists(launchStatePath))
            {
                using var launchState = JsonDocument.Parse(await File.ReadAllTextAsync(launchStatePath));
                if (launchState.RootElement.TryGetProperty("session_token", out var tokenValue))
                {
                    var token = tokenValue.GetString()?.Trim() ?? "";
                    if (token.Length > 0)
                    {
                        WorkspaceSidebar.TokenBox.Password = token;
                    }
                }
            }

            await Task.Run(_workspaceController.EnsureLocalServices);
            using var request = new HttpRequestMessage(HttpMethod.Get, _workspaceController.DesktopStateUrl());
            _workspaceController.ApplyAuth(request);
            using var response = await _http.SendAsync(request);
            ServiceHealthSignals.RecordHttpStatus(response.StatusCode);
            response.EnsureSuccessStatusCode();

            _serviceWatchdogUnhealthyStreak = 0;
            await _runtimeController.RefreshAllAsync();
            ShowServiceHealthBanner("当前窗口会话已恢复。");
            DesktopDiagnosticLog.Write(_rootDir, "session", "recover_window_session", "completed");
            _ = AutoHideServiceHealthBannerAsync("当前窗口会话已恢复。");
        }
        catch (Exception ex)
        {
            ShowServiceHealthBanner($"窗口会话恢复失败：{ex.Message}", error: true, tokenHint: true);
            DesktopDiagnosticLog.Write(_rootDir, "session", "recover_window_session", "failed", ex.Message);
        }
        finally
        {
            ServiceHealthBannerRecoverButton.IsEnabled = true;
            _serviceRecoveryInProgress = false;
        }
    }

    private async Task AutoHideServiceHealthBannerAsync(string expectedText)
    {
        await Task.Delay(TimeSpan.FromSeconds(6));
        // 只有横幅内容没被后续状态覆盖时才自动收起。
        if (ServiceHealthBannerText.Text == expectedText)
        {
            HideServiceHealthBanner();
        }
    }
}
