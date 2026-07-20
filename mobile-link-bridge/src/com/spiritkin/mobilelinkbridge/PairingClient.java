package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class PairingClient {
    private static final int APPROVAL_POLL_ATTEMPTS = 90;
    private static final long APPROVAL_POLL_INTERVAL_MS = 2000L;

    interface Callback {
        void onDone(boolean ok, String message);
    }

    private PairingClient() {
    }

    static boolean canHandle(Uri uri) {
        return uri != null && "spiritkin".equals(uri.getScheme()) && "pair".equals(uri.getHost());
    }

    static void applyPairingUri(Context context, Uri uri, Callback callback) {
        if (!canHandle(uri)) {
            finish(callback, false, "不是 SpiritKin 配对链接");
            return;
        }
        String serverUrl = clean(uri.getQueryParameter("server_url"));
        String workspaceId = clean(uri.getQueryParameter("workspace_id"));
        String token = clean(uri.getQueryParameter("pairing_token"));
        if (serverUrl.isEmpty() || token.isEmpty()) {
            finish(callback, false, "配对链接缺少服务器地址或配对码");
            return;
        }
        BridgeSettings.applyPairing(context, serverUrl, workspaceId, token);
        bind(context, callback);
    }

    static void bind(Context context, Callback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String token = BridgeSettings.getPairingToken(context);
                    if (token.trim().isEmpty()) {
                        finish(callback, false, "尚未保存配对码");
                        return;
                    }
                    String deviceId = Build.MANUFACTURER + "-" + Build.MODEL;
                    String bodyText = "{"
                            + "\"pairing_token\":\"" + escape(token) + "\","
                            + "\"device_id\":\"" + escape(deviceId) + "\","
                            + "\"device_state\":{"
                            + "\"manufacturer\":\"" + escape(Build.MANUFACTURER) + "\","
                            + "\"model\":\"" + escape(Build.MODEL) + "\","
                            + "\"android_version\":\"" + escape(Build.VERSION.RELEASE) + "\""
                            + "}"
                            + "}";
                    byte[] body = bodyText.getBytes(StandardCharsets.UTF_8);
                    HttpURLConnection conn = (HttpURLConnection) new URL(BridgeSettings.getPairUrl(context)).openConnection();
                    conn.setRequestMethod("POST");
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(10000);
                    conn.setDoOutput(true);
                    conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                    conn.setRequestProperty("Content-Length", String.valueOf(body.length));
                    OutputStream out = conn.getOutputStream();
                    try {
                        out.write(body);
                    } finally {
                        out.close();
                    }
                    int code = conn.getResponseCode();
                    InputStream input = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
                    String response = readResponse(input);
                    boolean ok = code >= 200 && code < 300;
                    String error = jsonField(response, "error");
                    if (error.isEmpty()) {
                        error = jsonField(response, "detail");
                    }
                    if (error.isEmpty()) {
                        error = jsonField(response, "message");
                    }
                    String message = ok ? "配对成功，手机已绑定到工作区" : pairingFailureMessage(code, error);
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, ok, message);
                } catch (Exception e) {
                    String message = "配对失败: " + e.getClass().getSimpleName();
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, false, message);
                }
            }
        }).start();
    }

    static void bindWithDiscovery(Context context, Callback callback) {
        ReceiverDiscovery.findAndSave(context, new ReceiverDiscovery.Callback() {
            @Override
            public void onDone(boolean ok, String receiverUrl, String message) {
                if (!ok) {
                    finish(callback, false, message);
                    return;
                }
                bind(context, callback);
            }
        });
    }

    static void requestAndBindWithDiscovery(Context context, Callback callback) {
        ReceiverDiscovery.findAndSave(context, new ReceiverDiscovery.Callback() {
            @Override
            public void onDone(boolean ok, String receiverUrl, String message) {
                if (!ok) {
                    finish(callback, false, message);
                    return;
                }
                requestAndBind(context, callback);
            }
        });
    }

    static void requestAndBind(Context context, Callback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String deviceId = Build.MANUFACTURER + "-" + Build.MODEL;
                    String bodyText = "{"
                            + "\"workspace_id\":\"" + escape(BridgeSettings.getWorkspaceId(context)) + "\","
                            + "\"device_id\":\"" + escape(deviceId) + "\","
                            + "\"requested_by\":\"android_bridge\","
                            + "\"device_state\":{"
                            + "\"manufacturer\":\"" + escape(Build.MANUFACTURER) + "\","
                            + "\"model\":\"" + escape(Build.MODEL) + "\","
                            + "\"android_version\":\"" + escape(Build.VERSION.RELEASE) + "\""
                            + "}"
                            + "}";
                    String response = postJson(BridgeSettings.getPairingRequestUrl(context), bodyText);
                    String requestId = firstJsonField(response, "request_id", "token_id");
                    if (requestId.isEmpty()) {
                        throw new IllegalStateException("missing request_id");
                    }
                    BridgeSettings.setPairingRequestId(context, requestId);
                    String requestSecret = firstJsonField(response, "request_secret");
                    BridgeSettings.setPairingRequestSecret(context, requestSecret);
                    BridgeSettings.appendEvent(context, "已发送绑定请求，等待主控批准");
                    progress(callback, "已发送绑定请求，正在等待主控批准");
                    pollApprovalAndBind(context, requestId, callback);
                } catch (Exception e) {
                    String message = "请求绑定失败: " + errorText(e);
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, false, message);
                }
            }
        }).start();
    }

    static void checkPendingApproval(Context context, Callback callback) {
        String requestId = BridgeSettings.getPairingRequestId(context);
        if (requestId.trim().isEmpty()) {
            finish(callback, false, "没有待主控批准的绑定请求");
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                pollApprovalAndBind(context, requestId, callback);
            }
        }).start();
    }

    static void unpair(Context context, Callback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String token = BridgeSettings.getPairingToken(context);
                    if (!token.trim().isEmpty()) {
                        postJson(BridgeSettings.getUnpairUrl(context), "{\"token\":\"" + escape(token) + "\"}");
                    }
                    BridgeSettings.clearPairing(context, "已撤销本机绑定");
                    finish(callback, true, "已撤销本机绑定");
                } catch (Exception e) {
                    BridgeSettings.clearPairing(context, "本机绑定已清除，云端撤销待下次确认");
                    finish(callback, false, "本机绑定已清除，云端撤销失败: " + errorText(e));
                }
            }
        }).start();
    }

    private static void pollApprovalAndBind(Context context, String requestId, Callback callback) {
        for (int attempt = 0; attempt < APPROVAL_POLL_ATTEMPTS; attempt++) {
            try {
                String response = getText(BridgeSettings.getPairingStatusUrl(context, requestId));
                String status = jsonField(response, "status");
                if ("pending".equals(status)) {
                    String token = firstJsonField(response, "token", "pairing_token");
                    String workspaceId = jsonField(response, "workspace_id");
                    String receiverUrl = jsonField(response, "receiver_url");
                    String expiresAt = jsonField(response, "expires_at");
                    if (token.isEmpty()) {
                        throw new IllegalStateException("approved response missing token");
                    }
                    BridgeSettings.applyPairing(
                            context,
                            receiverUrl.isEmpty() ? BridgeSettings.getReceiverUrl(context) : receiverUrl,
                            workspaceId.isEmpty() ? BridgeSettings.getWorkspaceId(context) : workspaceId,
                            token,
                            expiresAt);
                    BridgeSettings.clearPairingRequestId(context);
                    progress(callback, "主控已批准，正在绑定手机");
                    bind(context, new Callback() {
                        @Override
                        public void onDone(boolean ok, String message) {
                            String finalMessage = ok && !expiresAt.isEmpty()
                                    ? "绑定成功，" + BridgeSettings.pairingValidityText(expiresAt)
                                    : message;
                            finish(callback, ok, finalMessage);
                        }
                    });
                    return;
                }
                if ("rejected".equals(status)) {
                    BridgeSettings.clearPairingRequestId(context);
                    finish(callback, false, "主控已拒绝绑定请求");
                    return;
                }
                if ("expired".equals(status)) {
                    BridgeSettings.clearPairingRequestId(context);
                    finish(callback, false, "绑定请求已过期，请重新请求");
                    return;
                }
                Thread.sleep(APPROVAL_POLL_INTERVAL_MS);
            } catch (Exception e) {
                if (attempt >= APPROVAL_POLL_ATTEMPTS - 1) {
                    finish(callback, false, "等待主控批准失败: " + errorText(e));
                    return;
                }
                try {
                    Thread.sleep(APPROVAL_POLL_INTERVAL_MS);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    finish(callback, false, "等待主控批准已中断");
                    return;
                }
            }
        }
        finish(callback, true, "绑定请求已发送，仍在等待主控批准");
    }

    private static void progress(Callback callback, String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(false, message);
            }
        });
    }

    private static void finish(Callback callback, boolean ok, String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(ok, message);
            }
        });
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
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

    private static String postJson(String url, String bodyText) throws Exception {
        byte[] body = bodyText.getBytes(StandardCharsets.UTF_8);
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(10000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Content-Length", String.valueOf(body.length));
        OutputStream out = conn.getOutputStream();
        try {
            out.write(body);
        } finally {
            out.close();
        }
        int code = conn.getResponseCode();
        InputStream input = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readResponse(input);
        if (code < 200 || code >= 300) {
            String error = firstJsonField(response, "error", "detail", "message");
            throw new IllegalStateException(error.isEmpty() ? ("HTTP " + code) : error);
        }
        return response;
    }

    private static String getText(String url) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(10000);
        int code = conn.getResponseCode();
        InputStream input = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readResponse(input);
        if (code < 200 || code >= 300) {
            String error = firstJsonField(response, "error", "detail", "message");
            throw new IllegalStateException(error.isEmpty() ? ("HTTP " + code) : error);
        }
        return response;
    }

    private static String errorText(Exception e) {
        String detail = e.getMessage();
        return detail == null || detail.trim().isEmpty() ? e.getClass().getSimpleName() : detail;
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

    private static String firstJsonField(String body, String... keys) {
        if (keys == null) {
            return "";
        }
        for (String key : keys) {
            String value = jsonField(body, key);
            if (!value.isEmpty()) {
                return value;
            }
        }
        return "";
    }

    private static String pairingFailureMessage(int code, String error) {
        String detail = clean(error);
        if (detail.contains("pairing token is not pending")) {
            return "配对失败：这个配对码已经用过、过期或被取消。请在主控端重新生成 Android 配对码，再绑定到工作区。";
        }
        if (detail.contains("invalid pairing token") || detail.contains("not found")) {
            return "配对失败：配对码无效。请确认使用的是 Android 配对码，不是 Worker 或 iOS 主控令牌。";
        }
        if (detail.contains("expired")) {
            return "配对失败：配对码已过期。请在主控端重新生成 Android 配对码。";
        }
        if (detail.contains("device role")) {
            return "配对失败：配对码类型不匹配。手机端必须使用 Android 配对码。";
        }
        return "配对失败 HTTP " + code + (detail.isEmpty() ? "" : ": " + detail);
    }

    private static String escape(String value) {
        return String.valueOf(value == null ? "" : value).replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
