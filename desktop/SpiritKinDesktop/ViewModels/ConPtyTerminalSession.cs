using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Effects;

namespace SpiritKinDesktop;

public sealed class ConPtyTerminalSession : IDisposable
{
    private readonly Channel<string> _output = Channel.CreateUnbounded<string>();
    private readonly string _executable;
    private readonly string _workingDirectory;
    private readonly Dictionary<string, string> _environment;
    private string _currentDirectory;
    private bool _disposed;

    private ConPtyTerminalSession(string executable, string workingDirectory, IReadOnlyDictionary<string, string>? environment = null)
    {
        _executable = executable;
        _workingDirectory = workingDirectory;
        _environment = environment is null ? new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase) : new Dictionary<string, string>(environment, StringComparer.OrdinalIgnoreCase);
        _currentDirectory = workingDirectory;
    }

    public bool IsRunning => !_disposed;
    public string CurrentDirectory => _currentDirectory;

    public static Task<ConPtyTerminalSession> StartAsync(string executable, string arguments, string workingDirectory, IReadOnlyDictionary<string, string>? environment = null) =>
        Task.FromResult(new ConPtyTerminalSession(executable, workingDirectory, environment));

    public async Task WriteLineAsync(string command)
    {
        if (_disposed)
        {
            return;
        }
        var output = await ExecuteCommandAsync(command);
        if (!string.IsNullOrEmpty(output))
        {
            EnqueueOutput(output.TrimEnd());
        }
    }

    public Task<string> ExecuteCommandAsync(string command)
    {
        if (_disposed)
        {
            return Task.FromResult("");
        }
        return Task.Run(() => RunPowerShellCommand(command));
    }

    public async IAsyncEnumerable<string> ReadOutputAsync([System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken)
    {
        while (await _output.Reader.WaitToReadAsync(cancellationToken))
        {
            while (_output.Reader.TryRead(out var line))
            {
                yield return line;
            }
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _output.Writer.TryComplete();
    }

    private string RunPowerShellCommand(string command)
    {
        var marker = $"__SPIRITKIN_PWD__{Guid.NewGuid():N}__";
        var wrapper = string.Join(Environment.NewLine, new[]
        {
            "$ErrorActionPreference = 'Continue'",
            "try {",
            "    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "    $OutputEncoding = [System.Text.Encoding]::UTF8",
            $"    Set-Location -LiteralPath '{_currentDirectory.Replace("'", "''")}'",
            command,
            "} catch {",
            "    Write-Error $_",
            "}",
            $"Write-Output '{marker}'",
            "Write-Output (Get-Location).Path",
        });
        var encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(wrapper));
        var startInfo = new ProcessStartInfo(_executable, $"-NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}")
        {
            WorkingDirectory = _currentDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var item in _environment)
        {
            startInfo.Environment[item.Key] = item.Value ?? "";
        }
        try
        {
            using var process = Process.Start(startInfo) ?? throw new InvalidOperationException("PowerShell process did not start");
            var output = process.StandardOutput.ReadToEnd();
            var error = process.StandardError.ReadToEnd();
            process.WaitForExit(120000);
            var lines = output.Replace("\r\n", "\n").Split('\n').ToList();
            var markerIndex = lines.FindIndex(line => string.Equals(line.Trim(), marker, StringComparison.Ordinal));
            if (markerIndex >= 0)
            {
                var nextDir = lines.Skip(markerIndex + 1).FirstOrDefault(line => !string.IsNullOrWhiteSpace(line));
                if (!string.IsNullOrWhiteSpace(nextDir) && Directory.Exists(nextDir.Trim()))
                {
                    _currentDirectory = nextDir.Trim();
                }
                lines = lines.Take(markerIndex).ToList();
            }
            var cleanOutput = string.Join(Environment.NewLine, lines).TrimEnd();
            var builder = new StringBuilder();
            if (!string.IsNullOrWhiteSpace(cleanOutput))
            {
                builder.AppendLine(cleanOutput);
            }
            if (!string.IsNullOrWhiteSpace(error))
            {
                builder.AppendLine(error.TrimEnd());
            }
            if (process.ExitCode != 0)
            {
                builder.AppendLine($"[exit {process.ExitCode}]");
            }
            return builder.ToString();
        }
        catch (Exception ex)
        {
            return $"PowerShell 执行失败：{ex.Message}{Environment.NewLine}";
        }
    }

    private void EnqueueOutput(string? text)
    {
        if (text is null || _disposed)
        {
            return;
        }
        _output.Writer.TryWrite($"{text}{Environment.NewLine}");
    }
}
