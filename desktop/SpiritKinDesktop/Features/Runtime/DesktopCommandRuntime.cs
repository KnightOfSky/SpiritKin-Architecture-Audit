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
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal async Task RefreshAllAsync()
    {
        await LoadStateAsync();
        _workspaceControllerValue.StartWebSocket();
        await _safetyControllerValue.LoadAsync();
        await _workspaceControllerValue.LoadAvatarAsync();
        await _servicesControllerValue.LoadServicesAsync();
        await LoadSyncAsync();
        await LoadLogsAsync();
        await LoadDailyAsync();
        await LoadDiagnosticsAsync();
        await LoadModuleManagementAsync();
        await _workflowControllerValue.LoadWorkflowsAsync();
        await LoadSkillsAsync();
        await _learningControllerValue.LoadLearningAsync();
        await _evolutionControllerValue.LoadAsync();
        await _contextControllerValue.LoadContextAsync();
        await _contextControllerValue.LoadProjectOverviewAsync();
        await _contextControllerValue.LoadCollaborationAsync();
        await _agentsControllerValue.LoadAgentManagementAsync();
    }

    internal async Task LoadStateAsync()
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, _workspaceControllerValue.DesktopStateUrl());
            _workspaceControllerValue.ApplyAuth(request);
            using var response = await _http.SendAsync(request);
            var text = await response.Content.ReadAsStringAsync();
            response.EnsureSuccessStatusCode();
            var envelope = JsonSerializer.Deserialize<StateEnvelope>(text, _jsonOptions);
            if (envelope?.Ok == true && envelope.State is not null)
            {
                var nextState = ApplyPendingDeletions(envelope.State.Normalized());
                if (ShouldSuppressConsumedPending(PendingInfo(nextState.Pending)))
                {
                    nextState.Pending = null;
                }
                _state = nextState;
                _contextControllerValue.SetCollaborationChatActive(
                    _composerControllerValue.GetSettingBool(ComposerController.CollaborationModeSetting));
                RenderState();
                _workspaceControllerValue.SetConnected(true, "共享状态已同步");
            }
            else
            {
                throw new InvalidOperationException("desktop state response is invalid");
            }
        }
        catch (Exception ex)
        {
            _workspaceControllerValue.SetConnected(false, $"状态同步失败：{ex.Message}");
        }
    }

    internal async Task SaveStateAsync()
    {
        System.Threading.Interlocked.Increment(ref _pendingSaveRequests);
        await _saveStateLock.WaitAsync();
        try
        {
            var deletedSessionIds = _pendingDeletedSessionIds.ToArray();
            var deletedProjectIds = _pendingDeletedProjectIds.ToArray();
            var deletedTaskIds = _pendingDeletedTaskIds.ToArray();
            var deletedMessageIds = _pendingDeletedMessageIds.ToDictionary(
                entry => entry.Key,
                entry => entry.Value.ToArray(),
                StringComparer.OrdinalIgnoreCase);
            _state.UpdatedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var payload = new
            {
                client_id = "spiritkin_wpf_desktop",
                state = _state,
                deleted_session_ids = deletedSessionIds,
                deleted_project_ids = deletedProjectIds,
                deleted_task_ids = deletedTaskIds,
                deleted_message_ids = deletedMessageIds,
            };
            using var request = new HttpRequestMessage(HttpMethod.Post, _workspaceControllerValue.DesktopStateUrl());
            _workspaceControllerValue.ApplyAuth(request);
            request.Content = new StringContent(JsonSerializer.Serialize(payload, _jsonOptions), Encoding.UTF8, "application/json");
            using var response = await _http.SendAsync(request);
            var text = await response.Content.ReadAsStringAsync();
            response.EnsureSuccessStatusCode();
            var envelope = JsonSerializer.Deserialize<StateEnvelope>(text, _jsonOptions);
            if (envelope?.State is not null)
            {
                var savedState = envelope.State.Normalized();
                foreach (var id in deletedSessionIds.Where(id => !StateContainsSession(savedState, id)))
                {
                    _pendingDeletedSessionIds.Remove(id);
                }
                foreach (var id in deletedProjectIds.Where(id => !StateContainsProject(savedState, id)))
                {
                    _pendingDeletedProjectIds.Remove(id);
                }
                foreach (var id in deletedTaskIds.Where(id => !StateContainsTask(savedState, id)))
                {
                    _pendingDeletedTaskIds.Remove(id);
                }
                foreach (var (sessionId, ids) in deletedMessageIds)
                {
                    if (savedState.Sessions.FirstOrDefault(session => string.Equals(session.Id, sessionId, StringComparison.OrdinalIgnoreCase)) is not { } savedSession)
                    {
                        _pendingDeletedMessageIds.Remove(sessionId);
                        continue;
                    }
                    var savedMessageIds = savedSession.Messages.Select(message => message.Id).ToHashSet(StringComparer.OrdinalIgnoreCase);
                    if (_pendingDeletedMessageIds.TryGetValue(sessionId, out var pendingIds))
                    {
                        pendingIds.RemoveWhere(id => !savedMessageIds.Contains(id));
                        if (pendingIds.Count == 0)
                        {
                            _pendingDeletedMessageIds.Remove(sessionId);
                        }
                    }
                }
                var nextState = ApplyPendingDeletions(savedState);
                if (ShouldSuppressConsumedPending(PendingInfo(nextState.Pending)))
                {
                    nextState.Pending = null;
                }
                // 回包覆盖保护：HTTP 往返期间本地状态可能又被改过（还有排队中的保存）。
                // 此时直接用回包替换会把新改动冲掉（曾丢过刚 @ 进会话的参与者），
                // 跳过应用，让排队的那次保存带着最新状态去落地。
                if (System.Threading.Interlocked.CompareExchange(ref _pendingSaveRequests, 0, 0) <= 1)
                {
                    _state = nextState;
                    RenderState();
                }
                _workspaceControllerValue.SetConnected(true, "共享状态已保存");
            }
        }
        catch (Exception ex)
        {
            _workspaceControllerValue.SetConnected(false, $"保存失败：{ex.Message}");
        }
        finally
        {
            System.Threading.Interlocked.Decrement(ref _pendingSaveRequests);
            _saveStateLock.Release();
        }
    }

    private DesktopState ApplyPendingDeletions(DesktopState state)
    {
        state.Sessions.RemoveAll(session => _pendingDeletedSessionIds.Contains(session.Id));
        state.Projects.RemoveAll(project => _pendingDeletedProjectIds.Contains(project.Id));
        state.Tasks.RemoveAll(task => _pendingDeletedTaskIds.Contains(task.Id));
        foreach (var session in state.Sessions)
        {
            if (_pendingDeletedMessageIds.TryGetValue(session.Id, out var ids))
            {
                session.Messages.RemoveAll(message => ids.Contains(message.Id));
            }
        }
        if (state.Sessions.Count == 0)
        {
            state.Sessions.Add(DesktopState.DefaultSession());
        }
        if (!state.Sessions.Any(session => string.Equals(session.Id, state.ActiveSessionId, StringComparison.OrdinalIgnoreCase)))
        {
            state.ActiveSessionId = state.Sessions
                .OrderBy(session => WorkspaceController.IsArchived(session.Status))
                .ThenByDescending(session => session.UpdatedAt)
                .First()
                .Id;
        }
        return state.Normalized();
    }

    private static bool StateContainsSession(DesktopState state, string id) =>
        state.Sessions.Any(session => string.Equals(session.Id, id, StringComparison.OrdinalIgnoreCase));

    private static bool StateContainsProject(DesktopState state, string id) =>
        state.Projects.Any(project => string.Equals(project.Id, id, StringComparison.OrdinalIgnoreCase));

    private static bool StateContainsTask(DesktopState state, string id) =>
        state.Tasks.Any(task => string.Equals(task.Id, id, StringComparison.OrdinalIgnoreCase));

}
