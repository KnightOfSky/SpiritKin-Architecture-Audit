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
    internal async Task AddComposerFilesAsync()
    {
        var dialog = new OpenFileDialog
        {
            Title = "Add photos & files",
            Multiselect = true,
            CheckFileExists = true,
            Filter = "All supported files|*.png;*.jpg;*.jpeg;*.gif;*.webp;*.pdf;*.txt;*.md;*.markdown;*.rst;*.log;*.py;*.json;*.jsonl;*.yaml;*.yml;*.csv|All files|*.*",
        };
        if (dialog.ShowDialog(Application.Current.MainWindow) != true || dialog.FileNames.Length == 0)
        {
            return;
        }

        try
        {
            var files = new List<Dictionary<string, object?>>();
            foreach (var path in dialog.FileNames)
            {
                var info = new FileInfo(path);
                if (!info.Exists)
                {
                    continue;
                }
                const long maxBytes = 25L * 1024 * 1024;
                if (info.Length > maxBytes)
                {
                    WorkspaceSidebar.ConnectionStatusText.Text = $"已跳过过大文件：{info.Name}";
                    continue;
                }
                files.Add(new Dictionary<string, object?>
                {
                    ["path"] = info.Name,
                    ["content_base64"] = Convert.ToBase64String(File.ReadAllBytes(info.FullName)),
                    ["mime_type"] = GuessMimeType(info.FullName),
                    ["local_path"] = info.FullName,
                });
            }
            if (files.Count == 0)
            {
                return;
            }

            var payload = new { files, purpose = "desktop_composer" };
            using var request = new HttpRequestMessage(HttpMethod.Post, $"{ApiBase()}/attachments/ingest");
            ApplyAuth(request);
            request.Content = new StringContent(JsonSerializer.Serialize(payload, _jsonOptions), Encoding.UTF8, "application/json");
            using var response = await _http.SendAsync(request);
            var body = await response.Content.ReadAsStringAsync();
            response.EnsureSuccessStatusCode();
            using var doc = JsonDocument.Parse(body);
            if (!doc.RootElement.TryGetProperty("upload", out var upload))
            {
                return;
            }
            AddUploadedAttachments(upload, dialog.FileNames);
            RenderComposerAttachmentStatus();
            WorkspaceSidebar.ConnectionStatusText.Text = $"已添加附件：{_pendingAttachments.Count} 个。";
        }
        catch (Exception ex)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = $"添加附件失败：{ex.Message}";
        }
    }

    internal void AddUploadedAttachments(JsonElement upload, IReadOnlyList<string>? sourcePaths = null)
    {
        var localPathByName = sourcePaths?
            .Where(File.Exists)
            .GroupBy(path => Path.GetFileName(path), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.OrdinalIgnoreCase)
            ?? new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (upload.TryGetProperty("attachments", out var attachments) && attachments.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in attachments.EnumerateArray())
            {
                var fileId = ReadJsonString(item, "file_id");
                if (string.IsNullOrWhiteSpace(fileId) || _pendingAttachments.Any(existing => existing.FileId == fileId))
                {
                    continue;
                }
                var name = ReadJsonString(item, "name");
                var relativePath = ReadJsonString(item, "relative_path");
                var uri = ReadJsonString(item, "uri");
                var localPath = "";
                if (!string.IsNullOrWhiteSpace(name) && localPathByName.TryGetValue(name, out var matched))
                {
                    localPath = matched;
                }
                else if (!string.IsNullOrWhiteSpace(relativePath) && localPathByName.TryGetValue(Path.GetFileName(relativePath), out matched))
                {
                    localPath = matched;
                }
                else if (!string.IsNullOrWhiteSpace(uri))
                {
                    localPath = ResolveAttachmentDisplayPath(uri);
                }
                _pendingAttachments.Add(new ComposerAttachment(
                    fileId,
                    name,
                    ReadJsonString(item, "mime_type"),
                    uri,
                    ReadJsonLong(item, "size_bytes"),
                    ReadJsonString(item, "purpose"),
                    relativePath,
                    localPath));
            }
        }

        if (upload.TryGetProperty("documents", out var documents) && documents.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in documents.EnumerateArray())
            {
                var path = ReadJsonString(item, "path");
                var text = ReadJsonString(item, "text");
                if (string.IsNullOrWhiteSpace(path) || string.IsNullOrWhiteSpace(text))
                {
                    continue;
                }
                _pendingAttachmentDocuments.Add(new ComposerDocumentPreview(path, text.Length > 1600 ? text[..1600] : text));
            }
        }
    }

    internal void ClearComposerAttachments()
    {
        _pendingAttachments.Clear();
        _pendingAttachmentDocuments.Clear();
        RenderComposerAttachmentStatus();
        WorkspaceSidebar.ConnectionStatusText.Text = "已清空待发送附件。";
    }

    internal void RemoveAttachmentButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not FrameworkElement { Tag: string fileId } || string.IsNullOrWhiteSpace(fileId))
        {
            return;
        }
        var removed = _pendingAttachments.FirstOrDefault(item => string.Equals(item.FileId, fileId, StringComparison.OrdinalIgnoreCase));
        _pendingAttachments.RemoveAll(item => string.Equals(item.FileId, fileId, StringComparison.OrdinalIgnoreCase));
        if (removed is not null)
        {
            _pendingAttachmentDocuments.RemoveAll(item =>
                string.Equals(item.Path, removed.RelativePath, StringComparison.OrdinalIgnoreCase)
                || string.Equals(Path.GetFileName(item.Path), removed.Name, StringComparison.OrdinalIgnoreCase));
        }
        RenderComposerAttachmentStatus();
        WorkspaceSidebar.ConnectionStatusText.Text = "已移除附件。";
    }

    internal void RenderComposerAttachmentStatus()
    {
        _composerAttachments.Clear();
        foreach (var attachment in _pendingAttachments)
        {
            _composerAttachments.Add(ComposerAttachmentViewModel.FromAttachment(
                attachment.FileId,
                attachment.Name,
                attachment.MimeType,
                attachment.LocalPath,
                attachment.SizeBytes));
        }
        var planEnabled = GetSettingBool(PlanModeSetting);
        var goalEnabled = GetSettingBool(PursueGoalSetting);
        var goal = GetSettingString(PursueGoalTextSetting);
        var attachmentText = _pendingAttachments.Count > 0 ? $"{_pendingAttachments.Count} file{(_pendingAttachments.Count == 1 ? "" : "s")}" : "";
        ChatWorkspace.AttachmentStatusText.Text = attachmentText;
        ChatWorkspace.EmptyAttachmentStatusText.Text = attachmentText;
        var attachmentVisible = string.IsNullOrWhiteSpace(attachmentText) ? Visibility.Collapsed : Visibility.Visible;
        ChatWorkspace.AttachmentStatusText.Visibility = attachmentVisible;
        ChatWorkspace.EmptyAttachmentStatusText.Visibility = attachmentVisible;

        var planSummary = GetSettingString(PlanSummarySetting);
        var planLabel = string.IsNullOrWhiteSpace(planSummary) ? "× Plan" : $"× Plan: {TrimStatusText(planSummary, 28)}";
        ChatWorkspace.ClearPlanButton.Content = planLabel;
        ChatWorkspace.EmptyClearPlanButton.Content = planLabel;
        ChatWorkspace.ClearPlanButton.Visibility = planEnabled ? Visibility.Visible : Visibility.Collapsed;
        ChatWorkspace.EmptyClearPlanButton.Visibility = planEnabled ? Visibility.Visible : Visibility.Collapsed;
        var goalStatus = GetSettingString(PursueGoalStatusSetting);
        var goalProgress = GetSettingString(PursueGoalProgressSetting);
        var goalDescriptor = string.IsNullOrWhiteSpace(goalStatus)
            ? goal
            : string.IsNullOrWhiteSpace(goalProgress) ? $"{goalStatus} · {goal}" : $"{goalStatus} {goalProgress}% · {goal}";
        var goalLabel = string.IsNullOrWhiteSpace(goalDescriptor) ? "× Goal" : $"× Goal: {TrimStatusText(goalDescriptor, 32)}";
        ChatWorkspace.ClearGoalButton.Content = goalLabel;
        ChatWorkspace.EmptyClearGoalButton.Content = goalLabel;
        ChatWorkspace.ClearGoalButton.Visibility = goalEnabled ? Visibility.Visible : Visibility.Collapsed;
        ChatWorkspace.EmptyClearGoalButton.Visibility = goalEnabled ? Visibility.Visible : Visibility.Collapsed;

        RenderComposerStatusVisibility();
        ChatWorkspace.ComposerAttachmentsList.Visibility = _composerAttachments.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        ChatWorkspace.EmptyComposerAttachmentsList.Visibility = _composerAttachments.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    internal void RenderComposerStatusVisibility()
    {
        // @提及预览已改为悬浮气泡，不再占底部状态行；这里只负责刷新气泡状态。
        RenderAgentMentionStatus();
        var statusVisible = _pendingAttachments.Count > 0
            || GetSettingBool(PlanModeSetting)
            || GetSettingBool(PursueGoalSetting)
                ? Visibility.Visible
                : Visibility.Collapsed;
        ChatWorkspace.ComposerStatusPanel.Visibility = statusVisible;
        ChatWorkspace.EmptyComposerStatusPanel.Visibility = statusVisible;
    }

    internal static string GuessMimeType(string path)
    {
        return Path.GetExtension(path).ToLowerInvariant() switch
        {
            ".png" => "image/png",
            ".jpg" or ".jpeg" => "image/jpeg",
            ".gif" => "image/gif",
            ".webp" => "image/webp",
            ".pdf" => "application/pdf",
            ".txt" or ".md" or ".markdown" or ".rst" or ".log" or ".py" or ".json" or ".jsonl" or ".yaml" or ".yml" or ".csv" => "text/plain",
            _ => "application/octet-stream",
        };
    }

    internal string ResolveAttachmentDisplayPath(string uri)
    {
        if (string.IsNullOrWhiteSpace(uri))
        {
            return "";
        }
        try
        {
            var expanded = Environment.ExpandEnvironmentVariables(uri.Trim());
            var full = Path.IsPathRooted(expanded) ? expanded : Path.Combine(_rootDir, expanded);
            return File.Exists(full) ? full : "";
        }
        catch
        {
            return "";
        }
    }

    internal List<Dictionary<string, object?>> BuildPendingAttachmentPayload()
    {
        return _pendingAttachments.Select(item => new Dictionary<string, object?>
        {
            ["file_id"] = item.FileId,
            ["name"] = item.Name,
            ["mime_type"] = item.MimeType,
            ["uri"] = item.Uri,
            ["size_bytes"] = item.SizeBytes,
            ["purpose"] = item.Purpose,
            ["relative_path"] = item.RelativePath,
        }).ToList();
    }

    internal List<Dictionary<string, object?>> BuildPendingDocumentPayload()
    {
        return _pendingAttachmentDocuments.Select(item => new Dictionary<string, object?>
        {
            ["path"] = item.Path,
            ["text"] = item.TextPreview,
        }).ToList();
    }
}


