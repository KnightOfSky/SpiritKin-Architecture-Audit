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
    internal void RefreshLocalServicePorts()
    {
        var overrides = LoadLocalServicePortOverrides();
        _frontendPort = ResolveLocalServicePort(RealtimeContract.PortEnvVars.Frontend, "frontend", RealtimeContract.DefaultPorts.Frontend, overrides);
        _eventBridgePort = ResolveLocalServicePort(RealtimeContract.PortEnvVars.EventBridge, "event_bridge", RealtimeContract.DefaultPorts.EventBridge, overrides);
        _commandGatewayPort = ResolveLocalServicePort(RealtimeContract.PortEnvVars.CommandGateway, "command_gateway", RealtimeContract.DefaultPorts.CommandGateway, overrides);
        _remoteWorkerPort = ResolveLocalServicePort(RealtimeContract.PortEnvVars.RemoteWorker, "remote_worker", RealtimeContract.DefaultPorts.RemoteWorker, overrides);
        _androidEndpointPort = ResolveLocalServicePort(RealtimeContract.PortEnvVars.AndroidEndpoint, "android_endpoint", RealtimeContract.DefaultPorts.AndroidEndpoint, overrides);
        _iosEndpointPort = ResolveLocalServicePort(RealtimeContract.PortEnvVars.IosEndpoint, "ios_endpoint", RealtimeContract.DefaultPorts.IosEndpoint, overrides);
    }

    private Dictionary<string, int> LoadLocalServicePortOverrides()
    {
        var configPath = ResolveLocalServicePortConfigPath();
        if (!File.Exists(configPath))
        {
            return new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        }
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(configPath));
            if (!doc.RootElement.TryGetProperty("overrides", out var overrides) || overrides.ValueKind != JsonValueKind.Object)
            {
                return new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            }
            var ports = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach (var item in overrides.EnumerateObject())
            {
                if (TryReadPort(item.Value, out var port))
                {
                    ports[item.Name] = port;
                }
            }
            return ports;
        }
        catch
        {
            return new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        }
    }

    private string ResolveLocalServicePortConfigPath()
    {
        var configured = Environment.GetEnvironmentVariable("SPIRITKIN_SERVICE_PORT_CONFIG_PATH");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            var expanded = Environment.ExpandEnvironmentVariables(configured.Trim());
            return Path.IsPathRooted(expanded) ? expanded : Path.GetFullPath(Path.Combine(_rootDir, expanded));
        }
        return Path.Combine(_rootDir, "state", "service_ports", "config.json");
    }

    private static int ResolveLocalServicePort(string envVar, string serviceId, int defaultPort, IReadOnlyDictionary<string, int> overrides)
    {
        if (TryReadPort(Environment.GetEnvironmentVariable(envVar), out var envPort))
        {
            return envPort;
        }
        return overrides.TryGetValue(serviceId, out var configuredPort) ? configuredPort : defaultPort;
    }

    internal static bool TryReadPort(JsonElement value, out int port)
    {
        port = 0;
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
        {
            port = number;
        }
        else if (value.ValueKind == JsonValueKind.String)
        {
            return TryReadPort(value.GetString(), out port);
        }
        return port >= 0 && port <= 65535;
    }

    internal static bool TryReadPort(string? value, out int port)
    {
        port = 0;
        if (string.IsNullOrWhiteSpace(value) || !int.TryParse(value.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed))
        {
            return false;
        }
        if (parsed < 0 || parsed > 65535)
        {
            return false;
        }
        port = parsed;
        return true;
    }

    internal void EnsureLocalServices()
    {
        RefreshLocalServicePorts();
        EnsureFrontendService();
        StartServiceIfNeeded("bridge", _eventBridgePort, "python", "-m backend.app.realtime_bridge");
        var commandGatewayScript = "from backend.app.codex_work_events import install; install(); from backend.app.command_gateway import main; main()";
        EnsureCommandGatewayService(commandGatewayScript);
        EnsureMobileControlService();
        EnsureIosStaticService();
    }

    private void EnsureMobileControlService()
    {
        if (MobileControlHealthy())
        {
            return;
        }
        if (PortOpen("127.0.0.1", _androidEndpointPort))
        {
            StopProcessListeningOnPort(_androidEndpointPort);
            Thread.Sleep(400);
        }
        StartServiceIfNeeded(
            "mobile_control",
            _androidEndpointPort,
            "python",
            $"-u scripts/mobile_link_receiver.py --host 127.0.0.1 --port {_androidEndpointPort}",
            requireHealthy: MobileControlHealthy);
    }

    private void EnsureIosStaticService()
    {
        if (IosStaticHealthy())
        {
            return;
        }
        if (PortOpen("127.0.0.1", _iosEndpointPort))
        {
            StopProcessListeningOnPort(_iosEndpointPort);
            Thread.Sleep(400);
        }
        StartServiceIfNeeded(
            "ios_static",
            _iosEndpointPort,
            "python",
            $"-u -m backend.app.static_frontend_server --host 127.0.0.1 --port {_iosEndpointPort} --directory {WorkbenchController.QuoteArg(_frontendDir)} --url-prefix /frontend",
            requireHealthy: IosStaticHealthy);
    }

    private void EnsureCommandGatewayService(string commandGatewayScript)
    {
        if (CommandGatewayHealthy())
        {
            return;
        }
        if (PortOpen("127.0.0.1", _commandGatewayPort))
        {
            StopProcessListeningOnPort(_commandGatewayPort);
            Thread.Sleep(400);
        }
        StartServiceIfNeeded("command_gateway", _commandGatewayPort, "python", $"-u -c {WorkbenchController.QuoteArg(commandGatewayScript)}", requireHealthy: CommandGatewayHealthy);
    }

    internal void EnsureFrontendService()
    {
        RefreshLocalServicePorts();
        if (FrontendHealthy())
        {
            return;
        }
        StopStaleFrontendServers();
        Thread.Sleep(300);
        var candidates = FrontendPortCandidates().Distinct().ToList();
        foreach (var port in candidates)
        {
            _frontendPort = port;
            if (FrontendHealthy())
            {
                return;
            }
            if (PortOpen("127.0.0.1", port))
            {
                continue;
            }
            StartServiceIfNeeded("frontend", _frontendPort, "python", $"-u -m backend.app.static_frontend_server --host 127.0.0.1 --port {_frontendPort} --directory {WorkbenchController.QuoteArg(_frontendDir)}", requireHealthy: FrontendHealthy);
            if (FrontendHealthy())
            {
                return;
            }
        }
        throw new InvalidOperationException($"3D 前端服务启动失败，已尝试端口 {string.Join(", ", candidates)}");
    }

    private int SelectFrontendPort()
    {
        foreach (var port in FrontendPortCandidates().Distinct())
        {
            _frontendPort = port;
            if (FrontendHealthy() || !PortOpen("127.0.0.1", port))
            {
                return port;
            }
        }
        return _frontendPort;
    }

    private IEnumerable<int> FrontendPortCandidates()
    {
        yield return _frontendPort;
        yield return RealtimeContract.DefaultPorts.Frontend;
        for (var port = 8791; port <= 8815; port++)
        {
            yield return port;
        }
    }

    private void StopStaleFrontendServers()
    {
        try
        {
            var script = "$frontendDir = " + WorkbenchController.QuotePowerShellLiteral(_frontendDir) + @"
$needle = $frontendDir.ToLowerInvariant()
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    (
        $_.CommandLine.ToLowerInvariant().Contains('-m http.server') -or
        $_.CommandLine.ToLowerInvariant().Contains('backend.app.static_frontend_server')
    ) -and
    $_.CommandLine.ToLowerInvariant().Contains($needle)
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}";
            using var process = Process.Start(new ProcessStartInfo(
                WorkbenchController.ResolveShellExecutable(),
                $"-NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand {WorkbenchController.EncodePowerShellCommand(script)}")
            {
                WorkingDirectory = _rootDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
            process?.WaitForExit(3000);
        }
        catch
        {
            // Best effort cleanup; the following port probing still prevents loading a bad endpoint.
        }
    }

    private void StartServiceIfNeeded(string name, int port, string fileName, string arguments, Func<bool>? requireHealthy = null)
    {
        if (requireHealthy?.Invoke() == true || (requireHealthy is null && PortOpen("127.0.0.1", port)))
        {
            return;
        }
        var logDir = Path.Combine(_rootDir, "state", "logs");
        Directory.CreateDirectory(logDir);
        var startInfo = BuildServiceStartInfo(fileName, arguments);
        var process = Process.Start(startInfo);
        if (process is null)
        {
            return;
        }
        CaptureServiceOutput(process, name, logDir);
        for (var i = 0; i < 25; i++)
        {
            if (requireHealthy?.Invoke() == true || (requireHealthy is null && PortOpen("127.0.0.1", port)))
            {
                break;
            }
            Thread.Sleep(200);
        }
    }

    internal void StartProcessServiceIfNeeded(string name, string processMatch, string fileName, string arguments)
    {
        if (ProcessServiceRunning(processMatch))
        {
            return;
        }
        var logDir = Path.Combine(_rootDir, "state", "logs");
        Directory.CreateDirectory(logDir);
        var startInfo = BuildServiceStartInfo(fileName, arguments);
        var process = Process.Start(startInfo);
        if (process is null)
        {
            return;
        }
        CaptureServiceOutput(process, name, logDir);
        for (var i = 0; i < 25; i++)
        {
            if (ProcessServiceRunning(processMatch) || process.HasExited)
            {
                break;
            }
            Thread.Sleep(200);
        }
    }

    internal ProcessStartInfo BuildServiceStartInfo(string fileName, string arguments)
    {
        var startInfo = new ProcessStartInfo(fileName, arguments)
        {
            WorkingDirectory = _rootDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        startInfo.Environment["SPIRITKIN_EVENTS_BIND_HOST"] = "127.0.0.1";
        startInfo.Environment["SPIRITKIN_EVENTS_HOST"] = "127.0.0.1";
        startInfo.Environment["SPIRITKIN_EVENTS_PORT"] = _eventBridgePort.ToString(CultureInfo.InvariantCulture);
        startInfo.Environment["SPIRITKIN_EVENTS_WS_URL"] = $"ws://127.0.0.1:{_eventBridgePort}";
        startInfo.Environment["SPIRITKIN_COMMAND_HOST"] = "127.0.0.1";
        startInfo.Environment["SPIRITKIN_COMMAND_PORT"] = _commandGatewayPort.ToString(CultureInfo.InvariantCulture);
        startInfo.Environment["SPIRITKIN_FRONTEND_PORT"] = _frontendPort.ToString(CultureInfo.InvariantCulture);
        startInfo.Environment["SPIRITKIN_REMOTE_WORKER_PORT"] = _remoteWorkerPort.ToString(CultureInfo.InvariantCulture);
        startInfo.Environment["SPIRITKIN_ANDROID_PORT"] = _androidEndpointPort.ToString(CultureInfo.InvariantCulture);
        startInfo.Environment["SPIRITKIN_IOS_PORT"] = _iosEndpointPort.ToString(CultureInfo.InvariantCulture);
        startInfo.Environment.TryAdd("SPIRITKIN_ASR_MIN_RMS", "700");
        startInfo.Environment.TryAdd("SPIRITKIN_ASR_NO_SPEECH_THRESHOLD", "0.96");
        startInfo.Environment.TryAdd("SPIRITKIN_ASR_LOW_LOGPROB_THRESHOLD", "-2.0");
        return startInfo;
    }

    internal static void CaptureServiceOutput(Process process, string name, string logDir)
    {
        var outPath = Path.Combine(logDir, $"wpf_{name}.out.log");
        var errPath = Path.Combine(logDir, $"wpf_{name}.err.log");
        process.OutputDataReceived += (_, args) =>
        {
            if (args.Data is not null)
            {
                _ = File.AppendAllTextAsync(outPath, args.Data + Environment.NewLine);
            }
        };
        process.ErrorDataReceived += (_, args) =>
        {
            if (args.Data is not null)
            {
                _ = File.AppendAllTextAsync(errPath, args.Data + Environment.NewLine);
            }
        };
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
    }

    internal bool ProcessServiceRunning(string processMatch)
    {
        if (string.IsNullOrWhiteSpace(processMatch))
        {
            return false;
        }
        try
        {
            var script = "$needle = " + WorkbenchController.QuotePowerShellLiteral(processMatch.ToLowerInvariant()) + @"
$current = $PID
$found = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.ProcessId -ne $current -and
    $_.CommandLine.ToLowerInvariant().Contains($needle)
} | Select-Object -First 1
if ($found) { '1' }";
            using var process = Process.Start(new ProcessStartInfo(
                WorkbenchController.ResolveShellExecutable(),
                $"-NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand {WorkbenchController.EncodePowerShellCommand(script)}")
            {
                WorkingDirectory = _rootDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            });
            if (process is null)
            {
                return false;
            }
            var output = process.StandardOutput.ReadToEnd();
            process.WaitForExit(3000);
            return output.Contains('1');
        }
        catch
        {
            return false;
        }
    }

    internal void StopProcessesMatching(string processMatch)
    {
        if (string.IsNullOrWhiteSpace(processMatch))
        {
            return;
        }
        try
        {
            var script = "$needle = " + WorkbenchController.QuotePowerShellLiteral(processMatch.ToLowerInvariant()) + @"
$current = $PID
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.ProcessId -ne $current -and
    $_.CommandLine.ToLowerInvariant().Contains($needle)
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}";
            using var process = Process.Start(new ProcessStartInfo(
                WorkbenchController.ResolveShellExecutable(),
                $"-NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand {WorkbenchController.EncodePowerShellCommand(script)}")
            {
                WorkingDirectory = _rootDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
            process?.WaitForExit(3000);
        }
        catch
        {
        }
    }

    private void StopProcessListeningOnPort(int port)
    {
        if (port <= 0)
        {
            return;
        }
        try
        {
            var script = "$port = " + port + @"
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty OwningProcess |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }";
            using var process = Process.Start(new ProcessStartInfo(
                WorkbenchController.ResolveShellExecutable(),
                $"-NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand {WorkbenchController.EncodePowerShellCommand(script)}")
            {
                WorkingDirectory = _rootDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
            process?.WaitForExit(3000);
        }
        catch
        {
        }
    }

    internal bool CommandGatewayHealthy()
    {
        if (!PortOpen("127.0.0.1", _commandGatewayPort))
        {
            return false;
        }
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(900) };
            // 探测 /health（无鉴权端点）：旧实现探 /desktop/workflows，配置 token 后必然 401，
            // 会把健康网关误判为离线并反复杀掉重启（G1 看门狗接入前发现的隐患）。
            using var request = new HttpRequestMessage(HttpMethod.Get, $"http://127.0.0.1:{_commandGatewayPort}/health");
            using var response = client.Send(request);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private bool MobileControlHealthy()
    {
        if (!PortOpen("127.0.0.1", _androidEndpointPort))
        {
            return false;
        }
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(1500) };
            using var request = new HttpRequestMessage(HttpMethod.Get, $"http://127.0.0.1:{_androidEndpointPort}/ios/native/snapshot");
            using var response = client.Send(request);
            // A protected deployment returns 401; a local development receiver
            // returns 200. A stale receiver without the native contract returns 404.
            return response.IsSuccessStatusCode || response.StatusCode == System.Net.HttpStatusCode.Unauthorized;
        }
        catch
        {
            return false;
        }
    }

    private bool IosStaticHealthy()
    {
        if (!PortOpen("127.0.0.1", _iosEndpointPort))
        {
            return false;
        }
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(900) };
            using var request = new HttpRequestMessage(HttpMethod.Get, $"http://127.0.0.1:{_iosEndpointPort}/frontend/ios_controller_prototype.html");
            using var response = client.Send(request);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private bool FrontendHealthy()
    {
        if (!PortOpen("127.0.0.1", _frontendPort))
        {
            return false;
        }
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromMilliseconds(900) };
            using var pageRequest = new HttpRequestMessage(HttpMethod.Get, $"{FrontendBaseUrl()}/avatar_3d.html");
            using var pageResponse = client.Send(pageRequest);
            if (!pageResponse.IsSuccessStatusCode)
            {
                return false;
            }
            using var stateRequest = new HttpRequestMessage(HttpMethod.Get, $"{FrontendBaseUrl()}/avatar-state/locomotion");
            using var stateResponse = client.Send(stateRequest);
            return stateResponse.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private static bool PortOpen(string host, int port)
    {
        try
        {
            using var client = new TcpClient();
            var task = client.ConnectAsync(host, port);
            return task.Wait(TimeSpan.FromMilliseconds(350)) && client.Connected;
        }
        catch
        {
            return false;
        }
    }

}


