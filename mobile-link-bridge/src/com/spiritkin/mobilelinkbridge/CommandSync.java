package com.spiritkin.mobilelinkbridge;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import java.io.File;
import java.io.FileOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class CommandSync {
    interface Callback {
        void onDone(boolean ok, String message);
    }

    private static final Pattern COMMAND_PATTERN = Pattern.compile("\\{[^{}]*\\\"operation\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"[^{}]*(?:\\\"params\\\"\\s*:\\s*\\{([^{}]*)\\})?[^{}]*\\}");
    private static final Pattern STRING_FIELD_PATTERN = Pattern.compile("\\\"([^\\\"]+)\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    private static final int MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024;
    private static final int CONNECT_TIMEOUT_MS = 5000;
    private static final int HEARTBEAT_READ_TIMEOUT_MS = 30000;

    private CommandSync() {
    }

    private static final class CommandExecution {
        final boolean success;
        final String message;
        final String resultJson;

        CommandExecution(boolean success, String message) {
            this(success, message, "{}");
        }

        CommandExecution(boolean success, String message, String resultJson) {
            this.success = success;
            this.message = message == null ? "" : message;
            this.resultJson = resultJson == null || resultJson.trim().isEmpty() ? "{}" : resultJson;
        }
    }

    private static final class CommandExecutionBatch {
        int processed;
        int succeeded;
        String resultsJson = "[]";
    }

    static void sync(Context context, Callback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String response;
                    try {
                        response = postHeartbeat(context);
                    } catch (Exception first) {
                        if (!discoverReceiverBlocking(context)) {
                            throw first;
                        }
                        response = postHeartbeat(context);
                    }
                    CommandExecutionBatch batch = executePending(context, response);
                    if (batch.processed > 0) {
                        BridgeSettings.setPendingCommandResults(context, batch.resultsJson);
                        try {
                            String followupResponse = postHeartbeat(context);
                            CommandExecutionBatch followup = executePending(context, followupResponse);
                            if (followup.processed > 0) {
                                BridgeSettings.setPendingCommandResults(context, followup.resultsJson);
                                batch.processed += followup.processed;
                                batch.succeeded += followup.succeeded;
                            }
                        } catch (Exception resultError) {
                            BridgeSettings.appendEvent(context, "命令结果待下次回传: " + resultError.getClass().getSimpleName());
                        }
                    }
                    String message = batch.processed > 0
                            ? "已处理 " + batch.processed + " 条主控命令，成功 " + batch.succeeded + " 条"
                            : heartbeatSuccessMessage(context);
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, true, message);
                } catch (Exception e) {
                    String detail = e.getMessage() == null || e.getMessage().trim().isEmpty() ? e.getClass().getSimpleName() : e.getMessage();
                    String message = "heartbeat 失败: " + detail;
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, false, message);
                }
            }
        }).start();
    }

    private static boolean discoverReceiverBlocking(Context context) {
        final Object lock = new Object();
        final boolean[] result = new boolean[]{false};
        ReceiverDiscovery.findAndSave(context, new ReceiverDiscovery.Callback() {
            @Override
            public void onDone(boolean ok, String receiverUrl, String message) {
                synchronized (lock) {
                    result[0] = ok;
                    lock.notifyAll();
                }
            }
        });
        synchronized (lock) {
            try {
                lock.wait(9000L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        return result[0];
    }

    private static String heartbeatSuccessMessage(Context context) {
        if (!PddAutomationService.isSystemEnabled(context)) {
            return "主控已同步；重装或升级后需要重新开启无障碍，PDD 自动化暂不可用";
        }
        if (!PddAutomationService.isActive()) {
            return "主控已同步；无障碍已授权但服务未连接，请返回应用或重新开关无障碍";
        }
        return "主控已同步";
    }

    private static String postHeartbeat(Context context) throws Exception {
        String body = heartbeatBody(context);
        HttpURLConnection conn = (HttpURLConnection) new URL(BridgeSettings.getHeartbeatUrl(context)).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
        conn.setReadTimeout(HEARTBEAT_READ_TIMEOUT_MS);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        BridgeSettings.addAuthHeaders(conn, context);
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        conn.setRequestProperty("Content-Length", String.valueOf(bytes.length));
        OutputStream out = conn.getOutputStream();
        try {
            out.write(bytes);
        } finally {
            out.close();
        }
        int code = conn.getResponseCode();
        String response = readResponse(code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream());
        if (code < 200 || code >= 300) {
            if (code == 401 || code == 403) {
                int failures = BridgeSettings.incrementHeartbeatAuthFailure(context);
                if (failures >= 3) {
                    BridgeSettings.clearPairing(context, "主控连续拒绝当前绑定，已清除本机绑定信息。请重新请求配对码并绑定。");
                    throw new IOException("绑定已失效，请重新请求配对码并绑定");
                }
                throw new IOException("主控暂时拒绝绑定，已保留本机 token 并等待下次同步恢复");
            }
            throw new IOException("heartbeat HTTP " + code);
        }
        BridgeSettings.clearHeartbeatAuthFailure(context);
        BridgeSettings.clearPendingCommandResults(context);
        return response;
    }

    private static String heartbeatBody(Context context) {
        BatteryManager battery = (BatteryManager) context.getSystemService(Context.BATTERY_SERVICE);
        int batteryPct = battery == null ? -1 : battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        String deviceId = Build.MANUFACTURER + "-" + Build.MODEL;
        String foregroundPackage = PddAutomationService.foregroundPackage();
        if (foregroundPackage.trim().isEmpty()) {
            foregroundPackage = context.getPackageName();
        }
        String pendingResults = pendingResultsJson(context);
        String waitField = ("[]".equals(pendingResults) ? "\"wait_seconds\":20," : "\"wait_seconds\":0,");
        return "{"
                + "\"device_id\":\"" + escape(deviceId) + "\","
                + "\"workspace_id\":\"" + escape(BridgeSettings.getWorkspaceId(context)) + "\","
                + "\"token\":\"" + escape(BridgeSettings.getPairingToken(context)) + "\","
                + waitField
                + "\"device_state\":{"
                + "\"device_id\":\"" + escape(deviceId) + "\","
                + "\"model\":\"" + escape(Build.MODEL) + "\","
                + "\"manufacturer\":\"" + escape(Build.MANUFACTURER) + "\","
                + "\"android_version\":\"" + escape(Build.VERSION.RELEASE) + "\","
                + "\"bridge_version\":\"" + escape(appVersion(context)) + "\","
                + "\"worker_role\":\"android_control_worker\","
                + "\"worker_schema_version\":\"spiritkin.android_worker.v1\","
                + "\"heartbeat_service_running\":" + (HeartbeatService.isServiceRunning() ? "true" : "false") + ","
                + "\"battery_pct\":" + batteryPct + ","
                + "\"current_app\":\"" + escape(foregroundPackage) + "\","
                + "\"foreground_package\":\"" + escape(foregroundPackage) + "\","
                + "\"pdd_accessibility\":\"" + escape(PddAutomationService.status(context)) + "\","
                + "\"pdd_accessibility_granted\":" + (PddAutomationService.isSystemEnabled(context) ? "true" : "false") + ","
                + "\"pdd_accessibility_connected\":" + (PddAutomationService.isActive() ? "true" : "false") + ","
                + "\"screen_capture_authorized\":" + (ScreenCaptureStore.isAuthorized() ? "true" : "false") + ","
                + "\"automation_modules\":" + BridgeModuleRegistry.modulesJson(context)
                + "},"
                + "\"installed_apps\":" + installedAppsJson(context) + ","
                + "\"capabilities\":[\"heartbeat\",\"link.share\",\"artifact.upload\",\"artifact.download\",\"image.share_to_app\",\"artifact.cache.cleanup\",\"artifact.cache.status\",\"device.status\",\"list_installed_apps\",\"android.ui_snapshot\",\"android.screenshot.request_permission\",\"android.screenshot.capture\",\"screenshot.capture\",\"android.open_accessibility_settings\",\"android.open_bridge\",\"accessibility.tap\",\"workflow.android_step\",\"workflow.command_result\",\"pdd.launch\",\"pdd.share_image\",\"pdd.create_listing\",\"app.launch\",\"app.close\",\"url.open\",\"clipboard.write\"],"
                + "\"command_catalog\":" + commandCatalogJson() + ","
                + "\"command_results\":" + pendingResults
                + "}";
    }

    private static String commandCatalogJson() {
        return "["
                + commandCatalogItem("device.status", "low", "device.status", false, false, "")
                + "," + commandCatalogItem("list_installed_apps", "low", "list_installed_apps", false, false, "")
                + "," + commandCatalogItem("app.launch", "medium", "app.launch", false, false, "")
                + "," + commandCatalogItem("app.close", "medium", "app.close", false, false, "")
                + "," + commandCatalogItem("url.open", "medium", "url.open", false, false, "")
                + "," + commandCatalogItem("clipboard.write", "medium", "clipboard.write", false, false, "")
                + "," + commandCatalogItem("artifact.download", "low", "artifact.download", false, true, "")
                + "," + commandCatalogItem("image.share_to_app", "medium", "image.share_to_app", false, true, "")
                + "," + commandCatalogItem("artifact.cache.cleanup", "low", "artifact.cache.cleanup", false, false, "")
                + "," + commandCatalogItem("artifact.cache.status", "low", "artifact.cache.status", false, false, "")
                + "," + commandCatalogItem("android.ui_snapshot", "high", "android.ui_snapshot", true, false, "")
                + "," + commandCatalogItem("android.screenshot.request_permission", "medium", "android.screenshot.request_permission", false, false, "")
                + "," + commandCatalogItem("android.screenshot.capture", "high", "android.screenshot.capture", false, false, "")
                + "," + commandCatalogItem("screenshot.capture", "high", "android.screenshot.capture", false, false, "")
                + "," + commandCatalogItem("android.open_accessibility_settings", "low", "android.open_accessibility_settings", false, false, "")
                + "," + commandCatalogItem("android.open_bridge", "low", "android.open_bridge", false, false, "")
                + "," + commandCatalogItem("accessibility.tap", "high", "accessibility.tap", true, false, "")
                + "," + commandCatalogItem("workflow.android_step", "medium", "workflow.android_step", false, false, "")
                + "," + commandCatalogItem("workflow.android_step.status", "low", "workflow.android_step.status", false, false, "")
                + "," + commandCatalogItem("workflow.command_result", "low", "workflow.command_result", false, false, "")
                + "," + commandCatalogItem("pdd.launch", "medium", "pdd.launch", false, false, "com.xunmeng.pinduoduo")
                + "," + commandCatalogItem("pdd.share_image", "high", "pdd.share_image", false, true, "com.xunmeng.pinduoduo")
                + "," + commandCatalogItem("pdd.create_listing", "critical", "pdd.create_listing", true, false, "com.xunmeng.pinduoduo")
                + "]";
    }

    private static String commandCatalogItem(String operation, String risk, String capability, boolean requiresAccessibility, boolean requiresArtifact, String requiredPackage) {
        return "{"
                + "\"operation\":\"" + escape(operation) + "\","
                + "\"risk\":\"" + escape(risk) + "\","
                + "\"required_capabilities\":[\"" + escape(capability) + "\"],"
                + "\"requires_accessibility\":" + (requiresAccessibility ? "true" : "false") + ","
                + "\"requires_artifact\":" + (requiresArtifact ? "true" : "false") + ","
                + "\"required_packages\":" + (requiredPackage == null || requiredPackage.trim().isEmpty() ? "[]" : "[\"" + escape(requiredPackage) + "\"]")
                + "}";
    }

    private static String pendingResultsJson(Context context) {
        String results = BridgeSettings.getPendingCommandResults(context);
        return results == null || results.trim().isEmpty() ? "[]" : results;
    }

    private static String installedAppsJson(Context context) {
        PackageManager manager = context.getPackageManager();
        StringBuilder out = new StringBuilder("[");
        int count = 0;
        for (ApplicationInfo app : manager.getInstalledApplications(0)) {
            if ((app.flags & ApplicationInfo.FLAG_SYSTEM) != 0) {
                continue;
            }
            if (count > 0) {
                out.append(',');
            }
            out.append("{\"name\":\"").append(escape(String.valueOf(manager.getApplicationLabel(app)))).append("\",\"package\":\"").append(escape(app.packageName)).append("\"}");
            count++;
            if (count >= 80) {
                break;
            }
        }
        out.append(']');
        return out.toString();
    }

    private static String appVersion(Context context) {
        try {
            android.content.pm.PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
            return info.versionName + " (" + info.versionCode + ")";
        } catch (Exception e) {
            return "unknown";
        }
    }

    private static CommandExecutionBatch executePending(Context context, String response) {
        Matcher matcher = COMMAND_PATTERN.matcher(response == null ? "" : response);
        CommandExecutionBatch batch = new CommandExecutionBatch();
        StringBuilder results = new StringBuilder("[");
        while (matcher.find()) {
            String commandObject = matcher.group(0);
            String commandId = field(commandObject, "command_id");
            String operation = matcher.group(1);
            String params = matcher.group(2) == null ? "" : matcher.group(2);
            CommandExecution result = executeCommand(context, operation, params);
            if (batch.processed > 0) {
                results.append(',');
            }
            results.append("{\"command_id\":\"")
                    .append(escape(commandId))
                    .append("\",\"operation\":\"")
                    .append(escape(operation))
                    .append("\",\"status\":\"")
                    .append(result.success ? "completed" : "failed")
                    .append("\",\"success\":")
                    .append(result.success ? "true" : "false")
                    .append(",\"message\":\"")
                    .append(escape(result.message))
                    .append("\",\"result\":")
                    .append(result.resultJson)
                    .append("}");
            batch.processed++;
            if (result.success) {
                batch.succeeded++;
            }
        }
        results.append(']');
        batch.resultsJson = results.toString();
        return batch;
    }

    private static CommandExecution executeCommand(Context context, String operation, String params) {
        try {
            if ("device.status".equals(operation)) {
                return deviceStatus(context);
            }
            if ("list_installed_apps".equals(operation)) {
                return listInstalledApps(context);
            }
            if ("app.launch".equals(operation)) {
                String appName = field(params, "app_name");
                return launchApp(context, appName);
            }
            if ("url.open".equals(operation)) {
                String url = field(params, "url");
                if (url.isEmpty()) {
                    return new CommandExecution(false, "缺少 URL");
                }
                Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(intent);
                String message = "打开 URL: " + url;
                BridgeSettings.appendEvent(context, message);
                return new CommandExecution(true, message);
            }
            if ("clipboard.write".equals(operation)) {
                String text = field(params, "text");
                ClipboardManager manager = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
                if (manager == null) {
                    return new CommandExecution(false, "剪贴板服务不可用");
                }
                manager.setPrimaryClip(ClipData.newPlainText("SpiritKin", text));
                BridgeSettings.appendEvent(context, "已写入剪贴板");
                return new CommandExecution(true, "已写入剪贴板");
            }
            if ("artifact.download".equals(operation)) {
                return downloadArtifactCommand(context, params, false);
            }
            if ("image.share_to_app".equals(operation)) {
                return downloadArtifactCommand(context, params, true);
            }
            if ("artifact.cache.cleanup".equals(operation)) {
                int deleted = cleanupArtifactCache(context);
                String message = "已清理缓存图片: " + deleted + " 个";
                BridgeSettings.appendEvent(context, message);
                return new CommandExecution(true, message);
            }
            if ("artifact.cache.status".equals(operation)) {
                String message = artifactCacheStatus(context);
                BridgeSettings.appendEvent(context, message);
                return new CommandExecution(true, message);
            }
            if ("android.ui_snapshot".equals(operation)) {
                return uploadUiSnapshot(context);
            }
            if ("android.screenshot.request_permission".equals(operation)) {
                return requestScreenshotPermission(context);
            }
            if ("android.screenshot.capture".equals(operation) || "screenshot.capture".equals(operation)) {
                return captureScreenshot(context);
            }
            if ("android.open_accessibility_settings".equals(operation)) {
                return openAccessibilitySettings(context);
            }
            if ("android.open_bridge".equals(operation)) {
                return openBridge(context);
            }
            if ("accessibility.tap".equals(operation)) {
                return accessibilityTap(context, params);
            }
            if ("pdd.launch".equals(operation)) {
                return launchApp(context, "拼多多");
            }
            if ("pdd.share_image".equals(operation)) {
                return pddShareImage(context, params);
            }
            if ("pdd.create_listing".equals(operation)) {
                return pddCreateListingSkeleton(context, params);
            }
            if ("workflow.android_step".equals(operation)) {
                String targetOperation = field(params, "target_operation");
                if (targetOperation.isEmpty()) {
                    targetOperation = field(params, "android_operation");
                }
                if (targetOperation.isEmpty()) {
                    targetOperation = field(params, "operation");
                }
                if (targetOperation.isEmpty()) {
                    return new CommandExecution(false, "缺少 Android 工作流步骤目标操作");
                }
                if ("workflow.android_step".equals(targetOperation)) {
                    return new CommandExecution(false, "Android 工作流步骤不能递归调用自身");
                }
                CommandExecution result = executeCommand(context, targetOperation, params);
                return new CommandExecution(result.success, "Android 工作流步骤 " + targetOperation + ": " + result.message);
            }
            if ("workflow.android_step.status".equals(operation)) {
                String message = "Android 工作流步骤能力: " + BridgeModuleRegistry.summaryText(context).replace('\n', ' ');
                BridgeSettings.appendEvent(context, "已回传 Android 工作流步骤状态");
                return new CommandExecution(true, message);
            }
            if ("app.close".equals(operation)) {
                String message = "app.close 需要 ADB/设备管理员权限，已忽略";
                BridgeSettings.appendEvent(context, message);
                return new CommandExecution(false, message);
            }
            String message = "未知命令: " + operation;
            BridgeSettings.appendEvent(context, message);
            return new CommandExecution(false, message);
        } catch (Exception e) {
            String message = "命令执行失败: " + e.getClass().getSimpleName();
            BridgeSettings.appendEvent(context, message);
            return new CommandExecution(false, message);
        }
    }

    private static CommandExecution accessibilityTap(Context context, String params) {
        float x = floatField(params, "x", Float.NaN);
        float y = floatField(params, "y", Float.NaN);
        if (Float.isNaN(x) || Float.isNaN(y)) {
            String target = field(params, "target");
            float[] parsed = parsePoint(target);
            x = parsed[0];
            y = parsed[1];
        }
        if (Float.isNaN(x) || Float.isNaN(y)) {
            return new CommandExecution(false, "缺少点击坐标，请传 x/y 或 target=\"x,y\"");
        }
        String message = PddAutomationService.tap(x, y);
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(message.startsWith("已下发"), message, "{\"x\":" + x + ",\"y\":" + y + "}");
    }

    private static CommandExecution deviceStatus(Context context) {
        BatteryManager battery = (BatteryManager) context.getSystemService(Context.BATTERY_SERVICE);
        int batteryPct = battery == null ? -1 : battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        String foregroundPackage = PddAutomationService.foregroundPackage();
        if (foregroundPackage.trim().isEmpty()) {
            foregroundPackage = context.getPackageName();
        }
        String message = "手机状态: " + Build.MANUFACTURER + "-" + Build.MODEL + " · 电量 " + batteryPct + "% · 前台 " + foregroundPackage;
        String resultJson = "{"
                + "\"device_id\":\"" + escape(Build.MANUFACTURER + "-" + Build.MODEL) + "\","
                + "\"model\":\"" + escape(Build.MODEL) + "\","
                + "\"manufacturer\":\"" + escape(Build.MANUFACTURER) + "\","
                + "\"android_version\":\"" + escape(Build.VERSION.RELEASE) + "\","
                + "\"battery_pct\":" + batteryPct + ","
                + "\"foreground_package\":\"" + escape(foregroundPackage) + "\","
                + "\"pdd_accessibility\":\"" + escape(PddAutomationService.status(context)) + "\","
                + "\"screen_capture_authorized\":" + (ScreenCaptureStore.isAuthorized() ? "true" : "false")
                + "}";
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message, resultJson);
    }

    private static CommandExecution listInstalledApps(Context context) {
        String apps = installedAppsJson(context);
        int count = Math.max(0, apps.split("\\{\\\"name\\\"").length - 1);
        String message = "已读取应用列表: " + count + " 个";
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message, "{\"apps\":" + apps + ",\"count\":" + count + "}");
    }

    private static CommandExecution openAccessibilitySettings(Context context) {
        Intent intent = new Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
        String message = "已打开 Android 无障碍设置";
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message);
    }

    private static CommandExecution requestScreenshotPermission(Context context) {
        if (ScreenCaptureStore.isAuthorized()) {
            String ready = "屏幕截图会话已就绪，可直接上传截图";
            BridgeSettings.appendEvent(context, ready);
            return new CommandExecution(true, ready, "{\"authorized\":true,\"session_active\":true}");
        }
        ScreenCaptureStore.requestCapture(context);
        String message = "已打开屏幕截图授权";
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message, "{\"authorized\":false,\"session_active\":false,\"pending_user_consent\":true}");
    }

    private static CommandExecution captureScreenshot(Context context) {
        if (!ScreenCaptureStore.isAuthorized()) {
            String message = "屏幕截图会话未授权或已失效，请先执行 android.screenshot.request_permission 并在手机上选择整个屏幕";
            BridgeSettings.appendEvent(context, message);
            return new CommandExecution(false, message, "{\"authorized\":false,\"session_active\":false}");
        }
        ScreenCaptureStore.captureScreenshot(context);
        String message = "已启动屏幕截图上传";
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message, "{\"authorized\":true,\"session_active\":true}");
    }

    private static CommandExecution openBridge(Context context) {
        Intent intent = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        if (intent == null) {
            return new CommandExecution(false, "无法打开手机端");
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
        String message = "已打开手机端";
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message);
    }

    private static CommandExecution launchApp(Context context, String appName) {
        if (appName == null || appName.trim().isEmpty()) {
            return new CommandExecution(false, "缺少应用名");
        }
        PackageManager manager = context.getPackageManager();
        String targetPackage = "";
        for (ApplicationInfo app : manager.getInstalledApplications(0)) {
            String label = String.valueOf(manager.getApplicationLabel(app));
            if (app.packageName.equals(appName) || label.equalsIgnoreCase(appName) || label.toLowerCase().contains(appName.toLowerCase())) {
                targetPackage = app.packageName;
                break;
            }
        }
        if (targetPackage.isEmpty()) {
            String message = "未找到应用: " + appName;
            BridgeSettings.appendEvent(context, message);
            return new CommandExecution(false, message);
        }
        Intent intent = manager.getLaunchIntentForPackage(targetPackage);
        if (intent == null) {
            String message = "应用不可启动: " + appName;
            BridgeSettings.appendEvent(context, message);
            return new CommandExecution(false, message);
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
        String message = "启动应用: " + appName;
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message);
    }

    private static CommandExecution pddShareImage(Context context, String params) throws Exception {
        String artifactId = field(params, "artifact_id");
        String downloadUrl = field(params, "download_url");
        if (downloadUrl.isEmpty()) {
            return new CommandExecution(false, "PDD 图片分享缺少图片下载地址");
        }
        File file = downloadToCache(context, artifactId, downloadUrl);
        shareImage(context, file, "com.xunmeng.pinduoduo");
        String message = "已将图片分享给拼多多";
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message);
    }

    private static CommandExecution pddCreateListingSkeleton(Context context, String params) throws Exception {
        String artifactId = field(params, "artifact_id");
        String downloadUrl = field(params, "download_url");
        String title = field(params, "title");
        String price = field(params, "price");
        String description = field(params, "description");
        boolean allowSubmit = boolField(params, "allow_submit");
        if (!downloadUrl.isEmpty()) {
            File file = downloadToCache(context, artifactId, downloadUrl);
            shareImage(context, file, "com.xunmeng.pinduoduo");
        } else {
            launchApp(context, "拼多多");
        }
        String automation = PddAutomationService.beginListingTask(artifactId, title, price, description, allowSubmit);
        String message = "已进入 PDD 执行入口；" + automation;
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(PddAutomationService.isActive() && !automation.contains("未找到发布/提交按钮"), message);
    }

    private static CommandExecution downloadArtifactCommand(Context context, String params, boolean shareAfterDownload) throws Exception {
        String artifactId = field(params, "artifact_id");
        String downloadUrl = field(params, "download_url");
        String targetPackage = field(params, "target_package");
        if (downloadUrl.isEmpty()) {
            return new CommandExecution(false, "缺少图片下载地址");
        }
        File file = downloadToCache(context, artifactId, downloadUrl);
        String message = "已下载图片: " + file.getName();
        if (shareAfterDownload) {
            shareImage(context, file, targetPackage);
            message = targetPackage.isEmpty()
                    ? "已打开图片分享面板: " + file.getName()
                    : "已分享图片到目标应用: " + targetPackage;
        }
        BridgeSettings.appendEvent(context, message);
        return new CommandExecution(true, message);
    }

    private static File downloadToCache(Context context, String artifactId, String downloadUrl) throws Exception {
        File dir = new File(context.getCacheDir(), "spiritkin-artifacts");
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IOException("cache mkdir failed");
        }
        HttpURLConnection conn = (HttpURLConnection) new URL(downloadUrl).openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(15000);
        BridgeSettings.addAuthHeaders(conn, context);
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            InputStream error = conn.getErrorStream();
            if (error != null) {
                error.close();
            }
            throw new IOException("download HTTP " + code);
        }
        String extension = extensionFromContentType(conn.getContentType());
        String cleanArtifact = artifactId == null || artifactId.trim().isEmpty() ? "artifact" : artifactId.replaceAll("[^A-Za-z0-9_.-]", "_");
        File file = new File(dir, cleanArtifact + "-" + System.currentTimeMillis() + extension);
        InputStream input = conn.getInputStream();
        FileOutputStream output = new FileOutputStream(file);
        try {
            byte[] buffer = new byte[8192];
            int total = 0;
            int read;
            while ((read = input.read(buffer)) != -1) {
                total += read;
                if (total > MAX_DOWNLOAD_BYTES) {
                    throw new IOException("artifact too large");
                }
                output.write(buffer, 0, read);
            }
        } finally {
            output.close();
            input.close();
        }
        return file;
    }

    private static void shareImage(Context context, File file, String targetPackage) {
        Intent intent = new Intent(Intent.ACTION_SEND);
        intent.setType(contentTypeForName(file.getName()));
        intent.putExtra(Intent.EXTRA_STREAM, CacheFileProvider.uriForFile(context, file));
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        if (targetPackage != null && !targetPackage.trim().isEmpty()) {
            intent.setPackage(targetPackage.trim());
        }
        context.startActivity(intent);
    }

    private static int cleanupArtifactCache(Context context) {
        return cleanupCachedArtifacts(context);
    }

    static int cleanupCachedArtifacts(Context context) {
        File dir = new File(context.getCacheDir(), "spiritkin-artifacts");
        File[] files = dir.listFiles();
        if (files == null) {
            return 0;
        }
        int count = 0;
        for (File file : files) {
            if (file.isFile() && file.delete()) {
                count++;
            }
        }
        return count;
    }

    private static String artifactCacheStatus(Context context) {
        File dir = new File(context.getCacheDir(), "spiritkin-artifacts");
        File[] files = dir.listFiles();
        if (files == null) {
            return "缓存图片: 0 个，0 bytes";
        }
        int count = 0;
        long bytes = 0L;
        for (File file : files) {
            if (file.isFile()) {
                count++;
                bytes += file.length();
            }
        }
        return "缓存图片: " + count + " 个，" + bytes + " bytes";
    }

    private static CommandExecution uploadUiSnapshot(Context context) throws Exception {
        String snapshot = PddAutomationService.dumpCurrentUi();
        ArtifactSender.PostResult post = ArtifactSender.postTextArtifactResult(
                context,
                "android-ui-snapshot-" + System.currentTimeMillis() + ".txt",
                "android_ui_snapshot",
                snapshot);
        boolean ok = post.ok();
        String artifactId = jsonField(post.body, "artifact_id");
        String downloadUrl = jsonField(post.body, "download_url");
        String workspaceId = jsonField(post.body, "workspace_id");
        String message = ok
                ? "已上传页面快照" + (artifactId.isEmpty() ? "" : ": " + artifactId)
                : "页面快照上传失败 HTTP " + post.code;
        BridgeSettings.appendEvent(context, message);
        String resultJson = "{"
                + "\"artifact_id\":\"" + escape(artifactId) + "\","
                + "\"download_url\":\"" + escape(downloadUrl) + "\","
                + "\"workspace_id\":\"" + escape(workspaceId) + "\","
                + "\"foreground_package\":\"" + escape(PddAutomationService.foregroundPackage()) + "\","
                + "\"snapshot_chars\":" + snapshot.length()
                + "}";
        return new CommandExecution(ok, message, resultJson);
    }

    private static String extensionFromContentType(String contentType) {
        String value = String.valueOf(contentType == null ? "" : contentType).toLowerCase();
        if (value.contains("png")) {
            return ".png";
        }
        if (value.contains("webp")) {
            return ".webp";
        }
        if (value.contains("gif")) {
            return ".gif";
        }
        return ".jpg";
    }

    private static String contentTypeForName(String name) {
        String value = String.valueOf(name == null ? "" : name).toLowerCase();
        if (value.endsWith(".png")) {
            return "image/png";
        }
        if (value.endsWith(".webp")) {
            return "image/webp";
        }
        if (value.endsWith(".gif")) {
            return "image/gif";
        }
        return "image/jpeg";
    }

    private static String field(String params, String name) {
        Matcher matcher = STRING_FIELD_PATTERN.matcher(params == null ? "" : params);
        while (matcher.find()) {
            if (name.equals(matcher.group(1))) {
                return matcher.group(2);
            }
        }
        return "";
    }

    private static String jsonField(String body, String key) {
        if (body == null || key == null) {
            return "";
        }
        String needle = "\"" + key + "\"";
        int start = body.indexOf(needle);
        if (start < 0) {
            return "";
        }
        int colon = body.indexOf(':', start + needle.length());
        if (colon < 0) {
            return "";
        }
        int quote = body.indexOf('"', colon + 1);
        if (quote < 0) {
            return "";
        }
        StringBuilder out = new StringBuilder();
        boolean escaped = false;
        for (int i = quote + 1; i < body.length(); i++) {
            char ch = body.charAt(i);
            if (escaped) {
                out.append(ch);
                escaped = false;
            } else if (ch == '\\') {
                escaped = true;
            } else if (ch == '"') {
                break;
            } else {
                out.append(ch);
            }
        }
        return out.toString();
    }

    private static boolean boolField(String params, String name) {
        String text = params == null ? "" : params;
        Pattern pattern = Pattern.compile("\\\"" + Pattern.quote(name) + "\\\"\\s*:\\s*(true|false|1|0|\\\"true\\\"|\\\"false\\\"|\\\"1\\\"|\\\"0\\\")", Pattern.CASE_INSENSITIVE);
        Matcher matcher = pattern.matcher(text);
        if (!matcher.find()) {
            return false;
        }
        String value = matcher.group(1).replace("\"", "").toLowerCase();
        return "true".equals(value) || "1".equals(value);
    }

    private static float floatField(String params, String name, float fallback) {
        String text = params == null ? "" : params;
        Pattern pattern = Pattern.compile("\\\"" + Pattern.quote(name) + "\\\"\\s*:\\s*(\\\"[-0-9.]+\\\"|[-0-9.]+)");
        Matcher matcher = pattern.matcher(text);
        if (!matcher.find()) {
            return fallback;
        }
        try {
            return Float.parseFloat(matcher.group(1).replace("\"", ""));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    private static float[] parsePoint(String value) {
        String text = value == null ? "" : value.trim();
        if (text.isEmpty()) {
            return new float[] {Float.NaN, Float.NaN};
        }
        String[] parts = text.split("[,\\s]+");
        if (parts.length < 2) {
            return new float[] {Float.NaN, Float.NaN};
        }
        try {
            return new float[] {Float.parseFloat(parts[0]), Float.parseFloat(parts[1])};
        } catch (NumberFormatException e) {
            return new float[] {Float.NaN, Float.NaN};
        }
    }

    private static String readResponse(InputStream input) throws Exception {
        if (input == null) {
            return "";
        }
        try {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[1024];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            input.close();
        }
    }

    private static void finish(Callback callback, boolean ok, String message) {
        if (callback == null) {
            return;
        }
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(ok, message);
            }
        });
    }

    private static String escape(String value) {
        return String.valueOf(value == null ? "" : value).replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
