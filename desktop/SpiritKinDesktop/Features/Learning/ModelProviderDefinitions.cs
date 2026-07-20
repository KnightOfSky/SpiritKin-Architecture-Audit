using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class LearningController
{
    internal void EnsureProviderComboItems()
    {
        EnsureDefaultProviderDefinitions();
        if (!ReferenceEquals(WorkbenchShell.ManagementPanels.AssistModelProviderBox.ItemsSource, _providerDefinitions))
        {
            WorkbenchShell.ManagementPanels.AssistModelProviderBox.ItemsSource = _providerDefinitions;
        }
    }

    internal void EnsureDefaultProviderDefinitions()
    {
        AddDefaultProviderDefinition("llamacpp", "llama.cpp", "http://127.0.0.1:8080/v1", "qwen/qwen3.6-35b-a3b", "", false, true, true, "openai_compatible");
        AddDefaultProviderDefinition("ollama", "Ollama", "http://127.0.0.1:11434", "qwen2.5-coder:7b", "", false, true, true, "ollama");
        AddDefaultProviderDefinition("lmstudio", "LM Studio", "http://127.0.0.1:1234/v1", "local-model", "", false, true, true, "openai_compatible");
        AddDefaultProviderDefinition("openai_compatible", "OpenAI 兼容", "https://api.openai.com/v1", "gpt-4.1", "OPENAI_API_KEY", true, false, true, "openai_compatible");
        AddDefaultProviderDefinition("cloud_openai_compatible", "自定义 OpenAI 兼容", "", "", "CLOUD_MODEL_API_KEY", true, false, true, "openai_compatible");
        AddDefaultProviderDefinition("yundun", "云顿 OpenAI 兼容", "", "", "YUNDUN_API_KEY", true, false, true, "openai_compatible");
        AddDefaultProviderDefinition("anthropic", "Anthropic", "https://api.anthropic.com", "claude-3-7-sonnet-latest", "ANTHROPIC_API_KEY", true, false, true, "anthropic");
        AddDefaultProviderDefinition("gemini", "Gemini", "https://generativelanguage.googleapis.com", "gemini-2.5-pro", "GEMINI_API_KEY", true, false, true, "gemini");
        if (WorkbenchShell.ManagementPanels.ProviderManageBox.SelectedValue is null && _providerDefinitions.Count > 0)
        {
            WorkbenchShell.ManagementPanels.ProviderManageBox.SelectedValue = "llamacpp";
        }
    }

    internal void AddDefaultProviderDefinition(string provider, string displayName, string defaultEndpoint, string defaultModel, string envKey, bool requiresApiKey, bool localService, bool supportsModelSync, string protocol)
    {
        if (_providerDefinitions.Any(item => string.Equals(item.Provider, provider, StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }
        _providerDefinitions.Add(new ModelProviderDefinitionViewModel(provider, displayName, defaultEndpoint, defaultModel, envKey, requiresApiKey, localService, supportsModelSync, protocol));
    }

    internal ModelProviderDefinitionViewModel SelectedProviderDefinition()
    {
        var provider = SelectedProviderId();
        return _providerDefinitions.FirstOrDefault(item => string.Equals(item.Provider, provider, StringComparison.OrdinalIgnoreCase))
            ?? new ModelProviderDefinitionViewModel(
                provider,
                string.IsNullOrWhiteSpace(provider) ? "OpenAI 兼容" : provider,
                DefaultEndpointForProvider(provider),
                DefaultModelForProvider(provider),
                "",
                !IsLocalProvider(provider),
                IsLocalProvider(provider),
                true,
                string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase) ? "ollama" : "openai_compatible");
    }

    internal string SelectedProviderId()
    {
        NormalizeProviderComboSelection(WorkbenchShell.ManagementPanels.ProviderManageBox);
        var selected = WorkbenchShell.ManagementPanels.ProviderManageBox.SelectedValue as string;
        if (!string.IsNullOrWhiteSpace(selected))
        {
            return selected.Trim();
        }
        if (WorkbenchShell.ManagementPanels.ProviderManageBox.SelectedItem is ModelProviderDefinitionViewModel definition)
        {
            return definition.Provider;
        }
        var editorProvider = ComboText(WorkbenchShell.ManagementPanels.AssistModelProviderBox);
        return string.IsNullOrWhiteSpace(editorProvider) ? "openai_compatible" : editorProvider.Trim();
    }

    internal SelectedProviderConfig BuildSelectedProviderConfig()
    {
        var definition = SelectedProviderDefinition();
        var provider = string.IsNullOrWhiteSpace(definition.Provider) ? SelectedProviderId() : definition.Provider;
        var selectedModel = WorkbenchShell.ManagementPanels.AssistModelsList.SelectedItem as AssistModelViewModel;
        var endpoint = WorkbenchShell.ManagementPanels.CloudBaseUrlBox.Text.Trim();
        var model = WorkbenchShell.ManagementPanels.CloudModelBox.Text.Trim();
        var apiKey = WorkbenchShell.ManagementPanels.CloudApiKeyBox.Password.Trim();
        if (selectedModel is not null && string.Equals(selectedModel.Provider, provider, StringComparison.OrdinalIgnoreCase))
        {
            endpoint = string.IsNullOrWhiteSpace(endpoint) ? selectedModel.Endpoint : endpoint;
            model = string.IsNullOrWhiteSpace(model) ? selectedModel.Model : model;
        }
        endpoint = string.IsNullOrWhiteSpace(endpoint) ? definition.DefaultEndpoint : endpoint;
        model = string.IsNullOrWhiteSpace(model) ? definition.DefaultModel : model;
        return new SelectedProviderConfig(
            provider,
            string.IsNullOrWhiteSpace(definition.DisplayName) ? provider : definition.DisplayName,
            endpoint,
            model,
            apiKey,
            definition.RequiresApiKey,
            definition.LocalService,
            definition.SupportsModelSync,
            string.IsNullOrWhiteSpace(definition.Protocol) ? "openai_compatible" : definition.Protocol);
    }

    internal void SyncProviderManagerSelection()
    {
        if (_syncingProviderSelection)
        {
            return;
        }
        _syncingProviderSelection = true;
        try
        {
            var definition = SelectedProviderDefinition();
            if (!string.IsNullOrWhiteSpace(definition.Provider))
            {
                SetComboText(WorkbenchShell.ManagementPanels.AssistModelProviderBox, definition.Provider);
            }
            RefreshProviderComboDisplay();
            if (!string.IsNullOrWhiteSpace(definition.DefaultEndpoint))
            {
                WorkbenchShell.ManagementPanels.CloudBaseUrlBox.Text = definition.DefaultEndpoint;
            }
            if (!string.IsNullOrWhiteSpace(definition.DefaultModel))
            {
                WorkbenchShell.ManagementPanels.CloudModelBox.Text = definition.DefaultModel;
            }
            SyncProviderServiceButtonState();
        }
        finally
        {
            _syncingProviderSelection = false;
        }
    }

    internal void SyncAssistProviderSelection()
    {
        if (_syncingProviderSelection)
        {
            return;
        }
        var provider = ProviderIdFromCombo(WorkbenchShell.ManagementPanels.AssistModelProviderBox);
        if (string.IsNullOrWhiteSpace(provider))
        {
            return;
        }
        _syncingProviderSelection = true;
        try
        {
            WorkbenchShell.ManagementPanels.ProviderManageBox.SelectedValue = provider;
            var definition = SelectedProviderDefinition();
            RefreshProviderComboDisplay();
            if (!string.IsNullOrWhiteSpace(definition.DefaultEndpoint))
            {
                WorkbenchShell.ManagementPanels.CloudBaseUrlBox.Text = definition.DefaultEndpoint;
            }
            if (!string.IsNullOrWhiteSpace(definition.DefaultModel))
            {
                WorkbenchShell.ManagementPanels.CloudModelBox.Text = definition.DefaultModel;
            }
            SyncProviderServiceButtonState();
        }
        finally
        {
            _syncingProviderSelection = false;
        }
    }

    private static bool IsLocalProvider(string provider)
    {
        return string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "lmstudio", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "llamacpp", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "llama_cpp", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "llama.cpp", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "llama-cpp", StringComparison.OrdinalIgnoreCase);
    }

    private static string DefaultEndpointForProvider(string provider)
    {
        if (string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase))
        {
            return "http://127.0.0.1:11434";
        }
        if (string.Equals(provider, "lmstudio", StringComparison.OrdinalIgnoreCase))
        {
            return "http://127.0.0.1:1234/v1";
        }
        if (IsLlamaCppProvider(provider))
        {
            return "http://127.0.0.1:8080/v1";
        }
        return string.Equals(provider, "openai_compatible", StringComparison.OrdinalIgnoreCase)
            ? "https://api.openai.com/v1"
            : "";
    }

    private static string DefaultModelForProvider(string provider)
    {
        if (string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase))
        {
            return "qwen2.5-coder:7b";
        }
        if (string.Equals(provider, "lmstudio", StringComparison.OrdinalIgnoreCase))
        {
            return "local-model";
        }
        if (IsLlamaCppProvider(provider))
        {
            return "qwen/qwen3.6-35b-a3b";
        }
        if (string.Equals(provider, "openai_compatible", StringComparison.OrdinalIgnoreCase))
        {
            return "gpt-4.1";
        }
        return "";
    }

    private static bool IsLlamaCppProvider(string provider)
    {
        return string.Equals(provider, "llamacpp", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "llama_cpp", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "llama.cpp", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "llama-cpp", StringComparison.OrdinalIgnoreCase);
    }

}

