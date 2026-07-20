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
    internal async Task RunCommandInIntegratedTerminalAsync(string command)
    {
        command = command.Trim();
        if (string.IsNullOrWhiteSpace(command))
        {
            return;
        }

        await ShowTerminalAsync();
        ReplaceTerminalInput(command);
        await RunIntegratedTerminalCommandAsync();
    }

    private void ReplaceTerminalInput(string command)
    {
        _terminalUpdating = true;
        try
        {
            var prefix = _terminalInputStart > 0 && _terminalInputStart <= TerminalOutputBox.Text.Length
                ? TerminalOutputBox.Text[.._terminalInputStart]
                : TerminalOutputBox.Text;
            TerminalOutputBox.Text = prefix + command;
            TerminalOutputBox.CaretIndex = TerminalOutputBox.Text.Length;
        }
        finally
        {
            _terminalUpdating = false;
        }
        FocusTerminalInput();
    }

    private async Task<ConPtyTerminalSession> EnsureTerminalSessionAsync()
    {
        if (_terminalSession is { IsRunning: true } terminal)
        {
            return terminal;
        }

        _terminalReadCts?.Cancel();
        _terminalReadCts?.Dispose();
        _terminalSession?.Dispose();
        _terminalReadCts = new CancellationTokenSource();
        SetTerminalText("");
        var runtime = ActiveProjectRuntimeProfile();
        var workspace = runtime.WorkspacePath;
        var initScript = string.Join(Environment.NewLine, new[]
        {
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "$OutputEncoding = [System.Text.Encoding]::UTF8",
            $"Set-Location -LiteralPath {QuotePowerShellLiteral(workspace)}",
            $"$env:SPIRITKIN_PROJECT_ID = {QuotePowerShellLiteral(runtime.ProjectId)}",
            $"$env:SPIRITKIN_PROJECT_WORKSPACE = {QuotePowerShellLiteral(runtime.WorkspacePath)}",
            $"$env:SPIRITKIN_PROJECT_PACKAGE_MANAGER = {QuotePowerShellLiteral(runtime.PackageManager)}",
        });
        _terminalSession = await ConPtyTerminalSession.StartAsync(
            ResolveShellExecutable(),
            $"-NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -EncodedCommand {EncodePowerShellCommand(initScript)}",
            workspace,
            BuildProjectRuntimeEnvironment(runtime));
        _ = ReadTerminalOutputAsync(_terminalSession, _terminalReadCts.Token);
        AppendTerminalLine($"Project runtime: {runtime.ProjectTitle} · {runtime.PackageManager} · {runtime.WorkspacePath}");
        if (!string.IsNullOrWhiteSpace(runtime.EnvFilePath))
        {
            AppendTerminalLine(File.Exists(runtime.EnvFilePath) ? $"Loaded env file: {runtime.EnvFilePath}" : $"Env file not found: {runtime.EnvFilePath}");
        }
        AppendTerminalPrompt();
        return _terminalSession;
    }

    private async Task ReadTerminalOutputAsync(ConPtyTerminalSession terminal, CancellationToken cancellationToken)
    {
        try
        {
            await foreach (var text in terminal.ReadOutputAsync(cancellationToken))
            {
                await Dispatcher.InvokeAsync(() => AppendTerminalText(text));
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (ObjectDisposedException)
        {
        }
        catch (Exception ex)
        {
            await Dispatcher.InvokeAsync(() => AppendTerminalLine($"Terminal closed: {ex.Message}"));
        }
    }

    private void AppendTerminalLine(string text) => AppendTerminalText($"{text}{Environment.NewLine}");

    private void AppendTerminalText(string text)
    {
        _terminalUpdating = true;
        try
        {
            TerminalOutputBox.CaretIndex = TerminalOutputBox.Text.Length;
            TerminalOutputBox.AppendText(text);
            _terminalInputStart = TerminalOutputBox.Text.Length;
        }
        finally
        {
            _terminalUpdating = false;
        }
        FocusTerminalInput();
    }

    private void AppendTerminalPrompt()
    {
        var text = TerminalOutputBox.Text;
        if (text.Length > 0 && !text.EndsWith(Environment.NewLine, StringComparison.Ordinal))
        {
            AppendTerminalText(Environment.NewLine);
        }
        AppendTerminalText(TerminalPrompt());
    }

    private void SetTerminalText(string text)
    {
        _terminalUpdating = true;
        try
        {
            TerminalOutputBox.Text = text;
            TerminalOutputBox.CaretIndex = TerminalOutputBox.Text.Length;
            _terminalInputStart = TerminalOutputBox.Text.Length;
        }
        finally
        {
            _terminalUpdating = false;
        }
        FocusTerminalInput();
    }

    private void FocusTerminalInput()
    {
        if (!TerminalOutputBox.IsKeyboardFocusWithin)
        {
            TerminalOutputBox.Focus();
        }
        TerminalOutputBox.SelectionLength = 0;
        TerminalOutputBox.CaretIndex = Math.Max(_terminalInputStart, TerminalOutputBox.Text.Length);
        TerminalOutputBox.ScrollToEnd();
    }

    private string CurrentTerminalInput()
    {
        if (_terminalInputStart < 0 || _terminalInputStart > TerminalOutputBox.Text.Length)
        {
            return "";
        }
        return TerminalOutputBox.Text[_terminalInputStart..].TrimEnd('\r', '\n');
    }

    private bool TerminalSelectionTouchesOutput()
    {
        if (TerminalOutputBox.SelectionLength <= 0)
        {
            return false;
        }
        return TerminalOutputBox.SelectionStart < _terminalInputStart;
    }

    private string TerminalPrompt() => $"PS {(_terminalSession?.CurrentDirectory ?? ActiveWorkspaceRoot())}> ";

}

