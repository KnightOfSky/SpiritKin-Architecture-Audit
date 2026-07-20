using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace SpiritKinDesktop;

internal sealed class VoiceCallSessionController : IDisposable
{
    private readonly Window _owner;
    private readonly string _rootDir;
    private readonly RuntimeController _runtime;
    private readonly Action _returnToText;
    private readonly VoiceCallUiState _state = new();
    private readonly HashSet<Process> _expectedStops = new();
    private readonly CancellationTokenSource _lifetime = new();
    private VoiceCallWindow? _window;
    private Process? _process;
    private int? _deviceIndex;
    private bool _micMuted;
    private bool _speakerEnabled = true;
    private bool _disposed;
    private int _reconnectAttempts;
    private string _lastProcessError = "";

    internal VoiceCallSessionController(Window owner, string rootDir, RuntimeController runtime, Action returnToText)
    {
        _owner = owner;
        _rootDir = rootDir;
        _runtime = runtime;
        _returnToText = returnToText;
        _runtime.EventApplied += Runtime_EventApplied;
    }

    internal async Task OpenAsync()
    {
        if (_disposed)
        {
            return;
        }
        if (_window is not null)
        {
            _window.Activate();
            return;
        }

        var window = new VoiceCallWindow { Owner = _owner };
        _window = window;
        window.MicToggleRequested += (_, _) => ToggleMicrophone();
        window.SpeakerToggleRequested += (_, _) => ToggleSpeaker();
        window.EndRequested += (_, _) => EndAndClose();
        window.RetryRequested += (_, _) => _ = RestartCaptureAsync(userInitiated: true);
        window.TextFallbackRequested += (_, _) => ReturnToTextAndClose();
        window.DeviceChanged += device => SelectDevice(device.Index);
        window.Closed += (_, _) =>
        {
            if (ReferenceEquals(_window, window))
            {
                _window = null;
                StopCapture();
            }
        };
        window.SetMicMuted(_micMuted);
        window.SetSpeakerEnabled(_speakerEnabled);
        window.Render(_state);
        window.Show();

        var devicesTask = LoadDevicesAsync(_lifetime.Token);
        if (!_micMuted)
        {
            StartCapture();
        }
        var devices = await devicesTask;
        if (_window is not null)
        {
            _window.SetDevices(devices, _deviceIndex);
        }
    }

    internal static IReadOnlyList<string> BuildArguments(string callId, int? deviceIndex, bool speakerEnabled)
    {
        var arguments = new List<string>
        {
            "-u",
            "-m",
            "backend.perception.audio.realtime_session",
            "--call-mode",
            "--call-id",
            callId,
            "--no-hotword",
            "--max-turns",
            "0",
            "--idle-timeouts",
            "0",
        };
        if (deviceIndex is not null)
        {
            arguments.Add("--device-index");
            arguments.Add(deviceIndex.Value.ToString(CultureInfo.InvariantCulture));
        }
        if (!speakerEnabled)
        {
            arguments.Add("--no-speak");
        }
        return arguments;
    }

    private void StartCapture()
    {
        if (_disposed || _micMuted || _process is { HasExited: false })
        {
            return;
        }
        var callId = $"call_{Guid.NewGuid():N}";
        _state.BeginCall(callId);
        _window?.ResetElapsed();
        Render();

        var startInfo = new ProcessStartInfo
        {
            FileName = "python",
            WorkingDirectory = _rootDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var argument in BuildArguments(callId, _deviceIndex, _speakerEnabled))
        {
            startInfo.ArgumentList.Add(argument);
        }
        startInfo.Environment["PYTHONIOENCODING"] = "utf-8";
        try
        {
            var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            process.OutputDataReceived += (_, _) => { };
            process.ErrorDataReceived += (_, args) =>
            {
                if (!string.IsNullOrWhiteSpace(args.Data))
                {
                    _lastProcessError = args.Data.Trim();
                }
            };
            process.Exited += (_, _) => Process_Exited(process);
            if (!process.Start())
            {
                throw new InvalidOperationException("语音进程未能启动。");
            }
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            _process = process;
        }
        catch (Exception ex)
        {
            _state.ApplyLocalPhase(VoiceCallPhase.Error, $"无法启动语音服务：{ex.Message}");
            Render();
        }
    }

