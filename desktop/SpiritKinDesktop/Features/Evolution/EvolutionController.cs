using SpiritKinDesktop.Controls;
using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed class EvolutionController
{
    private readonly ManagementPanelsView _panels;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string> _apiBase;
    private readonly Func<Task> _loadSkillsAsync;
    private bool _sandboxExecutionEnabled;

    public ObservableCollection<EventViewModel> LoopSteps { get; } = new();

    public ObservableCollection<EventViewModel> Actions { get; } = new();

    public ObservableCollection<EventViewModel> AgentSkills { get; } = new();

    public ObservableCollection<EventViewModel> Artifacts { get; } = new();

    public ObservableCollection<EventViewModel> DomainSkills { get; } = new();

    public ObservableCollection<GrowthCandidateViewModel> GrowthCandidates { get; } = new();

    public ObservableCollection<RuntimeHostViewModel> RuntimeHosts { get; } = new();

    public ObservableCollection<RuntimeHostViewModel> RuntimeMigrationTargets { get; } = new();

    public ObservableCollection<RuntimeCheckpointViewModel> RuntimeCheckpoints { get; } = new();

    public ObservableCollection<EventViewModel> RuntimeWorldEntities { get; } = new();

    public EvolutionController(
        ManagementPanelsView panels,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string> apiBase,
        Func<Task> loadSkillsAsync)
    {
        _panels = panels;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _apiBase = apiBase;
        _loadSkillsAsync = loadSkillsAsync;
    }

    public async Task LoadAsync()
    {
        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/desktop/evolution");
            Render(doc.RootElement.GetProperty("evolution"));
            await LoadGrowthAsync();
            await LoadRuntimeContinuityAsync();
        }
        catch (Exception ex)
        {
            _panels.EvolutionSummaryText.Text = $"进化模块加载失败：{ex.Message}";
            _panels.GrowthRuntimeSummaryText.Text = "能力成长：进化模块不可用";
            _panels.RuntimeContinuitySummaryText.Text = "Runtime Continuity：进化模块不可用";
            LoopSteps.Clear();
            Actions.Clear();
            AgentSkills.Clear();
            Artifacts.Clear();
            DomainSkills.Clear();
            GrowthCandidates.Clear();
            RenderSelectedGrowthCandidate();
        }
    }

    public async Task LoadRuntimeContinuityAsync()
    {
        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/desktop/runtime-continuity");
            JsonHelpers.TryReadObject(doc.RootElement, "runtime_continuity", out var continuity);
            JsonHelpers.TryReadObject(continuity, "active_lease", out var lease);
            JsonHelpers.TryReadObject(continuity, "runtime_hosts", out var registry);
            JsonHelpers.TryReadObject(continuity, "checkpoints", out var checkpointSnapshot);
            JsonHelpers.TryReadObject(continuity, "world", out var world);
            var leaseHostId = JsonHelpers.ReadString(lease, "host_id");
            var leaseEpoch = JsonHelpers.ReadSafeInt(lease, "epoch");
            var leaseStatus = JsonHelpers.ReadString(lease, "effective_status", "missing");

            RuntimeHosts.Clear();
            RuntimeMigrationTargets.Clear();
            if (registry.TryGetProperty("hosts", out var hosts) && hosts.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in hosts.EnumerateArray())
                {
                    var hostId = JsonHelpers.ReadString(item, "host_id");
                    JsonHelpers.TryReadObject(item, "execution", out var execution);
                    var host = new RuntimeHostViewModel(
                        hostId,
                        JsonHelpers.ReadString(item, "label", hostId),
                        JsonHelpers.ReadString(item, "host_type", "host"),
                        JsonHelpers.ReadString(item, "effective_status", JsonHelpers.ReadString(item, "status", "unknown")),
                        JsonHelpers.ReadString(execution, "status", "not_reported"),
                        JsonHelpers.ReadSafeBool(item, "can_execute_workflows"),
                        JsonHelpers.ReadSafeBool(item, "can_observe"),
                        string.Equals(hostId, leaseHostId, StringComparison.Ordinal),
                        string.Equals(hostId, leaseHostId, StringComparison.Ordinal) ? leaseEpoch : 0);
                    RuntimeHosts.Add(host);
                    if (host.CanReceiveMigration)
                    {
                        RuntimeMigrationTargets.Add(host);
                    }
                }
            }

            RuntimeCheckpoints.Clear();
            if (checkpointSnapshot.TryGetProperty("checkpoints", out var checkpoints) && checkpoints.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in checkpoints.EnumerateArray())
                {
                    RuntimeCheckpoints.Add(new RuntimeCheckpointViewModel(
                        JsonHelpers.ReadString(item, "checkpoint_id"),
                        JsonHelpers.ReadString(item, "run_id"),
                        JsonHelpers.ReadString(item, "workflow_name"),
                        JsonHelpers.ReadSafeInt(item, "sequence"),
                        JsonHelpers.ReadString(item, "source_host_id"),
                        JsonHelpers.ReadString(item, "status", "unknown"),
                        JsonHelpers.ReadString(item, "created_at")));
                }
            }

            RuntimeWorldEntities.Clear();
            if (world.TryGetProperty("entities", out var entities) && entities.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in entities.EnumerateArray().Take(20))
                {
                    var label = JsonHelpers.ReadString(item, "label", JsonHelpers.ReadString(item, "kind", "entity"));
                    RuntimeWorldEntities.Add(new EventViewModel(
                        $"{label} · {JsonHelpers.ReadString(item, "status", "unknown")}",
                        $"{JsonHelpers.ReadString(item, "kind", "object")} · confidence {JsonHelpers.ReadDouble(item, "confidence"):F2} · {JsonHelpers.ReadString(item, "last_observed_at")}"));
                }
            }

            _panels.RuntimeHostsList.ItemsSource = RuntimeHosts;
            _panels.RuntimeCheckpointsList.ItemsSource = RuntimeCheckpoints;
            _panels.RuntimeWorldEntitiesList.ItemsSource = RuntimeWorldEntities;
            _panels.RuntimeMigrationCheckpointBox.ItemsSource = RuntimeCheckpoints.Where(item => item.Status == "active").ToArray();
            _panels.RuntimeMigrationTargetBox.ItemsSource = RuntimeMigrationTargets;
            _panels.RequestRuntimeMigrationButton.IsEnabled = RuntimeCheckpoints.Any(item => item.Status == "active") && RuntimeMigrationTargets.Count > 0;
            var summary = $"Runtime Continuity：租约 {(string.IsNullOrWhiteSpace(leaseHostId) ? "缺失" : leaseHostId)} / {leaseStatus} / epoch {leaseEpoch} · " +
                          $"Host {RuntimeHosts.Count} · Checkpoint {RuntimeCheckpoints.Count} · World 实体 {JsonHelpers.ReadSafeInt(world, "entity_count")} / 当前 {JsonHelpers.ReadSafeInt(world, "current_entity_count")}";
            _panels.RuntimeContinuitySummaryText.Text = summary;
            _panels.RuntimeContinuityStatusText.Text = summary + "。迁移不是重启；运行中的副作用节点必须先对账。";
        }
        catch (Exception ex)
        {
            _panels.RuntimeContinuitySummaryText.Text = $"Runtime Continuity：暂不可用（{ex.Message}）";
            _panels.RuntimeContinuityStatusText.Text = $"Continuity 加载失败：{ex.Message}";
            RuntimeHosts.Clear();
            RuntimeMigrationTargets.Clear();
            RuntimeCheckpoints.Clear();
            RuntimeWorldEntities.Clear();
            _panels.RequestRuntimeMigrationButton.IsEnabled = false;
        }
    }

    public async Task RequestRuntimeMigrationAsync()
    {
        if (_panels.RuntimeMigrationCheckpointBox.SelectedItem is not RuntimeCheckpointViewModel checkpoint ||
            _panels.RuntimeMigrationTargetBox.SelectedItem is not RuntimeHostViewModel target)
        {
            _panels.RuntimeContinuityStatusText.Text = "请选择 Checkpoint 和目标 Runtime Host。";
            return;
        }
        if (_panels.RuntimeMigrationConfirmBox.IsChecked != true)
        {
            _panels.RuntimeContinuityStatusText.Text = "请先确认 Checkpoint 恢复和副作用对账规则。";
            return;
        }
        try
        {
            _panels.RequestRuntimeMigrationButton.IsEnabled = false;
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/runtime-continuity", new
            {
                action = "request_migration",
                checkpoint_id = checkpoint.CheckpointId,
                target_host_id = target.HostId,
                confirmed = true,
                requested_by = "wpf-desktop",
            });
            EnsureOkResponse(doc.RootElement, "Runtime 迁移请求失败");
            _panels.RuntimeMigrationConfirmBox.IsChecked = false;
            await LoadRuntimeContinuityAsync();
            _panels.RuntimeContinuityStatusText.Text = $"迁移请求已提交：{checkpoint.WorkflowName} → {target.HostId}。等待目标 Host claim 并恢复。";
        }
        catch (Exception ex)
        {
            _panels.RuntimeContinuityStatusText.Text = $"Runtime 迁移请求失败：{ex.Message}";
            _panels.RequestRuntimeMigrationButton.IsEnabled = true;
        }
    }

    private async Task LoadGrowthAsync()
    {
        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/desktop/growth");
            JsonHelpers.TryReadObject(doc.RootElement, "growth", out var growth);
            JsonHelpers.TryReadObject(growth, "status_counts", out var counts);
            JsonHelpers.TryReadObject(growth, "builder_artifacts", out var builderArtifacts);
            JsonHelpers.TryReadObject(growth, "research_reports", out var researchReports);
            JsonHelpers.TryReadObject(growth, "sandbox_runtime", out var sandboxRuntime);
            JsonHelpers.TryReadObject(growth, "sandbox_executions", out var sandboxExecutions);
            JsonHelpers.TryReadObject(growth, "benchmarks", out var benchmarks);
            _sandboxExecutionEnabled = JsonHelpers.ReadSafeBool(sandboxRuntime, "candidate_execution_enabled");
            _panels.GrowthRuntimeSummaryText.Text =
                $"能力成长：{JsonHelpers.ReadSafeInt(growth, "candidate_count")} 个候选 · " +
                $"{JsonHelpers.ReadSafeInt(counts, "candidate")} 待审核 · " +
                $"{JsonHelpers.ReadSafeInt(counts, "registered")} 已注册 · " +
                $"{JsonHelpers.ReadSafeInt(builderArtifacts, "count")} 个 Builder 工件 · " +
                $"{JsonHelpers.ReadSafeInt(researchReports, "count")} 份公开研究 · " +
                $"{JsonHelpers.ReadSafeInt(sandboxExecutions, "count")} 次隔离测试 · " +
                $"{JsonHelpers.ReadSafeInt(benchmarks, "count")} 份 Benchmark · " +
                $"沙箱 {JsonHelpers.ReadString(sandboxRuntime, "status", "not_probed")}/" +
                $"{(_sandboxExecutionEnabled ? "执行可用" : "执行关闭")} · 不自动激活";
            GrowthCandidates.Clear();
            if (growth.TryGetProperty("candidates", out var candidates) && candidates.ValueKind == JsonValueKind.Array)
            {
                foreach (var candidate in candidates.EnumerateArray())
                {
                    var kind = JsonHelpers.ReadString(candidate, "kind", "capability");
                    var title = JsonHelpers.ReadString(candidate, "title", "未命名候选");
                    var status = JsonHelpers.ReadString(candidate, "status", "candidate");
                    var promotionStatus = JsonHelpers.ReadString(candidate, "promotion_status", status);
                    var stage = JsonHelpers.ReadString(candidate, "current_stage", "gap_analysis");
                    var workspace = JsonHelpers.ReadString(candidate, "workspace_id", "全局");
                    var stages = candidate.TryGetProperty("stages", out var stageItems) && stageItems.ValueKind == JsonValueKind.Array
                        ? stageItems.EnumerateArray().Select(item => item.GetString() ?? "").Where(item => item.Length > 0).ToArray()
                        : Array.Empty<string>();
                    JsonHelpers.TryReadObject(candidate, "evidence", out var evidence);
                    JsonHelpers.TryReadObject(candidate, "lineage", out var lineage);
                    JsonHelpers.TryReadObject(candidate, "resolution", out var resolution);
                    JsonHelpers.TryReadObject(evidence, "remote_research", out var remoteResearch);
                    JsonHelpers.TryReadObject(evidence, "sandbox_bundle", out var sandboxBundle);
                    JsonHelpers.TryReadObject(evidence, "sandbox_execution", out var sandboxExecution);
                    JsonHelpers.TryReadObject(evidence, "benchmark_report", out var benchmarkReport);
                    var builderPrepared = JsonHelpers.TryReadObject(evidence, "builder_artifact", out var builderArtifact);
                    GrowthCandidates.Add(new GrowthCandidateViewModel(
                        JsonHelpers.ReadString(candidate, "candidate_id"),
                        kind,
                        title,
                        status,
                        promotionStatus,
                        stage,
                        workspace == "全局" ? "" : workspace,
                        stages,
                        JsonHelpers.ReadString(lineage, "parent_candidate_id"),
                        JsonHelpers.ReadString(resolution, "status", "unrouted"),
                        JsonHelpers.ReadString(resolution, "target_kind"),
                        JsonHelpers.ReadString(resolution, "child_candidate_id"),
                        JsonHelpers.ReadString(remoteResearch, "report_id"),
                        JsonHelpers.ReadSafeInt(remoteResearch, "result_count"),
                        JsonHelpers.ReadString(remoteResearch, "query"),
                        builderPrepared,
                        JsonHelpers.ReadSafeInt(builderArtifact, "inventory_match_count"),
                        JsonHelpers.ReadString(builderArtifact, "verification_status"),
                        JsonHelpers.ReadString(builderArtifact, "registry_target"),
                        JsonHelpers.ReadSafeBool(builderArtifact, "human_required"),
                        !string.IsNullOrWhiteSpace(JsonHelpers.ReadString(sandboxBundle, "bundle_id")),
                        JsonHelpers.ReadString(sandboxBundle, "bundle_id"),
                        JsonHelpers.ReadSafeInt(sandboxBundle, "file_count"),
                        JsonHelpers.ReadString(sandboxExecution, "status"),
                        JsonHelpers.ReadString(sandboxExecution, "execution_id"),
                        JsonHelpers.ReadSafeInt(sandboxExecution, "exit_code"),
                        JsonHelpers.ReadString(benchmarkReport, "benchmark_id"),
                        JsonHelpers.ReadString(benchmarkReport, "promotion_status"),
                        JsonHelpers.ReadDouble(benchmarkReport, "overall_score"),
                        JsonHelpers.ReadDouble(benchmarkReport, "overall_delta")));
                }
            }
            RenderSelectedGrowthCandidate();
        }
        catch (Exception ex)
        {
            _sandboxExecutionEnabled = false;
            _panels.GrowthRuntimeSummaryText.Text = $"能力成长：暂不可用（{ex.Message}）";
            GrowthCandidates.Clear();
            RenderSelectedGrowthCandidate();
        }
    }

    public void Render(JsonElement evolution)
    {
        JsonHelpers.TryReadObject(evolution, "self_improvement_summary", out var summary);
        JsonHelpers.TryReadObject(summary, "counts", out var counts);
        JsonHelpers.TryReadObject(evolution, "trajectory", out var trajectory);
        JsonHelpers.TryReadObject(evolution, "agent_skill_distribution", out var distribution);
        JsonHelpers.TryReadObject(evolution, "training", out var training);
        JsonHelpers.TryReadObject(evolution, "failure_samples", out var failures);
        JsonHelpers.TryReadObject(evolution, "learning_artifacts", out var artifacts);
        JsonHelpers.TryReadObject(evolution, "domain_skill_templates", out var domainTemplates);
        JsonHelpers.TryReadObject(evolution, "jobs", out var jobs);
        JsonHelpers.TryReadObject(evolution, "review_gate_audit", out var reviewAudit);

        _panels.EvolutionSummaryText.Text = BuildSummaryText(evolution, counts, trajectory, distribution, artifacts, domainTemplates, jobs, reviewAudit);

        LoopSteps.Clear();
        if (evolution.TryGetProperty("loop_steps", out var steps) && steps.ValueKind == JsonValueKind.Array)
        {
            foreach (var step in steps.EnumerateArray())
            {
                LoopSteps.Add(new EventViewModel(
                    $"{UiDisplayText.Status(JsonHelpers.ReadString(step, "status"))} · {JsonHelpers.ReadString(step, "label")}",
                    JsonHelpers.ReadString(step, "detail")));
            }
        }

        Actions.Clear();
        if (evolution.TryGetProperty("action_items", out var actionItems) && actionItems.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in actionItems.EnumerateArray())
            {
                Actions.Add(new EventViewModel(
                    $"{UiDisplayText.Priority(JsonHelpers.ReadString(item, "priority", "medium"))} · {JsonHelpers.ReadString(item, "title")}",
                    JsonHelpers.ReadString(item, "detail")));
            }
        }

        AgentSkills.Clear();
        if (distribution.TryGetProperty("agents", out var agents) && agents.ValueKind == JsonValueKind.Array)
        {
            foreach (var agent in agents.EnumerateArray())
            {
                AgentSkills.Add(new EventViewModel(
                    $"{UiDisplayText.Domain(JsonHelpers.ReadString(agent, "agent_id"))} · Skill {JsonHelpers.ReadInt(agent, "skill_count")}",
                    $"{UiDisplayText.Domain(JsonHelpers.ReadString(agent, "domain"))} · {UiDisplayText.ShortTechnical(JsonHelpers.ReadString(agent, "workspace_path"), 42)}"));
            }
        }

        Artifacts.Clear();
        if (artifacts.TryGetProperty("recent", out var artifactList) && artifactList.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in artifactList.EnumerateArray())
            {
                JsonHelpers.TryReadObject(item, "skill_candidate", out var candidate);
                JsonHelpers.TryReadObject(item, "model_extraction", out var extraction);
                JsonHelpers.TryReadObject(extraction, "provider", out var extractionProvider);
                var extractionStatus = JsonHelpers.ReadString(extraction, "status", "skipped");
                var extractionModel = JsonHelpers.ReadString(extractionProvider, "model", "--");
                Artifacts.Add(new EventViewModel(
                    $"{UiDisplayText.ArtifactType(JsonHelpers.ReadString(item, "artifact_type"))} · {JsonHelpers.ReadString(item, "title")}",
                    $"{UiDisplayText.Domain(JsonHelpers.ReadString(item, "owner_agent_id"))} · {JsonHelpers.ReadString(candidate, "name")}{Environment.NewLine}模型抽取 {UiDisplayText.Status(extractionStatus)} · {UiDisplayText.ShortTechnical(extractionModel)}{Environment.NewLine}{UiDisplayText.ShortTechnical(JsonHelpers.ReadString(item, "source"), 56)}"));
            }
        }

        DomainSkills.Clear();
        if (domainTemplates.TryGetProperty("templates", out var templateList) && templateList.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in templateList.EnumerateArray())
            {
                DomainSkills.Add(new EventViewModel(
                    $"{(JsonHelpers.ReadSafeBool(item, "exists", false) ? "已存在" : "缺失")} · {JsonHelpers.ReadString(item, "label")}",
                    $"{UiDisplayText.Domain(JsonHelpers.ReadString(item, "owner_agent_id"))} · {JsonHelpers.ReadString(item, "skill_name")}"));
            }
        }

        var datasetCount = JsonHelpers.ReadInt(training, "dataset_count");
        JsonHelpers.TryReadObject(training, "recipe", out var recipe);
        _panels.EvolutionTrainingSummaryText.Text = $"训练集 {datasetCount} 条 · 配方 {JsonHelpers.ReadString(recipe, "method", "--")} · 基座 {JsonHelpers.ReadString(recipe, "base_model_hint", "--")}";
        _panels.EvolutionTrainingCommandBox.Text = JsonHelpers.ReadString(training, "training_command_text", "--");
        _panels.EvolutionTraceSummaryText.Text = $"轨迹日志: {JsonHelpers.ReadString(evolution, "trajectory_log_path", "--")}{Environment.NewLine}失败样本: {JsonHelpers.ReadSafeInt(failures, "total")} · 待处理 {JsonHelpers.ReadSafeInt(failures, "open")}{Environment.NewLine}失败日志: {JsonHelpers.ReadString(evolution, "failure_log_path", "--")}";
        if (string.IsNullOrWhiteSpace((_panels.EvolutionPaperOwnerBox.SelectedValue as string) ?? _panels.EvolutionPaperOwnerBox.Text))
        {
            _panels.EvolutionPaperOwnerBox.SelectedValue = "programming";
            _panels.EvolutionPaperOwnerBox.Text = "programming";
        }
        if (string.IsNullOrWhiteSpace((_panels.EvolutionVideoOwnerBox.SelectedValue as string) ?? _panels.EvolutionVideoOwnerBox.Text))
        {
            _panels.EvolutionVideoOwnerBox.SelectedValue = "video_animation";
            _panels.EvolutionVideoOwnerBox.Text = "video_animation";
        }

        JsonHelpers.TryReadObject(evolution, "review_gate", out var gate);
        _panels.EvolutionCoreReviewRequiredBox.IsChecked = JsonHelpers.ReadSafeBool(gate, "core_review_required", true);
        _panels.EvolutionAllowTrainingScheduleBox.IsChecked = JsonHelpers.ReadSafeBool(gate, "allow_training_schedule", false);
        _panels.EvolutionAutoPromoteSkillBox.IsChecked = JsonHelpers.ReadSafeBool(gate, "auto_promote_skill", false);
    }

    public static string BuildSummaryText(
        JsonElement evolution,
        JsonElement counts,
        JsonElement trajectory,
        JsonElement distribution,
        JsonElement artifacts,
        JsonElement domainTemplates,
        JsonElement jobs,
        JsonElement reviewAudit)
    {
        return string.Join(Environment.NewLine, new[]
        {
            $"状态 {UiDisplayText.Status(JsonHelpers.ReadString(evolution, "status", "--"))} · 轨迹 {JsonHelpers.ReadSafeInt(trajectory, "total")} · 成功率 {ReadPercent(trajectory, "success_rate")}",
            $"进化信号: 改进动作 {JsonHelpers.ReadSafeInt(counts, "improvement_actions")} · 评测 {JsonHelpers.ReadSafeInt(counts, "eval_cases")} · 自训样本 {JsonHelpers.ReadSafeInt(counts, "self_training_examples")} · 学习素材 {JsonHelpers.ReadSafeInt(artifacts, "artifact_count")}",
            $"Skill 隔离: Agent {JsonHelpers.ReadSafeInt(distribution, "agent_count")} · 未归属 {JsonHelpers.ReadSafeInt(distribution, "missing_owner_count")} · 任务失败 {JsonHelpers.ReadSafeInt(jobs, "failed_count")} · 审核拒绝 {JsonHelpers.ReadSafeInt(reviewAudit, "denied_count")}",
            $"模板: {JsonHelpers.ReadSafeInt(domainTemplates, "existing_count")} / {JsonHelpers.ReadSafeInt(domainTemplates, "count")} · 路径: {JsonHelpers.ReadString(evolution, "dataset_path", "--")}",
        });
    }

    public async Task ActionAsync(string action)
    {
        try
        {
            _panels.EvolutionSummaryText.Text = $"正在执行：{action}";
            object payload = action == "build_cloud_training_package"
                ? new { action, reviewer = "wpf_desktop", core_review_approved = true, review_reason = "Manual desktop approval for training package generation." }
                : new { action };
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/evolution", payload);
            EnsureOkResponse(doc.RootElement, $"进化动作失败：{action}");
            Render(doc.RootElement.GetProperty("evolution"));
            if (action == "enforce_skill_ownership" || action == "seed_domain_skill_templates")
            {
                await _loadSkillsAsync();
            }
            _panels.EvolutionSummaryText.Text = $"进化动作完成：{action}{Environment.NewLine}{_panels.EvolutionSummaryText.Text}";
        }
        catch (Exception ex)
        {
            _panels.EvolutionSummaryText.Text = $"进化动作失败：{ex.Message}";
        }
    }

    public void RenderSelectedGrowthCandidate()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        var hasCandidate = candidate is not null;
        _panels.GrowthPrepareBuilderButton.IsEnabled = hasCandidate && candidate!.CanPrepareBuilder;
        _panels.GrowthVerifyBuilderButton.IsEnabled = hasCandidate && candidate!.CanVerifyBuilder;
        _panels.GrowthPrepareSandboxBundleButton.IsEnabled = hasCandidate && candidate!.CanPrepareSandboxBundle;
        _panels.GrowthExecuteSandboxButton.IsEnabled = hasCandidate && candidate!.CanExecuteSandbox && _sandboxExecutionEnabled;
        _panels.GrowthRunBenchmarkButton.IsEnabled = hasCandidate && candidate!.CanRecordBenchmark;
        _panels.GrowthRunModelJuryButton.IsEnabled = hasCandidate && candidate!.CanRunModelJury;
        _panels.GrowthResearchButton.IsEnabled = hasCandidate && candidate!.CanResearch;
        _panels.GrowthAdvanceStageButton.IsEnabled = hasCandidate && candidate!.CanAdvance;
        _panels.GrowthApproveButton.IsEnabled = hasCandidate && candidate!.CanApprove;
        _panels.GrowthRejectButton.IsEnabled = hasCandidate && candidate!.CanReject;
        _panels.GrowthRegisterButton.IsEnabled = hasCandidate && candidate!.CanRegister;
        _panels.GrowthEscalationTargetBox.Items.Clear();
        if (candidate is not null)
        {
            foreach (var target in candidate.EscalationTargets)
            {
                _panels.GrowthEscalationTargetBox.Items.Add(new ComboBoxItem
                {
                    Content = GrowthKindLabel(target),
                    Tag = target,
                });
            }
        }
        _panels.GrowthEscalationTargetBox.SelectedIndex = _panels.GrowthEscalationTargetBox.Items.Count > 0 ? 0 : -1;
        _panels.GrowthEscalationTargetBox.IsEnabled = hasCandidate && candidate!.CanEscalate;
        _panels.GrowthEscalateButton.IsEnabled = hasCandidate && candidate!.CanEscalate;
        _panels.GrowthAdvanceStageButton.Content = candidate?.CanAdvance == true ? $"提交 {candidate.NextStage}" : "提交下一阶段";
        _panels.GrowthGovernanceStatusText.Text = candidate is null
            ? "选择候选后提交证据、审核或登记；登记不会自动激活。"
            : $"{candidate.CandidateId}{Environment.NewLine}{candidate.Meta}{Environment.NewLine}{candidate.ResearchSummary}{Environment.NewLine}{candidate.BuilderSummary}{Environment.NewLine}{candidate.SandboxSummary}{Environment.NewLine}{candidate.BenchmarkSummary}";
    }

    public async Task PrepareBuilderArtifactAsync()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        if (candidate is null)
        {
            _panels.GrowthGovernanceStatusText.Text = "请先选择一个 Growth 候选。";
            return;
        }

        try
        {
            _panels.GrowthPrepareBuilderButton.IsEnabled = false;
            _panels.GrowthGovernanceStatusText.Text = "正在盘点本地 Tool、MCP、Model 与 Worker...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", new
            {
                action = "prepare_builder_artifact",
                candidate_id = candidate.CandidateId,
                workspace_id = candidate.WorkspaceId,
                allow_unscoped_governance = true,
                actor = "wpf-desktop",
            });
            EnsureOkResponse(doc.RootElement, "Builder 工件准备失败");
            JsonHelpers.TryReadObject(doc.RootElement, "builder_artifact", out var artifact);
            JsonHelpers.TryReadObject(artifact, "research", out var research);
            JsonHelpers.TryReadObject(artifact, "verification_plan", out var verification);
            JsonHelpers.TryReadObject(artifact, "registry_plan", out var registry);
            JsonHelpers.TryReadObject(artifact, "human_escalation", out var human);
            var summary =
                $"Builder 工件已准备 · 匹配 {JsonHelpers.ReadSafeInt(research, "inventory_match_count")} · " +
                $"验证 {JsonHelpers.ReadString(verification, "execution_status", "not_run")} · " +
                $"目标 {JsonHelpers.ReadString(registry, "target", "--")}" +
                (JsonHelpers.ReadSafeBool(human, "required") ? " · 需人工补源" : "");
            await LoadGrowthAsync();
            _panels.GrowthGovernanceStatusText.Text = summary;
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"Builder 工件准备失败：{ex.Message}";
            RenderSelectedGrowthCandidate();
        }
    }

    public async Task ResearchGrowthCandidateAsync()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        if (candidate is null || !candidate.CanResearch)
        {
            _panels.GrowthGovernanceStatusText.Text = "请选择尚未进入审核的 Growth 候选。";
            return;
        }
        if (_panels.GrowthConfirmBox.IsChecked != true)
        {
            _panels.GrowthGovernanceStatusText.Text = "请确认只读取公开仓库元数据，不会克隆、安装、执行或激活能力。";
            return;
        }

        try
        {
            _panels.GrowthResearchButton.IsEnabled = false;
            _panels.GrowthGovernanceStatusText.Text = "正在检索公开 GitHub 仓库元数据...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", new
            {
                action = "research_candidate",
                candidate_id = candidate.CandidateId,
                workspace_id = candidate.WorkspaceId,
                allow_unscoped_governance = true,
                confirmed = true,
                actor = "wpf-desktop",
                researched_by = "wpf-desktop",
                keywords = _panels.GrowthResearchKeywordsBox.Text.Trim(),
            });
            EnsureOkResponse(doc.RootElement, "公开仓库研究失败");
            JsonHelpers.TryReadObject(doc.RootElement, "research_report", out var report);
            await LoadGrowthAsync();
            _panels.GrowthResearchKeywordsBox.Clear();
            _panels.GrowthConfirmBox.IsChecked = false;
            _panels.GrowthGovernanceStatusText.Text =
                $"公开仓库研究完成：{JsonHelpers.ReadSafeInt(report, "result_count")} 条候选来源 · " +
                "未克隆、未安装、未执行、未推进阶段。";
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"公开仓库研究失败：{ex.Message}";
            RenderSelectedGrowthCandidate();
        }
    }

    public async Task VerifyBuilderArtifactAsync()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        if (candidate is null)
        {
            _panels.GrowthGovernanceStatusText.Text = "请先选择一个 Growth 候选。";
            return;
        }
        if (!candidate.CanVerifyBuilder)
        {
            _panels.GrowthGovernanceStatusText.Text = "当前阶段或候选类型不可运行 Builder 沙箱预检。";
            return;
        }
        if (_panels.GrowthConfirmBox.IsChecked != true)
        {
            _panels.GrowthGovernanceStatusText.Text = "请确认预检只生成报告，不会联网、安装、执行、推进或激活能力。";
            return;
        }

        try
        {
            _panels.GrowthVerifyBuilderButton.IsEnabled = false;
            _panels.GrowthGovernanceStatusText.Text = "正在生成 Builder 静态沙箱预检报告...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", new
            {
                action = "verify_builder_artifact",
                candidate_id = candidate.CandidateId,
                workspace_id = candidate.WorkspaceId,
                allow_unscoped_governance = true,
                confirmed = true,
                actor = "wpf-desktop",
                verified_by = "wpf-desktop",
            });
            EnsureOkResponse(doc.RootElement, "Builder 沙箱预检失败");
            JsonHelpers.TryReadObject(doc.RootElement, "verification_report", out var report);
            var status = JsonHelpers.ReadString(report, "status", "unknown");
            JsonHelpers.TryReadObject(report, "summary", out var summary);
            await LoadGrowthAsync();
            _panels.GrowthConfirmBox.IsChecked = false;
            _panels.GrowthGovernanceStatusText.Text =
                $"Builder 沙箱预检：{status} · 通过 {JsonHelpers.ReadSafeInt(summary, "passed")} · " +
                $"需人工 {JsonHelpers.ReadSafeInt(summary, "needs_human")} · 未推进阶段、未激活。";
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"Builder 沙箱预检失败：{ex.Message}";
            RenderSelectedGrowthCandidate();
        }
    }

    public async Task PrepareSandboxBundleAsync()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        if (candidate is null || !candidate.CanPrepareSandboxBundle)
        {
            _panels.GrowthGovernanceStatusText.Text = "请选择已准备 Builder、且处于 research/design/sandbox 阶段的 Skill、Tool 或 Code 候选。";
            return;
        }
        if (_panels.GrowthConfirmBox.IsChecked != true)
        {
            _panels.GrowthGovernanceStatusText.Text = "请确认 Bundle 只写入受管沙箱目录，不会在主机执行或自动激活。";
            return;
        }
        var source = _panels.GrowthSandboxBundleBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(source))
        {
            _panels.GrowthGovernanceStatusText.Text = "请填写包含 files 与 command 的 Sandbox Bundle JSON。";
            return;
        }

        try
        {
            using var specification = JsonDocument.Parse(source);
            if (specification.RootElement.ValueKind != JsonValueKind.Object)
            {
                throw new JsonException("Sandbox Bundle 必须是 JSON 对象");
            }
            _panels.GrowthPrepareSandboxBundleButton.IsEnabled = false;
            _panels.GrowthGovernanceStatusText.Text = "正在校验并写入不可变 Sandbox Bundle...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", new
            {
                action = "prepare_sandbox_bundle",
                candidate_id = candidate.CandidateId,
                workspace_id = candidate.WorkspaceId,
                allow_unscoped_governance = true,
                confirmed = true,
                actor = "wpf-desktop",
                sandbox_bundle = specification.RootElement.Clone(),
            });
            EnsureOkResponse(doc.RootElement, "Sandbox Bundle 准备失败");
            JsonHelpers.TryReadObject(doc.RootElement, "sandbox_bundle", out var bundle);
            await LoadGrowthAsync();
            _panels.GrowthConfirmBox.IsChecked = false;
            _panels.GrowthGovernanceStatusText.Text =
                $"Sandbox Bundle 已准备：{JsonHelpers.ReadString(bundle, "bundle_id")} · " +
                $"{JsonHelpers.ReadSafeInt(bundle, "file_count")} 个文件。请重新运行静态预检。";
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"Sandbox Bundle 准备失败：{ex.Message}";
            RenderSelectedGrowthCandidate();
        }
    }

    public async Task ExecuteBuilderSandboxAsync()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        if (candidate is null || !candidate.CanExecuteSandbox || !_sandboxExecutionEnabled)
        {
            _panels.GrowthGovernanceStatusText.Text = "需要已通过静态预检的 Bundle 与可用的隔离执行器。";
            return;
        }
        if (_panels.GrowthConfirmBox.IsChecked != true)
        {
            _panels.GrowthGovernanceStatusText.Text = "请确认将在无网络、无主机挂载、只读且受限的容器中运行候选代码。";
            return;
        }

        try
        {
            _panels.GrowthExecuteSandboxButton.IsEnabled = false;
            _panels.GrowthGovernanceStatusText.Text = "正在运行隔离候选测试...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", new
            {
                action = "execute_builder_sandbox",
                candidate_id = candidate.CandidateId,
                workspace_id = candidate.WorkspaceId,
                allow_unscoped_governance = true,
                confirmed = true,
                execution_ack = "run_untrusted_code_in_isolated_container",
                actor = "wpf-desktop",
            });
            EnsureOkResponse(doc.RootElement, "隔离候选测试失败");
            JsonHelpers.TryReadObject(doc.RootElement, "sandbox_execution", out var report);
            await LoadGrowthAsync();
            _panels.GrowthConfirmBox.IsChecked = false;
            _panels.GrowthGovernanceStatusText.Text =
                $"隔离测试 {JsonHelpers.ReadString(report, "status", "unknown")} · " +
                $"exit {JsonHelpers.ReadSafeInt(report, "exit_code")} · " +
                $"{JsonHelpers.ReadSafeInt(report, "duration_ms")} ms · 未推进阶段、未激活。";
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"隔离候选测试失败：{ex.Message}";
            RenderSelectedGrowthCandidate();
        }
    }

    public async Task RecordCandidateBenchmarkAsync()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        if (candidate is null || !candidate.CanRecordBenchmark)
        {
            _panels.GrowthGovernanceStatusText.Text = "请选择处于 benchmark 阶段的活动候选。";
            return;
        }
        if (_panels.GrowthConfirmBox.IsChecked != true)
        {
            _panels.GrowthGovernanceStatusText.Text = "请确认 Benchmark 只生成评测证据，不会推进、登记或激活能力。";
            return;
        }
        var source = _panels.GrowthBenchmarkJsonBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(source))
        {
            _panels.GrowthGovernanceStatusText.Text = "请填写包含版本、数据集、Before 与 After 的 Benchmark JSON。";
            return;
        }

        try
        {
            using var benchmark = JsonDocument.Parse(source);
            if (benchmark.RootElement.ValueKind != JsonValueKind.Object)
            {
                throw new JsonException("Benchmark 必须是 JSON 对象");
            }
            _panels.GrowthRunBenchmarkButton.IsEnabled = false;
            _panels.GrowthGovernanceStatusText.Text = "正在校验测量并计算 Promotion Gate...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", new
            {
                action = "record_candidate_benchmark",
                candidate_id = candidate.CandidateId,
                workspace_id = candidate.WorkspaceId,
                allow_unscoped_governance = true,
                confirmed = true,
                actor = "wpf-desktop",
                recorded_by = "wpf-desktop",
                benchmark = benchmark.RootElement.Clone(),
            });
            EnsureOkResponse(doc.RootElement, "Benchmark 记录失败");
            JsonHelpers.TryReadObject(doc.RootElement, "benchmark_report", out var report);
            JsonHelpers.TryReadObject(report, "promotion_gate", out var gate);
            JsonHelpers.TryReadObject(report, "delta", out var delta);
            await LoadGrowthAsync();
            _panels.GrowthConfirmBox.IsChecked = false;
            _panels.GrowthGovernanceStatusText.Text =
                $"Benchmark {JsonHelpers.ReadString(gate, "status", "failed")} · " +
                $"总分 {JsonHelpers.ReadDouble(report, "overall_score"):F1} · " +
                $"Δ {JsonHelpers.ReadDouble(delta, "overall_score"):+0.0;-0.0;0.0} · 未推进阶段、未激活。";
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"Benchmark 记录失败：{ex.Message}";
            RenderSelectedGrowthCandidate();
        }
    }

    public async Task RunModelJuryAsync()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        if (candidate is null || !candidate.CanRunModelJury)
        {
            _panels.GrowthGovernanceStatusText.Text = "请选择已记录 Benchmark 的 Model 候选。";
            return;
        }
        if (_panels.GrowthConfirmBox.IsChecked != true)
        {
            _panels.GrowthGovernanceStatusText.Text = "请确认将受管 Benchmark 摘要发送给已配置的 Review Committee。";
            return;
        }
        try
        {
            _panels.GrowthRunModelJuryButton.IsEnabled = false;
            _panels.GrowthGovernanceStatusText.Text = "正在请求 Model Jury...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", new
            {
                action = "run_model_jury",
                candidate_id = candidate.CandidateId,
                workspace_id = candidate.WorkspaceId,
                allow_unscoped_governance = true,
                confirmed = true,
                actor = "wpf-desktop",
                requested_by = "wpf-desktop",
            });
            EnsureOkResponse(doc.RootElement, "Model Jury 请求失败");
            JsonHelpers.TryReadObject(doc.RootElement, "model_jury", out var jury);
            await LoadGrowthAsync();
            _panels.GrowthConfirmBox.IsChecked = false;
            _panels.GrowthGovernanceStatusText.Text =
                $"Model Jury {JsonHelpers.ReadString(jury, "status", "insufficient_evidence")} · " +
                $"{JsonHelpers.ReadSafeInt(jury, "structured_review_count")} 份结构化评审 · 未推进阶段、未激活。";
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"Model Jury 请求失败：{ex.Message}";
            RenderSelectedGrowthCandidate();
        }
    }

    public async Task GrowthActionAsync(string action, string decision = "")
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        var evidenceText = _panels.GrowthEvidenceBox.Text.Trim();
        if (candidate is null)
        {
            _panels.GrowthGovernanceStatusText.Text = "请先选择一个 Growth 候选。";
            return;
        }
        if (evidenceText.Length < 2)
        {
            _panels.GrowthGovernanceStatusText.Text = "请填写可追溯的测试、评测证据或审核理由。";
            return;
        }
        if (_panels.GrowthConfirmBox.IsChecked != true)
        {
            _panels.GrowthGovernanceStatusText.Text = "请确认本次治理动作不会自动激活能力。";
            return;
        }

        var payload = new Dictionary<string, object?>
        {
            ["action"] = action,
            ["candidate_id"] = candidate.CandidateId,
            ["workspace_id"] = candidate.WorkspaceId,
            ["allow_unscoped_governance"] = true,
            ["confirmed"] = true,
            ["actor"] = "wpf-desktop",
            ["evidence"] = new { summary = evidenceText, source = "wpf_desktop" },
        };
        if (action == "advance_stage") payload["stage"] = candidate.NextStage;
        if (action == "review_candidate")
        {
            payload["decision"] = decision;
            payload["reviewer"] = "wpf-desktop";
            payload["reason"] = evidenceText;
        }
        if (action == "register_candidate") payload["registered_by"] = "wpf-desktop";
        if (action == "register_candidate") payload["registry_evidence"] = new { summary = evidenceText, source = "wpf_desktop" };

        try
        {
            _panels.GrowthGovernanceStatusText.Text = "正在提交 Growth 治理动作...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", payload);
            EnsureOkResponse(doc.RootElement, "Growth 治理动作失败");
            await LoadGrowthAsync();
            _panels.GrowthEvidenceBox.Clear();
            _panels.GrowthConfirmBox.IsChecked = false;
            _panels.GrowthGovernanceStatusText.Text = action == "register_candidate"
                ? "候选已登记到 Registry，仍需独立激活。"
                : "Growth 治理状态已更新。";
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"Growth 治理动作失败：{ex.Message}";
        }
    }

    public async Task EscalateGrowthCandidateAsync()
    {
        var candidate = _panels.GrowthRuntimeCandidatesList.SelectedItem as GrowthCandidateViewModel;
        var target = (_panels.GrowthEscalationTargetBox.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "";
        var evidenceText = _panels.GrowthEvidenceBox.Text.Trim();
        if (candidate is null || !candidate.CanEscalate || string.IsNullOrWhiteSpace(target))
        {
            _panels.GrowthGovernanceStatusText.Text = "请选择可升级的 Growth 候选和下一类 Builder。";
            return;
        }
        if (evidenceText.Length < 2 || _panels.GrowthConfirmBox.IsChecked != true)
        {
            _panels.GrowthGovernanceStatusText.Text = "请填写升级理由，并确认只创建候选、不会自动激活能力。";
            return;
        }

        try
        {
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/growth", new
            {
                action = "escalate_candidate",
                candidate_id = candidate.CandidateId,
                workspace_id = candidate.WorkspaceId,
                allow_unscoped_governance = true,
                target_kind = target,
                reason = evidenceText,
                requested_by = "wpf-desktop",
                actor = "wpf-desktop",
                confirmed = true,
                evidence = new { summary = evidenceText, source = "wpf_desktop" },
            });
            EnsureOkResponse(doc.RootElement, "Growth 升级失败");
            await LoadGrowthAsync();
            _panels.GrowthEvidenceBox.Clear();
            _panels.GrowthConfirmBox.IsChecked = false;
            _panels.GrowthGovernanceStatusText.Text = target == "human"
                ? "候选已转人工处理，能力仍未激活。"
                : $"已创建 {GrowthKindLabel(target)} 子候选，父候选已冻结。";
        }
        catch (Exception ex)
        {
            _panels.GrowthGovernanceStatusText.Text = $"Growth 升级失败：{ex.Message}";
        }
    }

    private static string GrowthKindLabel(string kind) => kind switch
    {
        "workflow" => "Workflow Builder",
        "skill" => "Skill Builder",
        "tool" => "Tool Builder",
        "code" => "Code Builder",
        "model" => "Model Builder",
        "human" => "转人工处理",
        _ => kind,
    };

    public async Task IngestArtifactAsync(string artifactType)
    {
        var isPaper = string.Equals(artifactType, "paper", StringComparison.OrdinalIgnoreCase);
        var titleBox = isPaper ? _panels.EvolutionPaperTitleBox : _panels.EvolutionVideoTitleBox;
        var sourceBox = isPaper ? _panels.EvolutionPaperSourceBox : _panels.EvolutionVideoSourceBox;
        var bodyBox = isPaper ? _panels.EvolutionPaperSummaryBox : _panels.EvolutionVideoActionsBox;
        var ownerBox = isPaper ? _panels.EvolutionPaperOwnerBox : _panels.EvolutionVideoOwnerBox;
        var title = titleBox.Text.Trim();
        var body = bodyBox.Text.Trim();
        var frames = isPaper ? Array.Empty<string>() : LinesFromText(_panels.EvolutionVideoFramesBox.Text);
        if (string.IsNullOrWhiteSpace(title) || (string.IsNullOrWhiteSpace(body) && frames.Length == 0))
        {
            _panels.EvolutionSummaryText.Text = isPaper ? "论文标题和方法摘要不能为空。" : "视频标题，以及截图/帧或操作序列不能为空。";
            return;
        }
        var action = isPaper ? "ingest_paper" : "ingest_video";
        var owner = ((ownerBox.SelectedValue as string) ?? ownerBox.Text).Trim();
        object payload = isPaper
            ? new
            {
                action,
                title,
                source = sourceBox.Text.Trim(),
                owner_agent_id = string.IsNullOrWhiteSpace(owner) ? "programming" : owner,
                summary = body,
            }
            : new
            {
                action,
                title,
                source = sourceBox.Text.Trim(),
                owner_agent_id = string.IsNullOrWhiteSpace(owner) ? "video_animation" : owner,
                summary = string.IsNullOrWhiteSpace(body) ? "" : $"从视频操作序列提取 {LinesFromText(body).Length} 个动作。",
                operation_sequence = LinesFromText(body),
                frames,
            };
        try
        {
            _panels.EvolutionSummaryText.Text = isPaper ? "正在生成论文 Skill 候选..." : "正在生成视频 Skill 候选...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/evolution", payload);
            Render(doc.RootElement.GetProperty("evolution"));
            await _loadSkillsAsync();
            _panels.EvolutionSummaryText.Text = $"{(isPaper ? "论文" : "视频")}学习 Artifact 已保存，并生成待审核 Skill 候选。{Environment.NewLine}{_panels.EvolutionSummaryText.Text}";
        }
        catch (Exception ex)
        {
            _panels.EvolutionSummaryText.Text = $"{(isPaper ? "论文" : "视频")}学习失败：{ex.Message}";
        }
    }

    public async Task SaveReviewGateAsync()
    {
        try
        {
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/evolution", new
            {
                action = "save_review_gate",
                review_gate = new
                {
                    core_review_required = _panels.EvolutionCoreReviewRequiredBox.IsChecked == true,
                    allow_training_schedule = _panels.EvolutionAllowTrainingScheduleBox.IsChecked == true,
                    auto_promote_skill = _panels.EvolutionAutoPromoteSkillBox.IsChecked == true,
                },
            });
            Render(doc.RootElement.GetProperty("evolution"));
            _panels.EvolutionSummaryText.Text = $"审核门已保存。{Environment.NewLine}{_panels.EvolutionSummaryText.Text}";
        }
        catch (Exception ex)
        {
            _panels.EvolutionSummaryText.Text = $"审核门保存失败：{ex.Message}";
        }
    }

    private static string ReadPercent(JsonElement element, string key)
    {
        var raw = JsonHelpers.ReadString(element, key);
        return double.TryParse(raw, out var value) ? value.ToString("P0") : "--";
    }

    private static string[] LinesFromText(string value)
    {
        return value.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    }

    private static void EnsureOkResponse(JsonElement root, string actionLabel) => JsonResponseHelpers.EnsureOkResponse(root, actionLabel);

    internal static class JsonHelpers
    {
        public static string ReadString(JsonElement element, string key)
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

        public static string ReadString(JsonElement element, string key, string fallback)
        {
            var value = ReadString(element, key);
            return string.IsNullOrWhiteSpace(value) ? fallback : value;
        }

        public static int ReadInt(JsonElement element, string key)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return 0;
            }
            if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
            {
                return number;
            }
            return int.TryParse(ReadString(element, key), out var parsed) ? parsed : 0;
        }

        public static bool ReadSafeBool(JsonElement element, string key, bool fallback = false)
        {
            if (element.ValueKind != JsonValueKind.Object)
            {
                return fallback;
            }
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

        public static int ReadSafeInt(JsonElement element, string key, int fallback = 0)
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out _))
            {
                return fallback;
            }
            return ReadInt(element, key);
        }

        public static double ReadDouble(JsonElement element, string key, double fallback = 0)
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
            {
                return fallback;
            }
            if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
            {
                return number;
            }
            return double.TryParse(ReadString(element, key), out var parsed) ? parsed : fallback;
        }

        public static bool TryReadObject(JsonElement element, string key, out JsonElement value)
        {
            value = default;
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var candidate) || candidate.ValueKind != JsonValueKind.Object)
            {
                return false;
            }
            value = candidate;
            return true;
        }
    }
}
