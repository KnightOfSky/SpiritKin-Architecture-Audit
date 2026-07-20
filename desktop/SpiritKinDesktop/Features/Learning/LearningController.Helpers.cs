using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;

namespace SpiritKinDesktop;

internal sealed partial class LearningController
{
    internal static string[] SplitLines(string text) =>
        text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    internal static string ComboText(ComboBox combo)
    {
        if (combo.SelectedItem is ComboBoxItem item)
        {
            return ComboBoxItemValue(item);
        }
        if (combo.SelectedValue is string selectedValue && !string.IsNullOrWhiteSpace(selectedValue))
        {
            return selectedValue;
        }
        if (combo.SelectedItem is ModelProviderDefinitionViewModel definition)
        {
            return definition.Provider;
        }
        return combo.Text;
    }

    internal static string ProviderIdFromCombo(ComboBox combo)
    {
        if (combo.SelectedItem is ModelProviderDefinitionViewModel definition)
        {
            return definition.Provider;
        }
        if (combo.SelectedValue is string selectedValue && !string.IsNullOrWhiteSpace(selectedValue))
        {
            return selectedValue.Trim();
        }
        var text = combo.Text.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return "";
        }
        var matched = combo.Items.OfType<ModelProviderDefinitionViewModel>()
            .FirstOrDefault(item =>
                string.Equals(item.Provider, text, StringComparison.OrdinalIgnoreCase)
                || string.Equals(item.DisplayName, text, StringComparison.OrdinalIgnoreCase));
        return matched?.Provider ?? text;
    }

    internal void RefreshProviderComboDisplay()
    {
        NormalizeProviderComboSelection(WorkbenchShell.ManagementPanels.ProviderManageBox);
        NormalizeProviderComboSelection(WorkbenchShell.ManagementPanels.AssistModelProviderBox);
    }

    internal static void NormalizeProviderComboSelection(ComboBox combo)
    {
        if (combo.SelectedItem is ModelProviderDefinitionViewModel selectedDefinition)
        {
            combo.SelectedValue = selectedDefinition.Provider;
            combo.Text = ComboDisplayText(combo);
            combo.GetBindingExpression(Selector.SelectedItemProperty)?.UpdateTarget();
            combo.GetBindingExpression(Selector.SelectedValueProperty)?.UpdateTarget();
            return;
        }
        var provider = ProviderIdFromCombo(combo);
        if (string.IsNullOrWhiteSpace(provider))
        {
            return;
        }
        var match = combo.Items.OfType<ModelProviderDefinitionViewModel>()
            .FirstOrDefault(item => string.Equals(item.Provider, provider, StringComparison.OrdinalIgnoreCase));
        if (match is not null)
        {
            combo.SelectedItem = match;
            combo.Text = match.DisplayName;
        }
    }

    internal static void NormalizeAssistModelComboSelection(ComboBox combo)
    {
        if (combo.SelectedItem is AssistModelViewModel)
        {
            combo.Text = ComboDisplayText(combo);
            combo.GetBindingExpression(Selector.SelectedItemProperty)?.UpdateTarget();
            combo.GetBindingExpression(Selector.SelectedValueProperty)?.UpdateTarget();
            return;
        }
        var selected = combo.SelectedValue as string;
        if (string.IsNullOrWhiteSpace(selected))
        {
            combo.Text = "";
            return;
        }
        var match = combo.Items.OfType<AssistModelViewModel>()
            .FirstOrDefault(item => string.Equals(item.ModelId, selected, StringComparison.OrdinalIgnoreCase));
        if (match is not null)
        {
            combo.SelectedItem = match;
            combo.Text = ComboDisplayText(combo);
        }
        else
        {
            combo.Text = selected;
        }
    }

    internal static string ComboDisplayText(ComboBox combo)
    {
        if (combo.SelectedItem is ComboBoxItem item)
        {
            return Convert.ToString(item.Content) ?? "";
        }
        if (combo.SelectedItem is ModelProviderDefinitionViewModel definition)
        {
            return string.IsNullOrWhiteSpace(definition.DisplayName) ? definition.Provider : definition.DisplayName;
        }
        if (combo.SelectedItem is AssistModelViewModel model)
        {
            return string.IsNullOrWhiteSpace(model.DisplayName) ? model.ModelId : model.DisplayName;
        }
        if (combo.SelectedItem is AgentViewModel agent)
        {
            return agent.AgentId;
        }
        if (combo.SelectedItem is null && combo.SelectedValue is string selectedValue)
        {
            return selectedValue;
        }
        return Convert.ToString(combo.SelectedItem) ?? combo.Text;
    }

    internal static void SyncEditableComboSelectionText(ComboBox combo)
    {
        if (!combo.IsEditable)
        {
            return;
        }
        var display = ComboDisplayText(combo).Trim();
        if (!string.IsNullOrWhiteSpace(display))
        {
            combo.Text = display;
        }
    }

    internal static void SetComboText(ComboBox combo, string value)
    {
        if (!string.IsNullOrWhiteSpace(combo.SelectedValuePath))
        {
            combo.SelectedValue = value;
            if (combo.SelectedItem is not null)
            {
                SyncEditableComboSelectionText(combo);
                return;
            }
        }
        foreach (var definition in combo.Items.OfType<ModelProviderDefinitionViewModel>())
        {
            if (string.Equals(definition.Provider, value, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = definition;
                SyncEditableComboSelectionText(combo);
                return;
            }
        }
        foreach (var agent in combo.Items.OfType<AgentViewModel>())
        {
            if (string.Equals(agent.AgentId, value, StringComparison.OrdinalIgnoreCase)
                || string.Equals(agent.Label, value, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = agent;
                SyncEditableComboSelectionText(combo);
                return;
            }
        }
        foreach (var item in combo.Items.OfType<ComboBoxItem>())
        {
            if (string.Equals(ComboBoxItemValue(item), value, StringComparison.OrdinalIgnoreCase)
                || string.Equals(Convert.ToString(item.Content), value, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = item;
                SyncEditableComboSelectionText(combo);
                return;
            }
        }
        combo.Text = value;
    }

    internal static string ComboBoxItemValue(ComboBoxItem item)
    {
        var tag = Convert.ToString(item.Tag);
        return string.IsNullOrWhiteSpace(tag) ? Convert.ToString(item.Content) ?? "" : tag;
    }

    internal static string ReadJsonString(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return "";
        }
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? "",
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Null => "",
            _ => value.GetRawText(),
        };
    }

    internal static string ReadJsonString(JsonElement element, string key, string fallback)
    {
        var value = ReadJsonString(element, key);
        return string.IsNullOrWhiteSpace(value) ? fallback : value;
    }

    internal static int ReadJsonInt(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
        {
            return number;
        }
        return int.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : 0;
    }

    internal static bool ReadJsonBool(JsonElement element, string key, bool fallback = false)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String => bool.TryParse(value.GetString(), out var parsed) ? parsed : fallback,
            _ => fallback,
        };
    }

    internal static bool TryReadJsonObject(JsonElement element, string key, out JsonElement value)
    {
        value = default;
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var candidate) || candidate.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        value = candidate;
        return true;
    }

    internal static string ReadSafeJsonString(JsonElement element, string key, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return fallback;
        }
        return ReadJsonString(element, key, fallback);
    }

    internal static int ReadSafeJsonInt(JsonElement element, string key, int fallback = 0)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out _))
        {
            return fallback;
        }
        return ReadJsonInt(element, key);
    }

    internal static bool ReadSafeJsonBool(JsonElement element, string key, bool fallback = false)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return fallback;
        }
        return ReadJsonBool(element, key, fallback);
    }

    internal static string[] ReadSummaryStepTitles(JsonElement element, string key, int limit)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var steps) || steps.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return steps
            .EnumerateArray()
            .Take(Math.Max(0, limit))
            .Select(step =>
            {
                if (step.ValueKind == JsonValueKind.String)
                {
                    return step.GetString() ?? "";
                }
                if (step.ValueKind != JsonValueKind.Object)
                {
                    return "";
                }
                var title = ReadJsonString(step, "title");
                return string.IsNullOrWhiteSpace(title) ? ReadJsonString(step, "id") : title;
            })
            .Where(text => !string.IsNullOrWhiteSpace(text))
            .ToArray();
    }

    internal static string[] ReadJsonStringArray(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return value.EnumerateArray().Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText()).Where(item => !string.IsNullOrWhiteSpace(item)).ToArray();
    }

    internal static string? SelectExistingId(string? preferred, IEnumerable<string> candidates)
    {
        if (string.IsNullOrWhiteSpace(preferred))
        {
            return null;
        }
        return candidates.FirstOrDefault(candidate => string.Equals(candidate, preferred, StringComparison.OrdinalIgnoreCase));
    }

    internal static string UniqueId(string prefix, IEnumerable<string> existingIds)
    {
        var existing = existingIds.Where(id => !string.IsNullOrWhiteSpace(id)).ToHashSet(StringComparer.OrdinalIgnoreCase);
        for (var index = 1; index < 1000; index++)
        {
            var candidate = $"{prefix}_{index:00}";
            if (!existing.Contains(candidate))
            {
                return candidate;
            }
        }
        return $"{prefix}_{Guid.NewGuid():N}"[..Math.Min(prefix.Length + 17, prefix.Length + 33)];
    }

    internal static void EnsureOkResponse(JsonElement root, string actionLabel) => JsonResponseHelpers.EnsureOkResponse(root, actionLabel);

    internal static string? FindExistingPath(IEnumerable<string> paths)
    {
        return paths.FirstOrDefault(File.Exists);
    }

    internal static string? ResolveCommandPath(string command)
    {
        var path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (var dir in path.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            var candidate = Path.Combine(dir.Trim(), command.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ? command : $"{command}.exe");
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }
        return null;
    }
}
