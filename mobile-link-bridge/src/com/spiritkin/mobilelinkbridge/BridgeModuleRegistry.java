package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.content.pm.PackageManager;

final class BridgeModuleRegistry {
    private BridgeModuleRegistry() {
    }

    static String summaryText(Context context) {
        StringBuilder out = new StringBuilder();
        appendLine(out, "命令同步", "可用",
                "同步主控端为本机配置的工作流、Android 步骤和执行结果");
        appendLine(out, "商品图片", "可用",
                "相册分享上传、云端预览、删除、按工作流分享给目标 App");
        appendLine(out, "商品链接", "可用",
                "剪贴板发送或分享文本采集，云端可查看和删除");
        appendLine(out, "PDD 自动化", pddStatus(context),
                "负责打开 PDD、分享商品图、执行上架步骤");
        appendLine(out, "屏幕与调试", screenStatus(context),
                "截图上传和无障碍节点快照，用于验收和排查");
        appendLine(out, "组合工作流", "可用",
                "手机作为 Android 执行端，不在手机上编辑流程");
        return out.toString();
    }

    static String workerSummaryText(Context context) {
        StringBuilder out = new StringBuilder();
        appendLine(out, "worker", "android_control_worker",
                HeartbeatService.isServiceRunning() ? "后台同步运行" : "后台同步停止");
        appendLine(out, "command runtime", "ready",
                "device.status, app.launch, url.open, clipboard.write, workflow.android_step");
        appendLine(out, "automation", pddStatus(context),
                "需要无障碍连接后才能执行 UI 快照、坐标点击和 PDD 上架步骤");
        appendLine(out, "screen", screenStatus(context),
                "截图授权后可作为验收产物上传到主控");
        return out.toString();
    }

    static String modulesJson(Context context) {
        return "["
                + moduleJson("core.command_sync", "命令同步",
                        "ready",
                        "heartbeat,command.sync,command.result")
                + ","
                + moduleJson("core.artifacts", "工作素材",
                        "ready",
                        "artifact.upload,artifact.download,image.share_to_app,artifact.cache.cleanup,artifact.cache.status")
                + ","
                + moduleJson("pdd.automation", "PDD 自动化",
                        pddMachineStatus(context),
                        "pdd.launch,pdd.share_image,pdd.create_listing")
                + ","
                + moduleJson("android.ui_snapshot", "页面快照",
                        PddAutomationService.isActive() ? "ready" : "needs_accessibility",
                        "android.ui_snapshot,android.open_accessibility_settings,android.open_bridge")
                + ","
                + moduleJson("android.screenshot", "屏幕截图",
                        ScreenCaptureStore.isAuthorized() ? "ready" : "needs_permission",
                        "android.screenshot.request_permission,android.screenshot.capture,android.open_bridge")
                + ","
                + moduleJson("workflow.android_step", "手机工作流步骤",
                        "ready",
                        "workflow.android_step,workflow.command_result,workflow.android_step.status")
                + "]";
    }

    private static String pddStatus(Context context) {
        boolean installed = isPackageInstalled(context, "com.xunmeng.pinduoduo");
        if (!installed) {
            return "未安装 PDD";
        }
        if (PddAutomationService.isActive()) {
            return "可用";
        }
        if (PddAutomationService.isSystemEnabled(context)) {
            return "已授权，等待服务连接";
        }
        return "需要重新开启无障碍";
    }

    private static String screenStatus(Context context) {
        if (ScreenCaptureStore.isAuthorized() && PddAutomationService.isActive()) {
            return "可用";
        }
        if (!ScreenCaptureStore.isAuthorized() && !PddAutomationService.isActive()) {
            return "需要截图授权和重新开启无障碍";
        }
        if (!ScreenCaptureStore.isAuthorized()) {
            return "需要截图授权";
        }
        return "需要重新开启无障碍";
    }

    private static String pddMachineStatus(Context context) {
        boolean installed = isPackageInstalled(context, "com.xunmeng.pinduoduo");
        if (!installed) {
            return "missing_app";
        }
        if (PddAutomationService.isActive()) {
            return "ready";
        }
        if (PddAutomationService.isSystemEnabled(context)) {
            return "authorized_waiting_service";
        }
        return "needs_accessibility";
    }

    private static boolean isPackageInstalled(Context context, String packageName) {
        try {
            context.getPackageManager().getPackageInfo(packageName, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    private static void appendLine(StringBuilder out, String name, String status, String detail) {
        if (out.length() > 0) {
            out.append('\n');
        }
        out.append(name).append(" · ").append(status).append('\n').append(detail);
    }

    private static String moduleJson(String id, String name, String status, String capabilities) {
        return "{"
                + "\"id\":\"" + escape(id) + "\","
                + "\"name\":\"" + escape(name) + "\","
                + "\"status\":\"" + escape(status) + "\","
                + "\"capabilities\":" + stringArrayJson(capabilities)
                + "}";
    }

    private static String stringArrayJson(String csv) {
        String[] values = csv.split(",");
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < values.length; i++) {
            if (i > 0) {
                out.append(',');
            }
            out.append("\"").append(escape(values[i].trim())).append("\"");
        }
        out.append(']');
        return out.toString();
    }

    private static String escape(String value) {
        return String.valueOf(value == null ? "" : value).replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
