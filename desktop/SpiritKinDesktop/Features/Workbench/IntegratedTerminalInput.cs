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
    internal void ResetTerminalSession(string reason)
    {
        _terminalReadCts?.Cancel();
        _terminalReadCts?.Dispose();
        _terminalReadCts = null;
        _terminalSession?.Dispose();
        _terminalSession = null;
        if (TerminalPanel.Visibility == Visibility.Visible)
        {
            SetTerminalText("");
            AppendTerminalLine(reason);
            AppendTerminalPrompt();
        }
    }

    internal void TerminalOutputBox_PreviewMouseDown(object sender, MouseButtonEventArgs e)
    {
        if (IsWithin<ScrollBar>(e.OriginalSource as DependencyObject))
        {
            return;
        }
        FocusTerminalInput();
        e.Handled = true;
    }

    internal async void TerminalOutputBox_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if ((Keyboard.Modifiers & ModifierKeys.Control) != 0 && e.Key == Key.A)
        {
            e.Handled = true;
            TerminalOutputBox.SelectionStart = _terminalInputStart;
            TerminalOutputBox.SelectionLength = Math.Max(0, TerminalOutputBox.Text.Length - _terminalInputStart);
            return;
        }

        if ((Keyboard.Modifiers & ModifierKeys.Control) != 0 && e.Key is Key.X or Key.V && TerminalSelectionTouchesOutput())
        {
            e.Handled = true;
            FocusTerminalInput();
            return;
        }

        if (e.Key == Key.Enter || e.Key == Key.Return)
        {
            e.Handled = true;
            await RunIntegratedTerminalCommandAsync();
            return;
        }

        if (e.Key == Key.Back && (TerminalOutputBox.CaretIndex <= _terminalInputStart || TerminalSelectionTouchesOutput()))
        {
            e.Handled = true;
            return;
        }

        if (e.Key == Key.Delete && (TerminalOutputBox.CaretIndex < _terminalInputStart || TerminalSelectionTouchesOutput()))
        {
            e.Handled = true;
            return;
        }

        if (e.Key == Key.Left && TerminalOutputBox.CaretIndex <= _terminalInputStart)
        {
            e.Handled = true;
            return;
        }

        if (e.Key == Key.Home)
        {
            e.Handled = true;
            TerminalOutputBox.CaretIndex = _terminalInputStart;
            return;
        }

        if (e.Key == Key.Up || e.Key == Key.Down || e.Key == Key.PageUp || e.Key == Key.PageDown)
        {
            e.Handled = true;
            FocusTerminalInput();
            return;
        }

        if (TerminalOutputBox.CaretIndex < _terminalInputStart && IsTextInputKey(e.Key))
        {
            FocusTerminalInput();
        }
    }

    internal void TerminalOutputBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (_terminalUpdating)
        {
            return;
        }

        if (TerminalOutputBox.Text.Length < _terminalInputStart)
        {
            SetTerminalText(TerminalPrompt());
            return;
        }

        if (TerminalOutputBox.CaretIndex < _terminalInputStart)
        {
            FocusTerminalInput();
        }
    }

    private async Task RunIntegratedTerminalCommandAsync()
    {
        var command = CurrentTerminalInput();
        if (string.IsNullOrWhiteSpace(command))
        {
            AppendTerminalText(Environment.NewLine);
            AppendTerminalPrompt();
            return;
        }
        if (string.Equals(command.Trim(), "clear", StringComparison.OrdinalIgnoreCase) ||
            string.Equals(command.Trim(), "cls", StringComparison.OrdinalIgnoreCase))
        {
            SetTerminalText("");
            AppendTerminalPrompt();
            return;
        }
        try
        {
            var terminal = await EnsureTerminalSessionAsync();
            AppendTerminalText(Environment.NewLine);
            var output = await terminal.ExecuteCommandAsync(command);
            if (!string.IsNullOrEmpty(output))
            {
                AppendTerminalText(output.EndsWith(Environment.NewLine, StringComparison.Ordinal) ? output : $"{output}{Environment.NewLine}");
            }
            await RefreshGitChangesAsync();
        }
        catch (Exception ex)
        {
            AppendTerminalLine(ex.Message);
        }
        finally
        {
            AppendTerminalPrompt();
        }
    }

}

