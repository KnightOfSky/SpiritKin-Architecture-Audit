package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class LinkSender {
    private static final Pattern PDD_WEB_LINK = Pattern.compile(
            "https?://[^\\s\"'<>]*\\b(?:yangkeduo|pinduoduo)\\.com/[^\\s\"'<>]*");

    interface Callback {
        void onDone(boolean ok, String message);
    }

    private LinkSender() {
    }

    static String extractPddLink(String text) {
        if (text == null) {
            return "";
        }
        Matcher matcher = PDD_WEB_LINK.matcher(text);
        return matcher.find() ? matcher.group() : "";
    }

    static void postLink(Context context, String rawText, Callback callback) {
        String link = extractPddLink(rawText);
        if (link.isEmpty()) {
            finish(callback, false, "未找到拼多多链接");
            return;
        }

        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    byte[] body = ("{\"link\":\"" + escapeJson(link) + "\",\"source\":\"android-bridge\"}")
                            .getBytes(StandardCharsets.UTF_8);
                    PostResult result;
                    try {
                        result = postJson(context, BridgeSettings.getReceiverUrl(context), body);
                    } catch (Exception first) {
                        if (!discoverReceiverBlocking(context)) {
                            throw first;
                        }
                        result = postJson(context, BridgeSettings.getReceiverUrl(context), body);
                    }
                    int code = result.code;
                    boolean ok = code >= 200 && code < 300;
                    if (ok) {
                        BridgeSettings.appendLink(context, shorten(link, 72) + " · 已回传");
                    }
                    String message = ok ? "已回传链接，已加入我的链接记录" : "回传失败 HTTP " + code;
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, ok, message);
                } catch (Exception e) {
                    String message = "回传失败: " + e.getClass().getSimpleName();
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, false, message);
                }
            }
        }).start();
    }

    static void checkHealth(Context context, Callback callback) {
        ReceiverDiscovery.findAndSave(context, new ReceiverDiscovery.Callback() {
            @Override
            public void onDone(boolean ok, String receiverUrl, String message) {
                callback.onDone(ok, ok ? "接收器在线: " + receiverUrl : message);
            }
        });
    }

    private static PostResult postJson(Context context, String url, byte[] body) throws Exception {
        BridgeHttpClient.HttpResult result = BridgeHttpClient.postJson(context, url, body, 3000, 3000);
        return new PostResult(result.code, result.body);
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

    static void toast(Context context, String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                Toast.makeText(context, message, Toast.LENGTH_SHORT).show();
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

    private static String linkSummary(String body, String fallbackLink) {
        String linkId = jsonField(body, "link_id");
        String workspaceId = jsonField(body, "workspace_id");
        String legacyLatest = jsonField(body, "legacy_latest");
        String legacyQueue = jsonField(body, "legacy_queue");
        StringBuilder out = new StringBuilder();
        if (fallbackLink != null && !fallbackLink.trim().isEmpty()) {
            out.append(fallbackLink.trim());
        }
        if (!linkId.isEmpty()) {
            appendPart(out, "链接编号 " + shortId(linkId));
        }
        if (!workspaceId.isEmpty()) {
            appendPart(out, "工作区 " + workspaceId);
        }
        if (!legacyLatest.isEmpty()) {
            appendPart(out, "最新记录 " + legacyLatest);
        } else if (!legacyQueue.isEmpty()) {
            appendPart(out, "队列 " + legacyQueue);
        }
        return out.length() == 0 ? "已进入主控链接队列" : out.toString();
    }

    private static String shorten(String value, int max) {
        String text = value == null ? "" : value.trim();
        if (text.length() <= max) {
            return text;
        }
        return text.substring(0, Math.max(0, max - 1)) + "...";
    }

    private static String shortId(String value) {
        String text = value == null ? "" : value.trim();
        if (text.length() <= 18) {
            return text;
        }
        return text.substring(0, 10) + "..." + text.substring(text.length() - 5);
    }

    private static void appendPart(StringBuilder out, String value) {
        if (out.length() > 0) {
            out.append(" · ");
        }
        out.append(value);
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

    private static String escapeJson(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static final class PostResult {
        final int code;
        final String body;

        PostResult(int code, String body) {
            this.code = code;
            this.body = body == null ? "" : body;
        }
    }
}
