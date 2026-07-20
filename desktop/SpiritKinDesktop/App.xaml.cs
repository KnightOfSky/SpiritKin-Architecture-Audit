using System.Configuration;
using System.Data;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Threading.Tasks;
using System.Windows;

namespace SpiritKinDesktop;

/// <summary>
/// Interaction logic for App.xaml
/// </summary>
public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // 主题初始化：读本地注册表缓存的上次主题偏好（同步、无网络往返），窗口首帧即以正确日/夜主题打开；
        // 共享状态（State.Settings）在本地服务起来后再回放校正，正常与缓存一致，不再有"先亮后暗"的闪烁。
        var sw = System.Diagnostics.Stopwatch.StartNew();
        ThemeManager.ApplyMode(ThemeManager.LoadCachedMode());
        sw.Stop();
        System.Diagnostics.Debug.WriteLine($"[ThemeManager] ApplyMode took {sw.ElapsedMilliseconds}ms");

        // 全局异常兜底：async void 事件处理器里任何未捕获异常（典型：网关瞬断的 HttpRequestException）
        // 都会走 DispatcherUnhandledException；没有这层，一次网络抖动就把整个桌面杀掉（2026-07-07 闪退实测）。
        DispatcherUnhandledException += (_, args) =>
        {
            LogUnhandled("dispatcher", args.Exception);
            args.Handled = true;
        };
        TaskScheduler.UnobservedTaskException += (_, args) =>
        {
            LogUnhandled("task", args.Exception);
            args.SetObserved();
        };
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            // 非 UI 线程异常无法拦截进程终止，只能留尸检日志。
            LogUnhandled("appdomain", args.ExceptionObject as Exception);
        };

        if (HasArgument(e.Args, "--smoke-startup"))
        {
            try
            {
                var smokeWindow = new MainWindow();
                smokeWindow.RunStartupSmokeChecks();
                Shutdown(0);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"SpiritKinDesktop smoke startup failed: {ex}");
                Shutdown(1);
            }
            return;
        }

        var window = new MainWindow();
        MainWindow = window;
        window.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        ThemeManager.Shutdown();
        base.OnExit(e);
    }

    private static void LogUnhandled(string source, Exception? exception)
    {
        if (IsExpectedShutdownNoise(exception))
        {
            return;
        }
        try
        {
            var dir = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "state", "logs");
            dir = Path.GetFullPath(dir);
            Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, "desktop_unhandled.log");
            if (File.Exists(path) && new FileInfo(path).Length > 8 * 1024 * 1024)
            {
                var archive = Path.Combine(dir, $"desktop_unhandled.{DateTime.Now:yyyyMMdd-HHmmss}.log");
                File.Move(path, archive);
            }
            var line = $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] [{source}] {exception}{Environment.NewLine}";
            File.AppendAllText(path, line);
        }
        catch
        {
            // 尸检日志本身不能再抛。
        }
    }

    private static bool IsExpectedShutdownNoise(Exception? exception)
    {
        if (exception is null || exception is OperationCanceledException)
        {
            return true;
        }
        if (exception is SocketException socket
            && socket.SocketErrorCode is SocketError.OperationAborted or SocketError.Interrupted)
        {
            return true;
        }
        if (exception is AggregateException aggregate)
        {
            var flattened = aggregate.Flatten().InnerExceptions;
            return flattened.Count > 0 && flattened.All(IsExpectedShutdownNoise);
        }
        return false;
    }

    private static bool HasArgument(string[] args, string expected)
    {
        foreach (var arg in args)
        {
            if (string.Equals(arg, expected, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }
        return false;
    }
}
