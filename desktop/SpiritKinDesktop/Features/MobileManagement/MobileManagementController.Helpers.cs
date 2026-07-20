using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
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

    internal static bool TryReadJsonArray(JsonElement element, string key, out JsonElement value)
    {
        value = default;
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var candidate) || candidate.ValueKind != JsonValueKind.Array)
        {
            return false;
        }
        value = candidate;
        return true;
    }

    internal static bool TryReadJsonArray(JsonElement element, string key)
    {
        return element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(key, out var candidate)
            && candidate.ValueKind == JsonValueKind.Array;
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
        return int.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : fallback;
    }

    internal static bool ReadSafeJsonBool(JsonElement element, string key, bool fallback = false)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return fallback;
        }
        return ReadJsonBool(element, key, fallback);
    }

    internal static void EnsureOkResponse(JsonElement root, string actionLabel) => JsonResponseHelpers.EnsureOkResponse(root, actionLabel);

}
