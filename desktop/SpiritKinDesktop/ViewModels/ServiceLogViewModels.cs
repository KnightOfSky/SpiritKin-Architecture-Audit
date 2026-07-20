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

public sealed class ServiceViewModel
{
    public ServiceViewModel(string serviceId, string label, string status, string meta, bool running)
    {
        ServiceId = serviceId;
        Label = label;
        Status = status;
        StatusLabel = UiDisplayText.Status(Status);
        Meta = meta;
        StatusBrush = new SolidColorBrush(running ? Color.FromRgb(22, 163, 74) : Color.FromRgb(217, 119, 6));
    }

    public string ServiceId { get; }
    public string Label { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string Meta { get; }
    public Brush StatusBrush { get; }
}

public sealed class ServicePortViewModel
{
    public ServicePortViewModel(
        string serviceId,
        string label,
        int port,
        int defaultPort,
        string envVar,
        string envValue,
        string configValue,
        string source,
        bool editable,
        string type,
        string meta)
    {
        ServiceId = serviceId;
        Id = serviceId;
        Label = label;
        Port = port;
        DefaultPort = defaultPort;
        EnvVar = envVar;
        EnvValue = envValue;
        ConfigValue = configValue;
        Source = source;
        Editable = editable;
        Type = type;
        Meta = meta;
    }

    public string ServiceId { get; }
    public string Id { get; }
    public string Label { get; }
    public int Port { get; }
    public int DefaultPort { get; }
    public string EnvVar { get; }
    public string EnvValue { get; }
    public string ConfigValue { get; }
    public string Source { get; }
    public bool Editable { get; }
    public string Type { get; }
    public string Meta { get; }
    public string EditValue => string.IsNullOrWhiteSpace(ConfigValue) ? Port.ToString(CultureInfo.InvariantCulture) : ConfigValue;
}

public sealed record LogViewModel(string LogId, string Label, string Meta);
