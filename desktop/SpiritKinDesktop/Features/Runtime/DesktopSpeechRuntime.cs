using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal bool DesktopTtsEnabled()
    {
        if (!_composerControllerValue.DesktopTtsSettingIsEmpty)
        {
            return _composerControllerValue.DesktopTtsEnabled();
        }
        var env = (Environment.GetEnvironmentVariable("SPIRITKIN_DESKTOP_TTS")
            ?? Environment.GetEnvironmentVariable("SPIRITKIN_DESKTOP_TTS_ENABLED")
            ?? "").Trim();
        if (!string.IsNullOrWhiteSpace(env))
        {
            return !(env == "0"
                || env.Equals("false", StringComparison.OrdinalIgnoreCase)
                || env.Equals("off", StringComparison.OrdinalIgnoreCase)
                || env.Equals("no", StringComparison.OrdinalIgnoreCase)
                || env.Equals("disabled", StringComparison.OrdinalIgnoreCase));
        }
        return true;
    }

    internal void SyncDesktopTtsMenu()
    {
        var enabled = DesktopTtsEnabled();
        TitleBar.AppMenu.SetDesktopTtsState(enabled);
    }

    internal void SpeakDesktopReply(string text)
    {
        text = SummarizeDesktopSpeechText(text);
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        lock (_desktopTtsLock)
        {
            if (string.Equals(_lastDesktopTtsText, text, StringComparison.Ordinal) && DateTime.UtcNow - _lastDesktopTtsAt < TimeSpan.FromSeconds(2))
            {
                return;
            }
            _lastDesktopTtsText = text;
            _lastDesktopTtsAt = DateTime.UtcNow;
        }

        _ = Task.Run(() =>
        {
            try
            {
                var script = "from backend.expression.edge_tts import EdgeTTSProvider; "
                    + "import sys; "
                    + "text=sys.argv[1]; "
                    + "provider=EdgeTTSProvider(); "
                    + "ok=provider.speak_and_play(text); "
                    + "sys.exit(0 if ok else 2)";
                var startInfo = _workspaceControllerValue.BuildServiceStartInfo("python", $"-u -c {WorkbenchController.QuoteArg(script)} {WorkbenchController.QuoteArg(text)}");
                var process = Process.Start(startInfo);
                if (process is null)
                {
                    return;
                }
                var focusOwner = $"desktop_tts:{process.Id}";
                process.EnableRaisingEvents = true;
                process.Exited += (_, _) => DesktopSpeechActivityChanged?.Invoke(focusOwner, false);
                DesktopSpeechActivityChanged?.Invoke(focusOwner, true);
                lock (_desktopTtsLock)
                {
                    try
                    {
                        if (_desktopTtsProcess is { HasExited: false } previous)
                        {
                            previous.Kill(entireProcessTree: true);
                        }
                    }
                    catch
                    {
                    }
                    _desktopTtsProcess = process;
                }
                WorkspaceController.CaptureServiceOutput(process, "desktop_tts", Path.Combine(_rootDir, "state", "logs"));
            }
            catch (Exception ex)
            {
                _workspaceControllerValue.SetStatus($"TTS 播放失败：{ex.Message}");
            }
        });
    }

    internal void StopDesktopTtsPlayback()
    {
        lock (_desktopTtsLock)
        {
            try
            {
                if (_desktopTtsProcess is { HasExited: false } process)
                {
                    process.Kill(entireProcessTree: true);
                }
            }
            catch
            {
            }
            _desktopTtsProcess = null;
        }
    }

}

