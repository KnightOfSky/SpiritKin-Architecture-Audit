using Microsoft.Win32;
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
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class ComposerController
{
    internal void EmptyPromptBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        ChatWorkspace.EmptyPromptBox.Height = double.NaN;
        RenderComposerStatusVisibility();
        TryOpenMentionMenuFromText(sender as TextBox);
    }

    internal void ComposerPrompt_TextChanged(object sender, TextChangedEventArgs e)
    {
        RenderComposerStatusVisibility();
        TryOpenMentionMenuFromText(sender as TextBox);
    }

    internal void TryOpenMentionMenuFromText(TextBox? target)
    {
        if (target is null || !target.IsKeyboardFocusWithin)
        {
            _lastCollaborationMentionTriggerIndex = -1;
            return;
        }
        var text = target.Text ?? "";
        var index = Math.Clamp(target.CaretIndex, 0, text.Length);
        if (index == 0 || text[index - 1] != '@')
        {
            _lastCollaborationMentionTriggerIndex = -1;
            return;
        }
        var triggerIndex = index - 1;
        if (_lastCollaborationMentionTriggerIndex == triggerIndex)
        {
            return;
        }
        _lastCollaborationMentionTriggerIndex = triggerIndex;
        if (CollaborationChatActive)
        {
            OpenCollaborationMentionMenu(target, target);
            return;
        }
        OpenAgentMentionMenu(target, target);
    }

    internal void SelectDefaultComposerModel(bool persist)
    {
        var model = AutomaticComposerModel();
        SetSetting(ModelIdSetting, model.Id);
        SetSetting(ModelDisplaySetting, model.Display);
        SetSetting(ModelProviderSetting, model.Provider);
        SetSetting(ModelSourceSetting, model.Source);
        SetSetting(ModelNameSetting, model.ModelName);
        if (persist)
        {
            _ = SaveStateAsync();
        }
    }

    internal void SetSetting(string key, object? value)
    {
        State.Settings ??= new Dictionary<string, object?>();
        State.Settings[key] = value;
    }

    internal void RemoveSetting(string key)
    {
        State.Settings ??= new Dictionary<string, object?>();
        State.Settings.Remove(key);
    }

    internal string GetSettingString(string key, string fallback = "")
    {
        State.Settings ??= new Dictionary<string, object?>();
        if (!State.Settings.TryGetValue(key, out var value) || value is null)
        {
            return fallback;
        }
        return value switch
        {
            string text => string.IsNullOrWhiteSpace(text) ? fallback : text,
            JsonElement element => ReadJsonElementString(element, fallback),
            _ => Convert.ToString(value) is { Length: > 0 } text ? text : fallback,
        };
    }

    internal bool GetSettingBool(string key)
    {
        State.Settings ??= new Dictionary<string, object?>();
        if (!State.Settings.TryGetValue(key, out var value) || value is null)
        {
            return false;
        }
        return value switch
        {
            bool boolean => boolean,
            string text => bool.TryParse(text, out var parsed) && parsed,
            JsonElement { ValueKind: JsonValueKind.True } => true,
            JsonElement { ValueKind: JsonValueKind.False } => false,
            JsonElement element => bool.TryParse(ReadJsonElementString(element, "false"), out var parsed) && parsed,
            _ => false,
        };
    }

    internal double GetSettingDouble(string key)
    {
        State.Settings ??= new Dictionary<string, object?>();
        if (!State.Settings.TryGetValue(key, out var value) || value is null)
        {
            return 0;
        }
        return value switch
        {
            double number => number,
            float number => number,
            int number => number,
            long number => number,
            string text => double.TryParse(text, out var parsed) ? parsed : 0,
            JsonElement { ValueKind: JsonValueKind.Number } element when element.TryGetDouble(out var parsed) => parsed,
            JsonElement element => double.TryParse(ReadJsonElementString(element, "0"), out var parsed) ? parsed : 0,
            _ => double.TryParse(Convert.ToString(value), out var parsed) ? parsed : 0,
        };
    }

}

