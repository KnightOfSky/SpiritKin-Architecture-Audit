using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed partial class SkillsController
{
    internal async Task LoadSkillsAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/skills");
            RenderSkills(doc.RootElement.GetProperty("skills"));
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillActionText.Text = $"Skills 加载失败：{ex.Message}";
        }
    }

    private void RenderSkills(JsonElement state)
    {
        var previous = WorkbenchShell.ManagementPanels.SkillsList.SelectedValue as string;
        _skills.Clear();
        if (state.TryGetProperty("skills", out var skills) && skills.ValueKind == JsonValueKind.Array)
        {
            foreach (var skill in skills.EnumerateArray())
            {
                _skills.Add(new SkillViewModel(
                    ReadJsonString(skill, "name"),
                    ReadJsonString(skill, "description"),
                    ReadJsonString(skill, "status"),
                    ReadJsonString(skill, "version"),
                    ReadJsonString(skill, "risk_level"),
                    ReadJsonStringArray(skill, "trigger_intents"),
                    ReadJsonStringArray(skill, "tool_allowlist"),
                    skill.TryGetProperty("steps", out var steps) ? FormatJson(steps) : "[]",
                    ReadJsonString(skill, "rollback_strategy"),
                    ReadJsonStringArray(skill, "success_criteria"),
                    ReadJsonString(skill, "owner_agent_id"),
                    ReadJsonString(skill, "owner_domain"),
                    ReadJsonString(skill, "workspace_path"),
                    ReadJsonString(skill, "source_type"),
                    ReadJsonString(skill, "promotion_status"),
                    ReadJsonString(skill, "review_gate"),
                    skill.TryGetProperty("metadata", out var metadata) ? ReadJsonString(metadata, "ui_binding_status") : "",
                    skill.TryGetProperty("metadata", out var metadataForBindings) && metadataForBindings.TryGetProperty("ui_bindings", out var uiBindings) ? FormatJson(uiBindings) : "[]",
                    skill.TryGetProperty("debug_summary", out var debugSummary) ? BuildSkillDebugSummary(debugSummary) : ""));
            }
        }
        WorkbenchShell.ManagementPanels.SkillsList.SelectedValue = !string.IsNullOrWhiteSpace(previous) && _skills.Any(skill => skill.Name == previous)
            ? previous
            : _skills.FirstOrDefault()?.Name;
        WorkbenchShell.ManagementPanels.SkillsSummaryText.Text = $"Skills: {ReadJsonInt(state, "count")} · store: {ReadJsonString(state, "store_path")}";
        RenderSkillSources(state.TryGetProperty("skill_sources", out var sources) ? sources : default);
        RenderSelectedSkillEditor();
    }

    private static string BuildSkillDebugSummary(JsonElement debug)
    {
        if (debug.ValueKind != JsonValueKind.Object)
        {
            return "调试：暂无运行记录";
        }
        var total = ReadJsonInt(debug, "total_count");
        var success = ReadJsonInt(debug, "success_count");
        var dryRuns = ReadJsonInt(debug, "dry_run_count");
        var replayTotal = ReadJsonInt(debug, "replay_total_count");
        var replaySuccess = ReadJsonInt(debug, "replay_success_count");
        var lastSuccess = ReadJsonBool(debug, "last_run_success", false) ? "成功" : "失败";
        var lastDryRun = ReadJsonBool(debug, "last_run_dry_run", false) ? "预演" : "执行";
        var lastAt = ReadJsonString(debug, "last_run_at");
        var lastText = string.IsNullOrWhiteSpace(lastAt) ? "无最近运行" : $"最近{lastDryRun}{lastSuccess}";
        return $"调试：执行 {success}/{total} · 预演 {replaySuccess}/{replayTotal} · dry-run {dryRuns} · {lastText}";
    }

    private void RenderSkillSources(JsonElement state)
    {
        var previous = WorkbenchShell.ManagementPanels.SkillSourcesList.SelectedValue as string;
        _skillSources.Clear();
        if (state.ValueKind == JsonValueKind.Object && state.TryGetProperty("sources", out var sources) && sources.ValueKind == JsonValueKind.Array)
        {
            foreach (var source in sources.EnumerateArray())
            {
                _skillSources.Add(new SkillSourceViewModel(
                    ReadJsonString(source, "source_id"),
                    ReadJsonString(source, "label"),
                    ReadJsonString(source, "url"),
                    ReadJsonString(source, "branch"),
                    ReadJsonString(source, "source_type"),
                    ReadJsonString(source, "status"),
                    ReadJsonString(source, "trust_level"),
                    ReadJsonString(source, "target_scope"),
                    ReadJsonString(source, "quarantine_path"),
                    ReadJsonInt(source, "candidate_count"),
                    ReadJsonInt(source, "scanned_file_count"),
                    ReadJsonStringArray(source, "warnings")));
            }
        }
        WorkbenchShell.ManagementPanels.SkillSourcesList.SelectedValue = !string.IsNullOrWhiteSpace(previous) && _skillSources.Any(source => source.SourceId == previous)
            ? previous
            : _skillSources.FirstOrDefault()?.SourceId;
        WorkbenchShell.ManagementPanels.SkillSourcesSummaryText.Text = state.ValueKind == JsonValueKind.Object
            ? $"来源 {_skillSources.Count} · 隔离区 {ReadJsonString(state, "quarantine_dir", "--")}"
            : "来源：后端未返回 skill_sources";
        if (state.ValueKind == JsonValueKind.Object && state.TryGetProperty("policy", out var policy))
        {
            WorkbenchShell.ManagementPanels.SkillSourceAutonomousDiscoveryBox.IsChecked = ReadJsonBool(policy, "allow_autonomous_discovery", false);
        }
        RenderSelectedSkillSourceEditor();
    }

    internal void RenderSelectedSkillEditor()
    {
        var selected = WorkbenchShell.ManagementPanels.SkillsList.SelectedValue as string;
        var skill = _skills.FirstOrDefault(item => item.Name == selected) ?? _skills.FirstOrDefault();
        if (skill is null)
        {
            WorkbenchShell.ManagementPanels.SkillNameBox.Clear();
            WorkbenchShell.ManagementPanels.SkillVersionBox.Text = "0.1.0";
            WorkbenchShell.ManagementPanels.SkillDescriptionBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.SkillStatusBox, "candidate");
            SetComboText(WorkbenchShell.ManagementPanels.SkillRiskBox, "low");
            WorkbenchShell.ManagementPanels.SkillTriggersBox.Clear();
            WorkbenchShell.ManagementPanels.SkillAllowlistBox.Clear();
            WorkbenchShell.ManagementPanels.SkillStepsJsonBox.Text = "[]";
            WorkbenchShell.ManagementPanels.SkillUiBindingsBox.Text = "[]";
            WorkbenchShell.ManagementPanels.SkillSuccessCriteriaBox.Clear();
            WorkbenchShell.ManagementPanels.SkillRollbackBox.Text = "manual_review";
            WorkbenchShell.ManagementPanels.SkillOwnerAgentBox.SelectedValue = "skill_runner";
            WorkbenchShell.ManagementPanels.SkillOwnerAgentBox.Text = "skill_runner";
            WorkbenchShell.ManagementPanels.SkillWorkspacePathBox.Text = "state/agents/skill_runner/workspace";
            SetComboText(WorkbenchShell.ManagementPanels.SkillSourceTypeBox, "human");
            WorkbenchShell.ManagementPanels.SkillRunInputsBox.Text = "{}";
            WorkbenchShell.ManagementPanels.SkillRunResultBox.Text = "--";
            return;
        }
        WorkbenchShell.ManagementPanels.SkillNameBox.Text = skill.Name;
        WorkbenchShell.ManagementPanels.SkillVersionBox.Text = skill.Version;
        WorkbenchShell.ManagementPanels.SkillDescriptionBox.Text = skill.Description;
        SetComboText(WorkbenchShell.ManagementPanels.SkillStatusBox, skill.Status);
        SetComboText(WorkbenchShell.ManagementPanels.SkillRiskBox, skill.RiskLevel);
        WorkbenchShell.ManagementPanels.SkillTriggersBox.Text = string.Join(Environment.NewLine, skill.TriggerIntents);
        WorkbenchShell.ManagementPanels.SkillAllowlistBox.Text = string.Join(Environment.NewLine, skill.ToolAllowlist);
        WorkbenchShell.ManagementPanels.SkillStepsJsonBox.Text = skill.StepsJson;
        WorkbenchShell.ManagementPanels.SkillUiBindingsBox.Text = string.IsNullOrWhiteSpace(skill.UiBindingsJson) ? "[]" : skill.UiBindingsJson;
        WorkbenchShell.ManagementPanels.SkillSuccessCriteriaBox.Text = string.Join(Environment.NewLine, skill.SuccessCriteria);
        WorkbenchShell.ManagementPanels.SkillRollbackBox.Text = skill.RollbackStrategy;
        WorkbenchShell.ManagementPanels.SkillOwnerAgentBox.SelectedValue = SelectExistingId(skill.OwnerAgentId, Agents.Select(item => item.AgentId));
        WorkbenchShell.ManagementPanels.SkillOwnerAgentBox.Text = string.IsNullOrWhiteSpace(skill.OwnerAgentId) ? "skill_runner" : skill.OwnerAgentId;
        WorkbenchShell.ManagementPanels.SkillWorkspacePathBox.Text = string.IsNullOrWhiteSpace(skill.WorkspacePath) ? $"state/agents/{WorkbenchShell.ManagementPanels.SkillOwnerAgentBox.Text}/workspace" : skill.WorkspacePath;
        SetComboText(WorkbenchShell.ManagementPanels.SkillSourceTypeBox, string.IsNullOrWhiteSpace(skill.SourceType) ? "human" : skill.SourceType);
        WorkbenchShell.ManagementPanels.SkillRunInputsBox.Text = "{}";
        WorkbenchShell.ManagementPanels.SkillRunResultBox.Text = "--";
    }

    internal void NewSkill()
    {
        var name = UniqueId("skill", _skills.Select(item => item.Name));
        var skill = new SkillViewModel(name, "", "candidate", "0.1.0", "low", Array.Empty<string>(), Array.Empty<string>(), "[]", "manual_review", Array.Empty<string>(), "skill_runner", "skill", "state/agents/skill_runner/workspace", "human", "candidate", "core_review", "", "[]", "调试：暂无运行记录");
        _skills.Add(skill);
        WorkbenchShell.ManagementPanels.SkillsList.SelectedValue = name;
        RenderSelectedSkillEditor();
        WorkbenchShell.ManagementPanels.SkillActionText.Text = "已新建 Skill 草稿，编辑后保存。";
    }

    internal async Task SaveSkillAsync()
    {
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", BuildSkillPayload("save"));
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "Skill 保存失败");
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillActionText.Text = "Skill 已保存。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillActionText.Text = $"Skill 保存失败：{ex.Message}";
        }
    }

    internal async Task DeleteSkillAsync()
    {
        var name = WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            WorkbenchShell.ManagementPanels.SkillActionText.Text = "请先选择或填写 Skill 名称。";
            return;
        }
        if (!ConfirmDestructiveAction("删除 Skill", $"确定要删除 Skill“{name}”吗？"))
        {
            return;
        }
        using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", new { action = "delete", name });
        RenderSkills(doc.RootElement.GetProperty("skills"));
        WorkbenchShell.ManagementPanels.SkillActionText.Text = $"已删除 Skill：{name}";
    }

    internal async Task SkillActionAsync(string action)
    {
        try
        {
            object payload = action switch
            {
                "review_candidates" => new { action, reviewer = "wpf_desktop" },
                "export" => new { action, name = WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim(), export_id = $"skill-export-{DateTime.Now:yyyyMMdd-HHmmss}" },
                "promote" => new { action, name = WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim(), reviewer = "wpf_desktop", core_review_approved = true, review_reason = "Manual desktop promotion approval." },
                _ => new { action, name = WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim(), reviewer = "wpf_desktop" },
            };
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", payload);
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, $"Skill 动作失败：{action}");
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillActionText.Text = $"Skill 动作完成：{action}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillActionText.Text = $"Skill 动作失败：{ex.Message}";
        }
    }

    internal async Task BindSkillUiAsync()
    {
        var name = WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            WorkbenchShell.ManagementPanels.SkillActionText.Text = "请先选择或填写 Skill 名称。";
            return;
        }
        try
        {
            using var bindingsDoc = JsonDocument.Parse(string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.SkillUiBindingsBox.Text) ? "[]" : WorkbenchShell.ManagementPanels.SkillUiBindingsBox.Text);
            if (bindingsDoc.RootElement.ValueKind != JsonValueKind.Array || bindingsDoc.RootElement.GetArrayLength() == 0)
            {
                WorkbenchShell.ManagementPanels.SkillActionText.Text = "UI 绑定 JSON 必须是非空数组。";
                return;
            }
            var bindings = JsonSerializer.Deserialize<object>(bindingsDoc.RootElement.GetRawText(), _jsonOptions);
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", new
            {
                action = "bind_ui",
                name,
                ui_bindings = bindings,
                reviewer = "wpf_desktop",
            });
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "UI 绑定失败");
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillActionText.Text = $"UI 绑定已保存：{name}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillActionText.Text = $"UI 绑定失败：{ex.Message}";
        }
    }

    internal async Task RunSelectedSkillAsync(bool dryRun)
    {
        var name = WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            WorkbenchShell.ManagementPanels.SkillActionText.Text = "请先选择或填写 Skill 名称。";
            return;
        }
        try
        {
            using var inputsDoc = JsonDocument.Parse(string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.SkillRunInputsBox.Text) ? "{}" : WorkbenchShell.ManagementPanels.SkillRunInputsBox.Text);
            if (inputsDoc.RootElement.ValueKind != JsonValueKind.Object)
            {
                WorkbenchShell.ManagementPanels.SkillActionText.Text = "Skill 输入 JSON 必须是对象。";
                return;
            }
            var inputs = JsonSerializer.Deserialize<object>(inputsDoc.RootElement.GetRawText(), _jsonOptions);
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", new
            {
                action = dryRun ? "dry_run" : "run",
                name,
                inputs,
                reviewer = "wpf_desktop",
                dry_run = dryRun,
            });
            if (doc.RootElement.TryGetProperty("skill_run", out var run))
            {
                WorkbenchShell.ManagementPanels.SkillRunResultBox.Text = FormatJson(run);
            }
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, dryRun ? "Skill 预演失败" : "Skill 执行失败");
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillActionText.Text = dryRun ? $"Skill 预演完成：{name}" : $"Skill 执行完成：{name}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillActionText.Text = dryRun ? $"Skill 预演失败：{ex.Message}" : $"Skill 执行失败：{ex.Message}";
            if (string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.SkillRunResultBox.Text) || WorkbenchShell.ManagementPanels.SkillRunResultBox.Text == "--")
            {
                WorkbenchShell.ManagementPanels.SkillRunResultBox.Text = ex.Message;
            }
        }
    }

    internal async Task RegisterSkillSourceAsync()
    {
        var url = WorkbenchShell.ManagementPanels.SkillSourceUrlBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(url))
        {
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = "请填写 Git URL 或本地路径。";
            return;
        }
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", new
            {
                action = "register_source",
                source_id = WorkbenchShell.ManagementPanels.SkillSourceIdBox.Text.Trim(),
                label = WorkbenchShell.ManagementPanels.SkillSourceLabelBox.Text.Trim(),
                url,
                branch = WorkbenchShell.ManagementPanels.SkillSourceBranchBox.Text.Trim(),
                reviewer = "wpf_desktop",
            });
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "注册 Skill 来源失败");
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = "Skill 来源已注册，下一步可同步扫描。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = $"注册 Skill 来源失败：{ex.Message}";
        }
    }

    internal async Task SkillSourceActionAsync(string action)
    {
        var sourceId = SelectedSkillSourceId();
        if (string.IsNullOrWhiteSpace(sourceId))
        {
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = "请先选择或填写来源 ID。";
            return;
        }
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", new
            {
                action,
                source_id = sourceId,
                reviewer = "wpf_desktop",
            });
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, $"Skill 来源动作失败：{action}");
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = action == "import_source_candidates"
                ? $"已导入来源候选：{sourceId}"
                : $"来源同步/扫描完成：{sourceId}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = $"Skill 来源动作失败：{ex.Message}";
        }
    }

    internal async Task DeleteSkillSourceAsync()
    {
        var sourceId = SelectedSkillSourceId();
        if (string.IsNullOrWhiteSpace(sourceId))
        {
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = "请先选择或填写来源 ID。";
            return;
        }
        if (!ConfirmDestructiveAction("删除 Skill 来源", $"确定要删除来源“{sourceId}”吗？隔离区文件不会自动删除。"))
        {
            return;
        }
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", new
            {
                action = "delete_source",
                source_id = sourceId,
                reviewer = "wpf_desktop",
            });
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = $"已删除 Skill 来源：{sourceId}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = $"删除 Skill 来源失败：{ex.Message}";
        }
    }

    internal async Task SyncOpenClawSkillsAsync()
    {
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", new
            {
                action = "sync_openclaw",
                source_id = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.SkillSourceIdBox.Text) ? "openclaw-cli" : WorkbenchShell.ManagementPanels.SkillSourceIdBox.Text.Trim(),
                reviewer = "wpf_desktop",
            });
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "OpenClaw Skill 同步失败");
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = "OpenClaw Skill 已同步到隔离区；请检查候选后再导入。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = $"OpenClaw Skill 同步失败：{ex.Message}";
        }
    }

    internal async Task SaveSkillSourcePolicyAsync()
    {
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/skills", new
            {
                action = "save_source_policy",
                policy = new
                {
                    allow_autonomous_discovery = WorkbenchShell.ManagementPanels.SkillSourceAutonomousDiscoveryBox.IsChecked == true,
                },
                reviewer = "wpf_desktop",
            });
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "Skill 来源策略保存失败");
            RenderSkills(doc.RootElement.GetProperty("skills"));
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = WorkbenchShell.ManagementPanels.SkillSourceAutonomousDiscoveryBox.IsChecked == true
                ? "已允许自动发现，但导入仍只会进入隔离候选。"
                : "已关闭自动发现；手动注册/同步仍可使用。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = $"Skill 来源策略保存失败：{ex.Message}";
        }
    }

    private string SelectedSkillSourceId()
    {
        return ((WorkbenchShell.ManagementPanels.SkillSourcesList.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.SkillSourceIdBox.Text).Trim();
    }

    internal void RenderSelectedSkillSourceEditor()
    {
        var selected = WorkbenchShell.ManagementPanels.SkillSourcesList.SelectedValue as string;
        var source = _skillSources.FirstOrDefault(item => item.SourceId == selected);
        if (source is null)
        {
            WorkbenchShell.ManagementPanels.SkillSourceIdBox.Clear();
            WorkbenchShell.ManagementPanels.SkillSourceUrlBox.Clear();
            WorkbenchShell.ManagementPanels.SkillSourceLabelBox.Clear();
            WorkbenchShell.ManagementPanels.SkillSourceBranchBox.Clear();
            return;
        }
        WorkbenchShell.ManagementPanels.SkillSourceIdBox.Text = source.SourceId;
        WorkbenchShell.ManagementPanels.SkillSourceUrlBox.Text = source.Url;
        WorkbenchShell.ManagementPanels.SkillSourceLabelBox.Text = source.Label;
        WorkbenchShell.ManagementPanels.SkillSourceBranchBox.Text = source.Branch;
        WorkbenchShell.ManagementPanels.SkillSourceActionText.Text = $"{source.StatusLabel} · 候选 {source.CandidateCount} · 文件 {source.ScannedFileCount}{Environment.NewLine}{source.QuarantinePath}".Trim();
    }

    private object BuildSkillPayload(string action)
    {
        return new
        {
            action,
            name = WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim(),
            description = WorkbenchShell.ManagementPanels.SkillDescriptionBox.Text.Trim(),
            status = ComboText(WorkbenchShell.ManagementPanels.SkillStatusBox),
            version = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.SkillVersionBox.Text) ? "0.1.0" : WorkbenchShell.ManagementPanels.SkillVersionBox.Text.Trim(),
            risk_level = ComboText(WorkbenchShell.ManagementPanels.SkillRiskBox),
            trigger_intents = SplitLines(WorkbenchShell.ManagementPanels.SkillTriggersBox.Text),
            tool_allowlist = SplitLines(WorkbenchShell.ManagementPanels.SkillAllowlistBox.Text),
            steps = WorkbenchShell.ManagementPanels.SkillStepsJsonBox.Text.Trim(),
            success_criteria = SplitLines(WorkbenchShell.ManagementPanels.SkillSuccessCriteriaBox.Text),
            rollback_strategy = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.SkillRollbackBox.Text) ? "manual_review" : WorkbenchShell.ManagementPanels.SkillRollbackBox.Text.Trim(),
            owner_agent_id = ((WorkbenchShell.ManagementPanels.SkillOwnerAgentBox.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.SkillOwnerAgentBox.Text).Trim(),
            workspace_path = WorkbenchShell.ManagementPanels.SkillWorkspacePathBox.Text.Trim(),
            source_type = ComboText(WorkbenchShell.ManagementPanels.SkillSourceTypeBox),
            review_gate = "core_review",
        };
    }
}
