using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
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

namespace SpiritKinDesktop;

public sealed class ModelProviderDefinitionViewModel
{
    public ModelProviderDefinitionViewModel(string provider, string displayName, string defaultEndpoint, string defaultModel, string envKey, bool requiresApiKey, bool localService, bool supportsModelSync, string protocol)
    {
        Provider = string.IsNullOrWhiteSpace(provider) ? "openai_compatible" : provider;
        DisplayName = string.IsNullOrWhiteSpace(displayName) ? Provider : displayName;
        DefaultEndpoint = defaultEndpoint;
        DefaultModel = defaultModel;
        EnvKey = envKey;
        RequiresApiKey = requiresApiKey;
        LocalService = localService;
        SupportsModelSync = supportsModelSync;
        Protocol = string.IsNullOrWhiteSpace(protocol) ? "openai_compatible" : protocol;
    }

    public string Provider { get; }
    public string DisplayName { get; }
    public string DefaultEndpoint { get; }
    public string DefaultModel { get; }
    public string EnvKey { get; }
    public bool RequiresApiKey { get; }
    public bool LocalService { get; }
    public bool SupportsModelSync { get; }
    public string Protocol { get; }

    public override string ToString() => DisplayName;
}

public sealed class AssistModelViewModel
{
    public AssistModelViewModel(string modelId, string displayName, string provider, string endpoint, string model, bool enabled, bool apiKeySet, string role, int priority, string notes, bool configured, string requestParamsJson = "")
    {
        ModelId = modelId;
        DisplayName = string.IsNullOrWhiteSpace(displayName) ? modelId : displayName;
        Provider = string.IsNullOrWhiteSpace(provider) ? "openai_compatible" : provider;
        Endpoint = endpoint;
        Model = model;
        Enabled = enabled;
        ApiKeySet = apiKeySet;
        Role = string.IsNullOrWhiteSpace(role) ? "reviewer" : role;
        Priority = priority;
        Notes = notes;
        Configured = configured;
        RequestParamsJson = requestParamsJson;
        Type = $"{(enabled ? "启用" : "关闭")} · {DisplayName}";
        Meta = $"{UiDisplayText.Provider(Provider)} · {UiDisplayText.ShortTechnical(Model)} · 优先级 {Priority} · {(configured ? "已就绪" : "未配置")}";
    }

    public string ModelId { get; }
    public string DisplayName { get; }
    public string Provider { get; }
    public string Endpoint { get; }
    public string Model { get; }
    public bool Enabled { get; }
    public bool ApiKeySet { get; }
    public string Role { get; }
    public int Priority { get; }
    public string Notes { get; }
    public bool Configured { get; }
    public string RequestParamsJson { get; }
    public string Type { get; }
    public string Meta { get; }
}
