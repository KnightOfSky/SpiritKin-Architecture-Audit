using System;
using System.Net;
using System.Threading;

namespace SpiritKinDesktop;

/// <summary>
/// G2 埋点：网关请求统一入口的健康信号收集。
/// 连续 401 通常意味着桌面 token 与网关 token 不匹配（例如网关被外部重启换了 token），
/// 此时界面只会静默失败——看门狗横幅订阅本信号给出明确提示。
/// </summary>
internal static class ServiceHealthSignals
{
    private static int _consecutiveUnauthorized;

    /// <summary>连续 401 次数变化（任意成功请求清零）。可能在任意线程触发，订阅方自行调度到 UI 线程。</summary>
    internal static event Action<int>? UnauthorizedStreakChanged;

    internal static void RecordHttpStatus(HttpStatusCode status)
    {
        if (status == HttpStatusCode.Unauthorized)
        {
            var streak = Interlocked.Increment(ref _consecutiveUnauthorized);
            RaiseSafely(streak);
            return;
        }
        if ((int)status < 400 && Interlocked.Exchange(ref _consecutiveUnauthorized, 0) > 0)
        {
            RaiseSafely(0);
        }
    }

    private static void RaiseSafely(int streak)
    {
        try
        {
            UnauthorizedStreakChanged?.Invoke(streak);
        }
        catch
        {
            // 信号回调不允许影响业务请求路径。
        }
    }
}
