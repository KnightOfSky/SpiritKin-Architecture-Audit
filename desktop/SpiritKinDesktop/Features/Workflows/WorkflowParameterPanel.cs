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
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    internal void RenderWorkflowDynamicParameters(JsonElement definition)
    {
        var previousValues = ReadWorkflowParameterValues();
        WorkbenchShell.ManagementPanels.WorkflowDynamicParametersPanel.Children.Clear();
        _workflowParameterControls.Clear();
        _workflowParameterDefaults.Clear();

        if (!TryReadJsonObject(definition, "metadata", out var metadata)
            || !metadata.TryGetProperty("parameters", out var parameters)
            || parameters.ValueKind != JsonValueKind.Array
            || parameters.GetArrayLength() == 0)
        {
            WorkbenchShell.ManagementPanels.WorkflowDynamicParametersPanel.Children.Add(new TextBlock
            {
                Text = "这个工作流没有额外启动参数。",
                Foreground = new SolidColorBrush(Color.FromRgb(141, 160, 181)),
                FontSize = 12,
                TextWrapping = TextWrapping.Wrap,
            });
            return;
        }

        foreach (var parameter in parameters.EnumerateArray())
        {
            var name = ReadJsonString(parameter, "name");
            if (string.IsNullOrWhiteSpace(name))
            {
                continue;
            }
            var label = ReadJsonString(parameter, "label", name);
            var type = ReadJsonString(parameter, "type", "text").ToLowerInvariant();
            var placeholder = ReadJsonString(parameter, "placeholder");
            var defaultValue = ReadWorkflowParameterDefault(parameter);
            _workflowParameterDefaults[name] = defaultValue;
            var value = previousValues.TryGetValue(name, out var previousValue) ? previousValue : defaultValue;

            var block = new StackPanel { Margin = new Thickness(0, 0, 0, 8) };
            if (type == "boolean")
            {
                var checkBox = new CheckBox
                {
                    Content = label,
                    IsChecked = CoerceBool(value),
                };
                block.Children.Add(checkBox);
                _workflowParameterControls[name] = checkBox;
                WorkbenchShell.ManagementPanels.WorkflowDynamicParametersPanel.Children.Add(block);
                continue;
            }

            block.Children.Add(new TextBlock
            {
                Text = label,
                Foreground = new SolidColorBrush(Color.FromRgb(159, 176, 194)),
                FontSize = 12,
                Margin = new Thickness(0, 0, 0, 4),
            });

            if (type == "select")
            {
                var combo = new ComboBox
                {
                    IsEditable = true,
                    Margin = new Thickness(0),
                    Text = Convert.ToString(value) ?? "",
                };
                if (parameter.TryGetProperty("options", out var options) && options.ValueKind == JsonValueKind.Array)
                {
                    foreach (var option in options.EnumerateArray())
                    {
                        combo.Items.Add(option.ValueKind == JsonValueKind.String ? option.GetString() ?? "" : option.GetRawText());
                    }
                }
                block.Children.Add(combo);
                _workflowParameterControls[name] = combo;
            }
            else
            {
                var textBox = new TextBox
                {
                    Text = Convert.ToString(value) ?? "",
                    ToolTip = placeholder,
                    MinHeight = type == "textarea" ? 72 : 32,
                    TextWrapping = type == "textarea" ? TextWrapping.Wrap : TextWrapping.NoWrap,
                    AcceptsReturn = type == "textarea",
                    VerticalScrollBarVisibility = type == "textarea" ? ScrollBarVisibility.Auto : ScrollBarVisibility.Disabled,
                    Margin = new Thickness(0),
                };
                block.Children.Add(textBox);
                _workflowParameterControls[name] = textBox;
            }
            WorkbenchShell.ManagementPanels.WorkflowDynamicParametersPanel.Children.Add(block);
        }
    }

    internal Dictionary<string, object?> ReadWorkflowParameterValues()
    {
        var values = new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (name, control) in _workflowParameterControls)
        {
            values[name] = control switch
            {
                CheckBox checkBox => checkBox.IsChecked == true,
                ComboBox comboBox => ComboText(comboBox),
                TextBox textBox => textBox.Text.Trim(),
                _ => null,
            };
        }
        return values;
    }

    internal static object? ReadWorkflowParameterDefault(JsonElement parameter)
    {
        if (!parameter.TryGetProperty("default", out var value))
        {
            return "";
        }
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? "",
            JsonValueKind.Number => value.TryGetInt32(out var number) ? number : value.GetRawText(),
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => value.GetRawText(),
        };
    }

    internal static bool CoerceBool(object? value)
    {
        if (value is bool flag)
        {
            return flag;
        }
        return bool.TryParse(Convert.ToString(value), out var parsed) && parsed;
    }

    internal Dictionary<string, object?> BuildWorkflowStartPayload()
    {
        var inputs = new Dictionary<string, object?>
        {
            ["project_root"] = _rootDir,
        };
        var workflowName = ActiveWorkflowName();
        foreach (var (key, value) in BuildWorkflowDynamicInputs())
        {
            inputs[key] = value;
        }
        return new Dictionary<string, object?>
        {
            ["workflow_name"] = workflowName,
            ["project_root"] = _rootDir,
            ["inputs"] = inputs,
        };
    }

    internal Dictionary<string, object?> BuildWorkflowDynamicInputs()
    {
        var values = ReadWorkflowParameterValues();
        foreach (var (key, fallback) in _workflowParameterDefaults)
        {
            if (!values.ContainsKey(key))
            {
                values[key] = fallback;
            }
        }

        if (_activeWorkflowDefinition.ValueKind == JsonValueKind.Object
            && TryReadJsonObject(_activeWorkflowDefinition, "metadata", out var metadata)
            && metadata.TryGetProperty("parameters", out var parameters)
            && parameters.ValueKind == JsonValueKind.Array)
        {
            foreach (var parameter in parameters.EnumerateArray())
            {
                var name = ReadJsonString(parameter, "name");
                if (string.IsNullOrWhiteSpace(name) || !values.TryGetValue(name, out var value))
                {
                    continue;
                }
                var type = ReadJsonString(parameter, "type", "text").ToLowerInvariant();
                if (type == "number" && value is string text)
                {
                    values[name] = int.TryParse(text, out var intValue) ? intValue : text;
                }
                else if (type == "textarea" && name.Contains("images", StringComparison.OrdinalIgnoreCase) && value is string lines)
                {
                    values[name] = SplitLines(lines);
                }
            }
        }
        return values;
    }
}
