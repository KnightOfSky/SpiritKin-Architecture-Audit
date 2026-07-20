using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class EvolutionControllerTests
{
    [Fact]
    public void BuildSummaryTextKeepsTrajectoryAndDatasetSignals()
    {
        using var evolutionDoc = JsonDocument.Parse("""{"status":"active","dataset_path":"state/evolution/dataset.jsonl"}""");
        using var countsDoc = JsonDocument.Parse("""{"improvement_actions":2,"eval_cases":3,"self_training_examples":4}""");
        using var trajectoryDoc = JsonDocument.Parse("""{"total":10,"success_rate":0.7}""");
        using var distributionDoc = JsonDocument.Parse("""{"agent_count":5,"missing_owner_count":1}""");
        using var artifactsDoc = JsonDocument.Parse("""{"artifact_count":6}""");
        using var templatesDoc = JsonDocument.Parse("""{"existing_count":7,"count":9}""");
        using var jobsDoc = JsonDocument.Parse("""{"failed_count":1}""");
        using var reviewDoc = JsonDocument.Parse("""{"denied_count":2}""");

        var summary = EvolutionController.BuildSummaryText(
            evolutionDoc.RootElement,
            countsDoc.RootElement,
            trajectoryDoc.RootElement,
            distributionDoc.RootElement,
            artifactsDoc.RootElement,
            templatesDoc.RootElement,
            jobsDoc.RootElement,
            reviewDoc.RootElement);

        Assert.Contains("状态 已启用", summary);
        Assert.Contains("轨迹 10", summary);
        Assert.Contains("成功率 70", summary);
        Assert.Contains("自训样本 4", summary);
        Assert.Contains("路径: state/evolution/dataset.jsonl", summary);
    }

    [Fact]
    public void GrowthCandidateOnlyEnablesLegalGovernanceActions()
    {
        var candidate = new GrowthCandidateViewModel(
            "growth-tool-test",
            "tool",
            "候选工具",
            "candidate",
            "candidate",
            "gap_analysis",
            "tenant-a",
            ["gap_analysis", "research", "sandbox", "dry_run", "benchmark", "review", "registry"]);

        Assert.True(candidate.CanAdvance);
        Assert.True(candidate.CanPrepareBuilder);
        Assert.False(candidate.CanVerifyBuilder);
        Assert.Equal("Builder 工件尚未准备", candidate.BuilderSummary);
        Assert.Equal("research", candidate.NextStage);
        Assert.False(candidate.CanApprove);
        Assert.False(candidate.CanRegister);
        Assert.True(candidate.CanEscalate);
        Assert.True(candidate.CanResearch);
        Assert.Contains("human", candidate.EscalationTargets);

        var reviewCandidate = candidate with { CurrentStage = "review" };
        Assert.False(reviewCandidate.CanAdvance);
        Assert.True(reviewCandidate.CanApprove);
        Assert.True(reviewCandidate.CanReject);

        var approvedCandidate = reviewCandidate with { Status = "approved", PromotionStatus = "approved" };
        Assert.False(approvedCandidate.CanApprove);
        Assert.True(approvedCandidate.CanRegister);

        var preparedCandidate = candidate with
        {
            BuilderPrepared = true,
            BuilderInventoryMatchCount = 3,
            BuilderVerificationStatus = "not_run",
            BuilderRegistryTarget = "tool_registry",
        };
        Assert.Contains("匹配 3", preparedCandidate.BuilderSummary);
        Assert.Contains("tool_registry", preparedCandidate.BuilderSummary);
        var sandboxCandidate = preparedCandidate with { CurrentStage = "sandbox" };
        Assert.True(sandboxCandidate.CanVerifyBuilder);
        Assert.True(sandboxCandidate.CanPrepareSandboxBundle);
        Assert.False(sandboxCandidate.CanExecuteSandbox);
        var executableCandidate = sandboxCandidate with
        {
            BuilderVerificationStatus = "passed",
            SandboxBundlePrepared = true,
            SandboxBundleId = "bundle-test",
            SandboxBundleFileCount = 2,
        };
        Assert.True(executableCandidate.CanExecuteSandbox);
        Assert.Contains("bundle-test", executableCandidate.SandboxSummary);
        Assert.Contains("2 个文件", executableCandidate.SandboxSummary);

        var benchmarkCandidate = executableCandidate with
        {
            CurrentStage = "benchmark",
            BenchmarkId = "benchmark-test",
            BenchmarkPromotionStatus = "passed",
            BenchmarkOverallScore = 91.4,
            BenchmarkOverallDelta = 6.2,
        };
        Assert.True(benchmarkCandidate.CanRecordBenchmark);
        Assert.True(benchmarkCandidate.CanAdvance);
        Assert.Contains("91.4", benchmarkCandidate.BenchmarkSummary);

        var blockedBenchmarkCandidate = benchmarkCandidate with { BenchmarkPromotionStatus = "failed" };
        Assert.False(blockedBenchmarkCandidate.CanAdvance);

        var modelBenchmarkCandidate = benchmarkCandidate with { Kind = "model" };
        Assert.True(modelBenchmarkCandidate.CanRunModelJury);

        var registeredCandidate = preparedCandidate with { Status = "registered" };
        Assert.False(registeredCandidate.CanPrepareBuilder);
        Assert.False(registeredCandidate.CanEscalate);
        Assert.False(registeredCandidate.CanResearch);

        var escalatedCandidate = candidate with
        {
            Status = "escalated",
            ResolutionStatus = "escalated",
            ResolutionTarget = "code",
            ChildCandidateId = "growth-code-child",
        };
        Assert.False(escalatedCandidate.CanAdvance);
        Assert.False(escalatedCandidate.CanPrepareBuilder);
        Assert.False(escalatedCandidate.CanEscalate);
        Assert.Contains("路由 code", escalatedCandidate.Meta);
        Assert.False(escalatedCandidate.CanResearch);

        var researchedCandidate = candidate with
        {
            RemoteResearchReportId = "research-test",
            RemoteResearchResultCount = 3,
            RemoteResearchQuery = "video beat sync",
        };
        Assert.Contains("3 条", researchedCandidate.ResearchSummary);
        Assert.Contains("video beat sync", researchedCandidate.ResearchSummary);
    }

    [Fact]
    public void RuntimeHostOnlyOffersOnlineNonOwnerExecutionHostsForMigration()
    {
        var owner = new RuntimeHostViewModel(
            "desktop-a",
            "Desktop A",
            "desktop",
            "online",
            "active",
            true,
            false,
            true,
            7);
        var cloud = new RuntimeHostViewModel(
            "cloud-a",
            "Cloud A",
            "cloud",
            "online",
            "standby",
            true,
            false,
            false,
            0);
        var offline = cloud with { HostId = "cloud-offline", Label = "Cloud Offline", Status = "offline" };
        var ios = new RuntimeHostViewModel(
            "ios:phone-a",
            "iPhone",
            "ios",
            "online",
            "not_reported",
            false,
            true,
            false,
            0);
        var checkpoint = new RuntimeCheckpointViewModel(
            "checkpoint-test",
            "run-test",
            "listing",
            3,
            "desktop-a",
            "active",
            "2026-07-19T00:00:00Z");

        Assert.False(owner.CanReceiveMigration);
        Assert.True(cloud.CanReceiveMigration);
        Assert.False(offline.CanReceiveMigration);
        Assert.False(ios.CanReceiveMigration);
        Assert.Contains("epoch 7", owner.Meta);
        Assert.Contains("#3", checkpoint.Type);
    }
}
