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

public sealed record WorkflowNodeTemplateViewModel(
    string TemplateId,
    string DisplayName,
    string BaseNodeId,
    string NodeType,
    string Label,
    string AssignedAgent,
    string ToolName,
    string SkillName,
    string ReviewGate,
    string ArgumentsJson,
    string Description)
{
    public string Meta => $"{NodeType} · {(string.IsNullOrWhiteSpace(AssignedAgent) ? Description : AssignedAgent)}";
    public override string ToString() => DisplayName;
}
public sealed record WorkflowDependencyOptionViewModel(string NodeId, string Title, string Meta)
{
    public override string ToString() => Title;
}
