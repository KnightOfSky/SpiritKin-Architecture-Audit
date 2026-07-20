using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
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
using Microsoft.Win32;

namespace SpiritKinDesktop;

internal static partial class DesktopRuntimeHelpers
{
    internal static string[] SplitLines(string text)
    {
        return text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    }

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

}
