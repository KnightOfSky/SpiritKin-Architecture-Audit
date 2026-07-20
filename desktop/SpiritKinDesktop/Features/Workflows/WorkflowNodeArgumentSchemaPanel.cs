using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Shapes;

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    internal void WorkflowNodeArgumentsBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (_syncingWorkflowNodeArgumentSchema)
        {
            return;
        }
        RenderWorkflowNodeArgumentSchema(WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text);
    }

    internal void RenderWorkflowNodeArgumentSchema(string json)
    {
        if (_syncingWorkflowNodeArgumentSchema)
        {
            return;
        }
        _syncingWorkflowNodeArgumentSchema = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowNodeSchemaPanel.Children.Clear();
            _workflowNodeArgumentControls.Clear();
            if (!TryParseJsonObject(json, out var arguments, out _))
            {
                WorkbenchShell.ManagementPanels.WorkflowNodeSchemaPanel.Children.Add(new TextBlock
                {
                    Text = "JSON 解析失败，修复后会生成参数表单。",
                    Foreground = new SolidColorBrush(Color.FromRgb(220, 38, 38)),
                    FontSize = 12,
                    TextWrapping = TextWrapping.Wrap,
                });
                return;
            }
            if (arguments.Count == 0)
            {
                WorkbenchShell.ManagementPanels.WorkflowNodeSchemaPanel.Children.Add(new TextBlock
                {
                    Text = "当前节点无参数。",
                    Foreground = new SolidColorBrush(Color.FromRgb(75, 85, 99)),
                    FontSize = 12,
                    TextWrapping = TextWrapping.Wrap,
                });
                return;
            }
            foreach (var (key, value) in arguments.Take(12))
            {
                var block = new StackPanel { Margin = new Thickness(0, 0, 0, 6) };
                block.Children.Add(new TextBlock
                {
                    Text = key,
                    Foreground = new SolidColorBrush(Color.FromRgb(75, 85, 99)),
                    FontSize = 12,
                    FontWeight = FontWeights.SemiBold,
                    Margin = new Thickness(0, 0, 0, 3),
                });
                var input = new TextBox
                {
                    Text = WorkflowArgumentValueToText(value),
                    Tag = key,
                    MinHeight = 30,
                    TextWrapping = TextWrapping.Wrap,
                };
                input.LostFocus += (_, _) => SyncWorkflowNodeArgumentsFromSchema();
                input.KeyDown += (_, keyArgs) =>
                {
                    if (keyArgs.Key == Key.Enter && (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control)
                    {
                        SyncWorkflowNodeArgumentsFromSchema();
                        keyArgs.Handled = true;
                    }
                };
                block.Children.Add(input);
                _workflowNodeArgumentControls[key] = input;
                WorkbenchShell.ManagementPanels.WorkflowNodeSchemaPanel.Children.Add(block);
            }
        }
        finally
        {
            _syncingWorkflowNodeArgumentSchema = false;
        }
    }

    internal void SyncWorkflowNodeArgumentsFromSchema()
    {
        if (_syncingWorkflowNodeArgumentSchema || !TryParseJsonObject(WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text, out var arguments, out _))
        {
            return;
        }
        foreach (var (key, input) in _workflowNodeArgumentControls)
        {
            arguments[key] = CoerceWorkflowArgumentInput(input.Text);
        }
        _syncingWorkflowNodeArgumentSchema = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text = JsonSerializer.Serialize(arguments, new JsonSerializerOptions { WriteIndented = true });
        }
        finally
        {
            _syncingWorkflowNodeArgumentSchema = false;
        }
    }

    internal void AddWorkflowNodeArgumentFromEditor()
    {
        var key = WorkbenchShell.ManagementPanels.WorkflowNewArgumentNameBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(key))
        {
            WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = "请先填写参数名。";
            return;
        }
        if (!Regex.IsMatch(key, @"^[A-Za-z_][A-Za-z0-9_.-]*$"))
        {
            WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = "参数名只能包含字母、数字、下划线、点和短横线，并且不能以数字开头。";
            return;
        }
        if (!TryParseJsonObject(WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text, out var arguments, out var error))
        {
            WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = $"当前 arguments JSON 还不能编辑：{error}";
            return;
        }
        var rawValue = WorkbenchShell.ManagementPanels.WorkflowNewArgumentValueBox.Text.Trim();
        arguments[key] = string.IsNullOrWhiteSpace(rawValue)
            ? $"{{{{{key}}}}}"
            : CoerceWorkflowArgumentInput(rawValue);
        _syncingWorkflowNodeArgumentSchema = true;
        try
        {
            WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text = JsonSerializer.Serialize(arguments, new JsonSerializerOptions { WriteIndented = true });
        }
        finally
        {
            _syncingWorkflowNodeArgumentSchema = false;
        }
        WorkbenchShell.ManagementPanels.WorkflowNewArgumentNameBox.Clear();
        WorkbenchShell.ManagementPanels.WorkflowNewArgumentValueBox.Clear();
        RenderWorkflowNodeArgumentSchema(WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text);
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = $"已添加参数 {key}；如果值是 {("{{" + key + "}}")}，保存定义后会自动成为启动参数。";
    }

    internal static string WorkflowArgumentValueToText(object? value)
    {
        return value switch
        {
            null => "",
            string text => text,
            bool flag => flag ? "true" : "false",
            _ => JsonSerializer.Serialize(value),
        };
    }

    internal static object? CoerceWorkflowArgumentInput(string text)
    {
        var trimmed = text.Trim();
        if (string.Equals(trimmed, "true", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        if (string.Equals(trimmed, "false", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        if (int.TryParse(trimmed, out var integer))
        {
            return integer;
        }
        if (double.TryParse(trimmed, out var number))
        {
            return number;
        }
        if ((trimmed.StartsWith("{", StringComparison.Ordinal) && trimmed.EndsWith("}", StringComparison.Ordinal))
            || (trimmed.StartsWith("[", StringComparison.Ordinal) && trimmed.EndsWith("]", StringComparison.Ordinal)))
        {
            try
            {
                using var doc = JsonDocument.Parse(trimmed);
                return JsonElementToObject(doc.RootElement);
            }
            catch (JsonException)
            {
                return text;
            }
        }
        return text;
    }

}
