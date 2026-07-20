using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class GovernanceControllerTests
{
    [Theory]
    [InlineData("date", "2026-07-18T09:00:00+08:00", "3600", "0 9 * * *")]
    [InlineData("interval", "", "300", "0 9 * * *")]
    [InlineData("cron", "", "0", "0 9 * * *")]
    public void BuildScheduledIntentValuesCoversAllTriggerTypes(string trigger, string runAt, string interval, string cron)
    {
        var values = GovernanceController.BuildScheduledIntentValues(
            "  喝水   提醒 ",
            "reminder",
            trigger,
            "Asia/Shanghai",
            runAt,
            interval,
            cron,
            "打开提醒");

        Assert.Equal("喝水 提醒", values["text"]);
        Assert.Equal(trigger, values["trigger_type"]);
        Assert.Equal("Asia/Shanghai", values["timezone"]);
    }

    [Fact]
    public void BuildScheduledIntentValuesRejectsInvalidActiveTriggerField()
    {
        Assert.Throws<ArgumentException>(() => GovernanceController.BuildScheduledIntentValues(
            "提醒", "reminder", "interval", "Asia/Shanghai", "", "0", "0 9 * * *", ""));
        Assert.Throws<ArgumentException>(() => GovernanceController.BuildScheduledIntentValues(
            "提醒", "reminder", "cron", "Asia/Shanghai", "", "60", "bad cron", ""));
    }

    [Fact]
    public void GovernanceItemsParseOperatorFacingState()
    {
        using var toolDoc = JsonDocument.Parse(
            """
            {"tool_id":"python.run","enabled":false,"risk":"shell","confirmation_policy":"always","source":"registry","updated_at":1784290000}
            """);
        using var intentDoc = JsonDocument.Parse(
            """
            {"intent_id":"intent-1","text":"喝水","intent_type":"reminder","trigger_type":"interval","timezone":"Asia/Shanghai","interval_seconds":300,"status":"paused","next_run_time":"2026-07-18T09:00:00+08:00"}
            """);

        var tool = ToolAuthorizationItemViewModel.FromJson(toolDoc.RootElement);
        var intent = ScheduledIntentItemViewModel.FromJson(intentDoc.RootElement);

        Assert.Contains("已停用", tool.DisplayLabel);
        Assert.Contains("python.run", tool.DisplayLabel);
        Assert.Contains("每 300s", intent.DisplayLabel);
        Assert.Equal("paused", intent.Status);
    }
}
