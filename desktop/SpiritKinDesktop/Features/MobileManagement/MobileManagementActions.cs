using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    internal async Task MobileManagementActionAsync(string action, object? extra = null)
    {
        try
        {
            var payload = extra is Dictionary<string, object?> dict
                ? new Dictionary<string, object?>(dict, StringComparer.OrdinalIgnoreCase)
                : new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase);
            payload["action"] = action;
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/mobile-management", payload);
            EnsureOkResponse(doc.RootElement, $"移动端动作失败：{action}");
            if (doc.RootElement.TryGetProperty("mobile_management", out var mobile))
            {
                RenderMobileManagement(mobile);
            }
            if (doc.RootElement.TryGetProperty("result", out var result))
            {
                if (string.Equals(action, "create_android_pairing", StringComparison.OrdinalIgnoreCase)
                    && result.TryGetProperty("pairing", out var pairing)
                    && pairing.ValueKind == JsonValueKind.Object)
                {
                    RenderAndroidPairingResult(pairing);
                }
                WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = $"{UiDisplayText.Status(ReadJsonString(result, "status", "ok"))} · {ReadJsonString(result, "message", action)}";
            }
            else
            {
                WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = $"移动端动作完成：{action}";
            }
            await LoadModuleManagementAsync();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = $"移动端动作失败：{ex.Message}";
        }
    }
    internal Dictionary<string, object?> BuildMobileAdbPayload()
    {
        return new Dictionary<string, object?>
        {
            ["device_ip"] = WorkbenchShell.ManagementPanels.MobileAdbDeviceIpBox.Text.Trim(),
            ["known_port"] = WorkbenchShell.ManagementPanels.MobileAdbKnownPortBox.Text.Trim(),
            ["build"] = true,
        };
    }
    internal Dictionary<string, object?> BuildAndroidPairingPayload()
    {
        var workspaceId = ComboText(WorkbenchShell.ManagementPanels.MobileWorkspaceBox).Trim();
        if (string.IsNullOrWhiteSpace(workspaceId))
        {
            workspaceId = "local-ecommerce";
        }
        return new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase)
        {
            ["workspace_id"] = workspaceId,
            ["ttl_minutes"] = 30,
        };
    }
    internal Dictionary<string, object?> BuildMobileCommandPayload()
    {
        var operation = ComboText(WorkbenchShell.ManagementPanels.MobileCommandOperationBox);
        if (string.IsNullOrWhiteSpace(operation))
        {
            operation = "app.launch";
        }
        var value = WorkbenchShell.ManagementPanels.MobileCommandValueBox.Text.Trim();
        var parameters = new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase)
        {
            ["actor"] = "wpf_desktop",
        };
        if (operation is "device.status" or "list_installed_apps")
        {
            parameters["query"] = string.IsNullOrWhiteSpace(value) ? operation : value;
        }
        else if (operation is "app.launch" or "app.close")
        {
            parameters["app_name"] = value;
        }
        else if (operation == "url.open")
        {
            parameters["url"] = value;
        }
        else if (operation is "android.screenshot.capture" or "screenshot.capture" or "android.screenshot.request_permission" or "android.ui_snapshot")
        {
            parameters["purpose"] = string.IsNullOrWhiteSpace(value) ? "desktop_requested_screenshot" : value;
        }
        else if (operation == "pdd.share_image")
        {
            parameters["artifact_id"] = value;
        }
        else if (operation == "pdd.create_listing")
        {
            parameters["artifact_id"] = value;
            parameters["draft_only"] = true;
        }
        else if (operation == "accessibility.tap")
        {
            parameters["target"] = value;
        }
        else
        {
            parameters["text"] = value;
        }
        return new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase)
        {
            ["workspace_id"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobileDeviceWorkflowWorkspaceBox.Text)
                ? ComboText(WorkbenchShell.ManagementPanels.MobileWorkspaceBox).Trim()
                : WorkbenchShell.ManagementPanels.MobileDeviceWorkflowWorkspaceBox.Text.Trim(),
            ["device_id"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobileCommandDeviceIdBox.Text) ? "android_device" : WorkbenchShell.ManagementPanels.MobileCommandDeviceIdBox.Text.Trim(),
            ["operation"] = operation,
            ["params"] = parameters,
        };
    }

    internal async Task ApproveAndroidApkReleaseAsync()
    {
        await MobileManagementActionAsync("approve_android_apk_release", new Dictionary<string, object?>
        {
            ["reviewer"] = "wpf_desktop",
            ["reason"] = "Desktop smoke passed; allow Android controlled update.",
        });
    }

    internal async Task StartAndroidLifecycleWorkflowAsync()
    {
        var workflowName = "android.command_lifecycle_acceptance.v1";
        WorkbenchShell.ManagementPanels.WorkflowNameBox.Text = workflowName;
        await _workflowController.WorkflowActionAsync("save_builtin_definition", new Dictionary<string, object?>
        {
            ["workflow_name"] = workflowName,
        });
        var inputs = new Dictionary<string, object?>
        {
            ["project_root"] = _rootDir,
            ["device_id"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobileCommandDeviceIdBox.Text) ? "android_device" : WorkbenchShell.ManagementPanels.MobileCommandDeviceIdBox.Text.Trim(),
            ["artifact_id"] = "manual_acceptance_artifact",
            ["artifact_label"] = "android_acceptance",
            ["caption"] = "wpf desktop acceptance",
            ["product_data_path"] = "state/ecommerce_tasks/productData.json",
            ["draft_only"] = true,
            ["confirmed_high_risk"] = false,
        };
        await _workflowController.WorkflowActionAsync("start_run", new Dictionary<string, object?>
        {
            ["workflow_name"] = workflowName,
            ["inputs"] = inputs,
        });
        WorkbenchShell.ManagementPanels.MobileManagementActionText.Text = "Android 验收工作流已启动；请到工作流面板继续查看节点状态。";
    }

    internal Dictionary<string, object?> BuildDeviceWorkflowPayload(bool enabled)
    {
        var workspaceId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobileDeviceWorkflowWorkspaceBox.Text)
            ? ComboText(WorkbenchShell.ManagementPanels.MobileWorkspaceBox).Trim()
            : WorkbenchShell.ManagementPanels.MobileDeviceWorkflowWorkspaceBox.Text.Trim();
        var deviceId = WorkbenchShell.ManagementPanels.MobileDeviceWorkflowDeviceBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(deviceId))
        {
            throw new InvalidOperationException("请先在“工作区与设备”里选择一台 Android 手机端。");
        }
        return new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase)
        {
            ["workspace_id"] = workspaceId,
            ["device_id"] = deviceId,
            ["workflow_id"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobileDeviceWorkflowIdBox.Text)
                ? "ecommerce.auto_listing.v1"
                : WorkbenchShell.ManagementPanels.MobileDeviceWorkflowIdBox.Text.Trim(),
            ["enabled"] = enabled,
            ["reason"] = enabled ? "" : "桌面主控手动暂停",
        };
    }

    internal Dictionary<string, object?> BuildDeviceWorkflowRepairPayload(string repairType)
    {
        var payload = BuildDeviceWorkflowPayload(enabled: true);
        payload.Remove("enabled");
        payload.Remove("reason");
        payload["repair_type"] = repairType;
        return payload;
    }

    internal Dictionary<string, object?> BuildClearMobileCommandPayload()
    {
        return new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase)
        {
            ["workspace_id"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.MobileDeviceWorkflowWorkspaceBox.Text)
                ? ComboText(WorkbenchShell.ManagementPanels.MobileWorkspaceBox).Trim()
                : WorkbenchShell.ManagementPanels.MobileDeviceWorkflowWorkspaceBox.Text.Trim(),
            ["device_id"] = WorkbenchShell.ManagementPanels.MobileCommandDeviceIdBox.Text.Trim(),
        };
    }
}
