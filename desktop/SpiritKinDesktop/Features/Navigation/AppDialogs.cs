using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class NavigationController
{
    internal string? PromptText(string title, string label, string defaultValue)
    {
        var dialog = new Window
        {
            Title = title,
            Owner = _owner(),
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            ResizeMode = ResizeMode.NoResize,
            Width = 360,
            SizeToContent = SizeToContent.Height,
            Background = new SolidColorBrush(Color.FromRgb(17, 24, 33)),
            Foreground = new SolidColorBrush(Color.FromRgb(229, 237, 246)),
        };
        var input = new TextBox
        {
            Text = defaultValue,
            MinWidth = 300,
            Margin = new Thickness(0, 6, 0, 12),
        };
        EnsureTextEditContextMenu(input);
        input.SelectAll();
        var ok = false;
        var okButton = new Button { Content = "确定", Width = 72, Margin = new Thickness(8, 0, 0, 0) };
        var cancelButton = new Button { Content = "取消", Width = 72 };
        okButton.Click += (_, _) => { ok = true; dialog.Close(); };
        cancelButton.Click += (_, _) => dialog.Close();
        input.KeyDown += (_, e) =>
        {
            if (e.Key == Key.Enter)
            {
                ok = true;
                dialog.Close();
            }
        };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        buttons.Children.Add(cancelButton);
        buttons.Children.Add(okButton);
        var panel = new StackPanel { Margin = new Thickness(16) };
        panel.Children.Add(new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(input);
        panel.Children.Add(buttons);
        dialog.Content = panel;
        dialog.ShowDialog();
        return ok ? input.Text : null;
    }

    internal bool ConfirmDestructiveAction(string title, string message)
    {
        return ShowAppDialog(title, message, destructive: true, confirmText: "删除", cancelText: "取消");
    }

    internal bool ConfirmAction(string title, string message, string confirmText = "确定")
    {
        return ShowAppDialog(title, message, destructive: false, confirmText, "取消");
    }

    internal bool ShowAppDialog(string title, string message, bool destructive, string confirmText, string cancelText)
    {
        var dialog = new Window
        {
            Title = title,
            Owner = _owner(),
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            ResizeMode = ResizeMode.NoResize,
            Width = 480,
            SizeToContent = SizeToContent.Height,
            Background = new SolidColorBrush(Color.FromRgb(255, 255, 255)),
            Foreground = new SolidColorBrush(Color.FromRgb(17, 24, 39)),
            WindowStyle = WindowStyle.ToolWindow,
            ShowInTaskbar = false,
        };
        var ok = false;
        var titleText = new TextBlock
        {
            Text = title,
            FontSize = 16,
            FontWeight = FontWeights.SemiBold,
            Foreground = destructive ? new SolidColorBrush(Color.FromRgb(185, 28, 28)) : new SolidColorBrush(Color.FromRgb(2, 80, 204)),
            TextWrapping = TextWrapping.Wrap,
        };
        var messageText = new TextBlock
        {
            Text = message,
            Foreground = new SolidColorBrush(Color.FromRgb(75, 85, 99)),
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 8, 0, 0),
        };
        var confirmButton = new Button
        {
            Content = string.IsNullOrWhiteSpace(confirmText) ? "确定" : confirmText,
            MinWidth = 82,
            Margin = new Thickness(8, 0, 0, 0),
            Style = destructive && TryFindResource("DangerButton") is Style dangerStyle
                ? dangerStyle
                : TryFindResource("PrimaryButton") as Style,
        };
        confirmButton.Click += (_, _) =>
        {
            ok = true;
            dialog.Close();
        };
        var buttons = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 16, 0, 0),
        };
        if (!string.IsNullOrWhiteSpace(cancelText))
        {
            var cancelButton = new Button
            {
                Content = cancelText,
                MinWidth = 82,
            };
            cancelButton.Click += (_, _) => dialog.Close();
            buttons.Children.Add(cancelButton);
        }
        buttons.Children.Add(confirmButton);
        var panel = new StackPanel { Margin = new Thickness(16) };
        panel.Children.Add(titleText);
        panel.Children.Add(messageText);
        panel.Children.Add(buttons);
        dialog.Content = panel;
        dialog.PreviewKeyDown += (_, e) =>
        {
            if (e.Key == Key.Escape)
            {
                dialog.Close();
                e.Handled = true;
            }
            else if (e.Key == Key.Enter)
            {
                ok = true;
                dialog.Close();
                e.Handled = true;
            }
        };
        dialog.ShowDialog();
        return ok;
    }

}