    private void Process_Exited(Process process)
    {
        _ = _owner.Dispatcher.InvokeAsync(async () =>
        {
            var expected = _expectedStops.Remove(process);
            if (ReferenceEquals(_process, process))
            {
                _process = null;
            }
            process.Dispose();
            if (_disposed || expected || _micMuted || _window is null)
            {
                return;
            }
            if (_reconnectAttempts >= 3)
            {
                var detail = string.IsNullOrWhiteSpace(_lastProcessError) ? "语音服务连续退出。" : _lastProcessError;
                _state.ApplyLocalPhase(VoiceCallPhase.Error, detail);
                Render();
                return;
            }
            _reconnectAttempts++;
            _state.ApplyLocalPhase(VoiceCallPhase.Reconnecting, $"正在重连（{_reconnectAttempts}/3）");
            Render();
            try
            {
                await Task.Delay(TimeSpan.FromMilliseconds(350 * _reconnectAttempts), _lifetime.Token).ConfigureAwait(true);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            StartCapture();
        });
    }

    private async Task RestartCaptureAsync(bool userInitiated)
    {
        if (userInitiated)
        {
            _reconnectAttempts = 0;
        }
        StopCapture();
        if (_micMuted || _disposed)
        {
            return;
        }
        _state.ApplyLocalPhase(VoiceCallPhase.Reconnecting, "正在重新连接");
        Render();
        try
        {
            await Task.Delay(150, _lifetime.Token);
        }
        catch (OperationCanceledException)
        {
            return;
        }
        StartCapture();
    }

    private void ToggleMicrophone()
    {
        _micMuted = !_micMuted;
        _window?.SetMicMuted(_micMuted);
        if (_micMuted)
        {
            StopCapture();
            _state.ApplyLocalPhase(VoiceCallPhase.Idle, "麦克风已静音");
            Render();
        }
        else
        {
            _reconnectAttempts = 0;
            StartCapture();
        }
    }

    private void ToggleSpeaker()
    {
        _speakerEnabled = !_speakerEnabled;
        _window?.SetSpeakerEnabled(_speakerEnabled);
        if (!_micMuted)
        {
            _ = RestartCaptureAsync(userInitiated: true);
        }
    }

    private void SelectDevice(int index)
    {
        if (_deviceIndex == index)
        {
            return;
        }
        _deviceIndex = index;
        if (!_micMuted)
        {
            _ = RestartCaptureAsync(userInitiated: true);
        }
    }

    private void Runtime_EventApplied(RuntimeEvent ev)
    {
        if (!_state.Apply(ev))
        {
            return;
        }
        if (_state.Phase == VoiceCallPhase.Listening)
        {
            _reconnectAttempts = 0;
        }
        _ = _owner.Dispatcher.InvokeAsync(Render);
    }

    private void Render()
    {
        _window?.Render(_state);
    }

    private async Task<IReadOnlyList<VoiceInputDevice>> LoadDevicesAsync(CancellationToken cancellationToken)
    {
        const string script = "import json; from backend.perception.audio.listener import list_microphone_devices; print(json.dumps(list_microphone_devices(), ensure_ascii=False))";
        var startInfo = new ProcessStartInfo
        {
            FileName = "python",
            WorkingDirectory = _rootDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
        };
        startInfo.ArgumentList.Add("-u");
        startInfo.ArgumentList.Add("-c");
        startInfo.ArgumentList.Add(script);
        try
        {
            using var process = Process.Start(startInfo);
            if (process is null)
            {
                return Array.Empty<VoiceInputDevice>();
            }
            var output = await process.StandardOutput.ReadToEndAsync(cancellationToken);
            await process.WaitForExitAsync(cancellationToken);
            using var document = JsonDocument.Parse(output.Trim());
            return document.RootElement.EnumerateArray()
                .Where(item => !item.TryGetProperty("blocked", out var blocked) || blocked.ValueKind != JsonValueKind.True)
                .Select(item => new VoiceInputDevice(
                    item.TryGetProperty("index", out var index) ? index.GetInt32() : -1,
                    item.TryGetProperty("name", out var name) ? name.GetString() ?? "麦克风" : "麦克风"))
                .Where(device => device.Index >= 0)
                .ToList();
        }
        catch
        {
            return Array.Empty<VoiceInputDevice>();
        }
    }

    private void EndAndClose()
    {
        StopCapture();
        _state.ApplyLocalPhase(VoiceCallPhase.Ended);
        Render();
        _window?.Close();
    }

    private void ReturnToTextAndClose()
    {
        StopCapture();
        _returnToText();
        _window?.Close();
    }

    private void StopCapture()
    {
        var process = _process;
        _process = null;
        if (process is null)
        {
            return;
        }
        _expectedStops.Add(process);
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            _expectedStops.Remove(process);
            process.Dispose();
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _runtime.EventApplied -= Runtime_EventApplied;
        _lifetime.Cancel();
        StopCapture();
        _window?.Close();
        _window = null;
        _lifetime.Dispose();
    }
}
