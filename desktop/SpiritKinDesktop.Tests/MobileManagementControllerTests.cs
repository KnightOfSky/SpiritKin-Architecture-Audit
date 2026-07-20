using System.Collections.Generic;
using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class MobileManagementControllerTests
{
    [Theory]
    [InlineData("tailscale", "Tailscale")]
    [InlineData("private_lan", "局域网")]
    [InlineData("public_or_unknown", "公网/未知")]
    public void MobileNetworkScopeLabelMapsKnownScopes(string scope, string expected)
    {
        Assert.Equal(expected, MobileManagementController.MobileNetworkScopeLabel(scope));
    }

    [Fact]
    public void BuildAndroidWorkerTextSummarizesQueuePermissionsAndPromotionGate()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "status": "ready",
              "role": "controlled_execution_worker",
              "online_device_count": 1,
              "device_count": 2,
              "capability_count": 3,
              "queue": {
                "pending": 4,
                "inflight": 1,
                "status_counts": {
                  "queued": 4,
                  "running": 1
                }
              },
              "permissions": {
                "gap_count": 2
              },
              "lifecycle": {
                "can_receive_commands": true,
                "can_run_automation": false,
                "can_capture_screen": true
              },
              "update": {
                "apk_exists": true,
                "installed": false,
                "installed_version_name": "1.0",
                "release_version_name": "1.1"
              },
              "promotion_gate": {
                "status": "waiting_review",
                "serving_allowed": false,
                "required_actions": ["desktop_smoke"]
              },
              "capabilities": ["tap", "screenshot"]
            }
            """);

        var text = MobileManagementController.BuildAndroidWorkerText(doc.RootElement);

        Assert.Contains("controlled_execution_worker", text);
        Assert.Contains("pending 4", text);
        Assert.Contains("gaps 2", text);
        Assert.Contains("desktop_smoke", text);
        Assert.Contains("tap, screenshot", text);
    }

    [Fact]
    public void BuildMobileSecurityWarningLinesIncludesSeverityAndDetail()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "warnings": [
                { "severity": "high", "title": "Missing token", "detail": "configure SPIRITKIN token" }
              ]
            }
            """);

        var lines = MobileManagementController.BuildMobileSecurityWarningLines(doc.RootElement);

        Assert.Single(lines);
        Assert.Contains("Missing token", lines[0]);
        Assert.Contains("configure SPIRITKIN token", lines[0]);
    }

    [Fact]
    public void BuildAccountConsoleSummaryShowsQuotaUsage()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "accounts": {
                "items": [
                  {
                    "account_id": "acct-a",
                    "name": "Tenant A",
                    "usage_summary": {
                      "workspace_count": 1,
                      "max_workspaces": 2,
                      "worker_count": 1,
                      "max_workers": 3,
                      "scrapes_this_period": 8,
                      "max_scrapes_per_period": 10
                    }
                  }
                ]
              }
            }
            """);

        var text = MobileManagementController.BuildAccountConsoleSummary(doc.RootElement);

        Assert.Contains("Tenant A", text);
        Assert.Contains("工作区 1/2", text);
        Assert.Contains("Worker 1/3", text);
        Assert.Contains("抓取 8/10", text);
    }

    [Fact]
    public void AppendAccountConsoleItemsIncludesBlueprintGateWarning()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "accounts": {
                "items": [
                  {
                    "account_id": "acct-a",
                    "name": "Tenant A",
                    "status": "active",
                    "workspace_ids": ["tenant-a"],
                    "usage_summary": {
                      "workspace_count": 1,
                      "max_workspaces": 0,
                      "worker_count": 2,
                      "max_workers": 0,
                      "scrapes_this_period": 5,
                      "max_scrapes_per_period": 0
                    }
                  }
                ]
              }
            }
            """);
        var items = new List<EventViewModel>();

        MobileManagementController.AppendAccountConsoleItems(items, doc.RootElement);

        var item = Assert.Single(items);
        Assert.Equal("账户自助 · Tenant A", item.Type);
        Assert.Contains("tenant-a", item.Meta);
        Assert.Contains("工作区 1/不限", item.Meta);
        Assert.Contains("workflow.graph.*", item.Meta);
    }
}
