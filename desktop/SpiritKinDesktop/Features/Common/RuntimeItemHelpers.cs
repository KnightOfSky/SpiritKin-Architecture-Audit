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
    internal static void UpsertRuntimeItem(List<DesktopItem> items, JsonElement payload, string prefix, string fallbackTitle)
    {
        var dict = JsonElementToDictionary(payload);
        var id = ReadDict(dict, $"{prefix}_id");
        if (id == "--")
        {
            id = ReadDict(dict, "id");
        }
        if (id == "--")
        {
            id = NewId(prefix);
        }
        var title = ReadDict(dict, "title");
        if (title == "--")
        {
            title = ReadDict(dict, prefix == "task" ? "request" : "project_type");
        }
        if (title == "--")
        {
            title = fallbackTitle;
        }
        var existing = items.FirstOrDefault(item => item.Id == id);
        if (existing is null)
        {
            items.Add(new DesktopItem { Id = id, Title = title, Status = ReadDict(dict, "status"), CreatedAt = NowSeconds(), UpdatedAt = NowSeconds() });
        }
        else
        {
            existing.Title = title;
            existing.Status = ReadDict(dict, "status");
            existing.UpdatedAt = NowSeconds();
        }
    }
}
