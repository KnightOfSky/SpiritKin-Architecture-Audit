package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.content.SharedPreferences;
import java.net.HttpURLConnection;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.TimeUnit;

final class BridgeSettings {
    private static final String PREFS = "pdd_link_bridge";
    private static final String KEY_RECEIVER_URL = "receiver_url";
    private static final String KEY_WORKSPACE_ID = "workspace_id";
    private static final String KEY_PAIRING_TOKEN = "pairing_token";
    private static final String KEY_PAIRING_EXPIRES_AT = "pairing_expires_at";
    private static final String KEY_PAIRING_REQUEST_ID = "pairing_request_id";
    private static final String KEY_PAIRING_REQUEST_SECRET = "pairing_request_secret";
    private static final String KEY_HEARTBEAT_ENABLED = "heartbeat_enabled";
    private static final String KEY_RECENT_EVENTS = "recent_events";
    private static final String KEY_RECENT_UPLOADS = "recent_uploads";
    private static final String KEY_RECENT_LINKS = "recent_links";
    private static final String KEY_PENDING_COMMAND_RESULTS = "pending_command_results";
    private static final String KEY_HEARTBEAT_AUTH_FAIL_COUNT = "heartbeat_auth_fail_count";
    private static final String KEY_CONFIG_VERSION = "config_version";
    private static final int CONFIG_VERSION = 5;
    private static final int MAX_EVENTS = 12;
    private static final int MAX_USER_ITEMS = 8;

    private BridgeSettings() {
    }

    static String getReceiverUrl(Context context) {
        migrate(context);
        String value = prefs(context).getString(KEY_RECEIVER_URL, BridgeConfig.DEFAULT_RECEIVER_URL);
        return normalizeReceiverUrl(value);
    }

    static void setReceiverUrl(Context context, String value) {
        prefs(context).edit()
                .putString(KEY_RECEIVER_URL, normalizeReceiverUrl(value))
                .putInt(KEY_CONFIG_VERSION, CONFIG_VERSION)
                .apply();
    }

    static void applyPairing(Context context, String receiverUrl, String workspaceId, String pairingToken) {
        applyPairing(context, receiverUrl, workspaceId, pairingToken, "");
    }

    static void applyPairing(Context context, String receiverUrl, String workspaceId, String pairingToken, String expiresAt) {
        prefs(context).edit()
                .putString(KEY_RECEIVER_URL, normalizeReceiverUrl(receiverUrl))
                .putString(KEY_WORKSPACE_ID, clean(workspaceId))
                .putString(KEY_PAIRING_TOKEN, clean(pairingToken))
                .putString(KEY_PAIRING_EXPIRES_AT, clean(expiresAt))
                .putInt(KEY_CONFIG_VERSION, CONFIG_VERSION)
                .apply();
        appendEvent(context, "已保存工作区绑定信息");
    }

    static String getWorkspaceId(Context context) {
        return prefs(context).getString(KEY_WORKSPACE_ID, "local-ecommerce");
    }

    static String getPairingToken(Context context) {
        return prefs(context).getString(KEY_PAIRING_TOKEN, "");
    }

    static String getPairingExpiresAt(Context context) {
        return prefs(context).getString(KEY_PAIRING_EXPIRES_AT, "");
    }

    static void setPairingRequestId(Context context, String requestId) {
        prefs(context).edit().putString(KEY_PAIRING_REQUEST_ID, clean(requestId)).apply();
    }

    static void setPairingRequestSecret(Context context, String secret) {
        prefs(context).edit().putString(KEY_PAIRING_REQUEST_SECRET, clean(secret)).apply();
    }

    static String getPairingRequestSecret(Context context) {
        return prefs(context).getString(KEY_PAIRING_REQUEST_SECRET, "");
    }

    static String getPairingRequestId(Context context) {
        return prefs(context).getString(KEY_PAIRING_REQUEST_ID, "");
    }

    static void clearPairingRequestId(Context context) {
        prefs(context).edit().remove(KEY_PAIRING_REQUEST_ID).remove(KEY_PAIRING_REQUEST_SECRET).apply();
    }

    static void clearPairing(Context context, String message) {
        prefs(context).edit()
                .remove(KEY_PAIRING_TOKEN)
                .remove(KEY_PAIRING_EXPIRES_AT)
                .remove(KEY_PAIRING_REQUEST_ID)
                .remove(KEY_PAIRING_REQUEST_SECRET)
                .apply();
        appendEvent(context, message == null || message.trim().isEmpty() ? "绑定已失效，请重新请求配对码" : message);
    }

    static String pairingValidityText(String expiresAt) {
        String formatted = formatControlTime(expiresAt);
        long expiresMs = parseControlTimeMillis(expiresAt);
        if (expiresMs <= 0L) {
            return formatted.isEmpty() ? "有效期未知" : "有效至 " + formatted;
        }
        long remainingMs = expiresMs - System.currentTimeMillis();
        if (remainingMs <= 0L) {
            return "绑定已过期，请重新请求配对码";
        }
        long days = TimeUnit.MILLISECONDS.toDays(remainingMs);
        long hours = TimeUnit.MILLISECONDS.toHours(remainingMs);
        String remaining;
        if (days > 0L) {
            remaining = "剩余约 " + days + " 天";
        } else if (hours > 0L) {
            remaining = "剩余约 " + hours + " 小时";
        } else {
            long minutes = Math.max(1L, TimeUnit.MILLISECONDS.toMinutes(remainingMs));
            remaining = "剩余约 " + minutes + " 分钟";
        }
        return "有效至 " + formatted + "（" + remaining + "）";
    }

    static boolean isPairingExpired(String expiresAt) {
        long expiresMs = parseControlTimeMillis(expiresAt);
        return expiresMs > 0L && expiresMs <= System.currentTimeMillis();
    }

    static String formatControlTime(String value) {
        long millis = parseControlTimeMillis(value);
        if (millis > 0L) {
            SimpleDateFormat format = new SimpleDateFormat("yy-MM-dd HH:mm:ss", Locale.US);
            format.setTimeZone(TimeZone.getDefault());
            return format.format(new Date(millis));
        }
        return clean(value);
    }

    static String getPairingRequestUrl(Context context) {
        return androidBaseUrl(context) + "/pairing/request";
    }

    static String getPairingStatusUrl(Context context, String requestId) {
        return androidBaseUrl(context) + "/pairing/status?request_id=" + urlEncode(clean(requestId)) + "&request_secret=" + urlEncode(getPairingRequestSecret(context)) + "&workspace_id=" + urlEncode(getWorkspaceId(context));
    }

    static boolean isPaired(Context context) {
        return !getPairingToken(context).trim().isEmpty();
    }

    static String getPairUrl(Context context) {
        String receiverUrl = getReceiverUrl(context);
        if (receiverUrl.endsWith("/link")) {
            return receiverUrl.substring(0, receiverUrl.length() - "/link".length()) + "/pair";
        }
        return receiverUrl;
    }

    static String getUnpairUrl(Context context) {
        return androidBaseUrl(context) + "/unpair";
    }

    static void setHeartbeatEnabled(Context context, boolean enabled) {
        prefs(context).edit().putBoolean(KEY_HEARTBEAT_ENABLED, enabled).apply();
    }

    static boolean isHeartbeatEnabled(Context context) {
        return prefs(context).getBoolean(KEY_HEARTBEAT_ENABLED, false);
    }

    static String getHealthUrl(Context context) {
        return androidBaseUrl(context) + "/health";
    }

    static String getHeartbeatUrl(Context context) {
        return androidBaseUrl(context) + "/heartbeat";
    }

    static String getArtifactUrl(Context context) {
        return androidBaseUrl(context) + "/artifact";
    }

    static String getAndroidBaseUrl(Context context) {
        return androidBaseUrl(context);
    }

    static void addAuthHeaders(HttpURLConnection conn, Context context) {
        String token = getPairingToken(context);
        if (!token.trim().isEmpty()) {
            conn.setRequestProperty("Authorization", "Bearer " + token);
            conn.setRequestProperty("X-SpiritKin-Workspace", getWorkspaceId(context));
        }
    }

    static void appendEvent(Context context, String message) {
        String clean = message == null ? "" : message.replace('\n', ' ').trim();
        String stamp = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date());
        String line = stamp + "  " + clean;
        String current = prefs(context).getString(KEY_RECENT_EVENTS, "");
        String[] oldLines = current.isEmpty() ? new String[0] : current.split("\\n");
        StringBuilder next = new StringBuilder(line);
        for (int i = 0; i < oldLines.length && i < MAX_EVENTS - 1; i++) {
            if (!oldLines[i].trim().isEmpty()) {
                next.append('\n').append(oldLines[i]);
            }
        }
        prefs(context).edit().putString(KEY_RECENT_EVENTS, next.toString()).apply();
    }

    static String getRecentEvents(Context context) {
        return prefs(context).getString(KEY_RECENT_EVENTS, "");
    }

    static void appendUpload(Context context, String message) {
        appendListItem(context, KEY_RECENT_UPLOADS, message, MAX_USER_ITEMS);
    }

    static String getRecentUploads(Context context) {
        return prefs(context).getString(KEY_RECENT_UPLOADS, "");
    }

    static void clearRecentUploads(Context context) {
        prefs(context).edit().remove(KEY_RECENT_UPLOADS).apply();
    }

    static void appendLink(Context context, String message) {
        appendListItem(context, KEY_RECENT_LINKS, message, MAX_USER_ITEMS);
    }

    static String getRecentLinks(Context context) {
        return prefs(context).getString(KEY_RECENT_LINKS, "");
    }

    static void clearRecentLinks(Context context) {
        prefs(context).edit().remove(KEY_RECENT_LINKS).apply();
    }

    static String getPendingCommandResults(Context context) {
        return prefs(context).getString(KEY_PENDING_COMMAND_RESULTS, "");
    }

    static void setPendingCommandResults(Context context, String resultsJson) {
        String clean = resultsJson == null ? "" : resultsJson.trim();
        if (clean.isEmpty() || "[]".equals(clean)) {
            clearPendingCommandResults(context);
            return;
        }
        prefs(context).edit().putString(KEY_PENDING_COMMAND_RESULTS, clean).apply();
    }

    static void clearPendingCommandResults(Context context) {
        prefs(context).edit().remove(KEY_PENDING_COMMAND_RESULTS).apply();
    }

    static int incrementHeartbeatAuthFailure(Context context) {
        int count = prefs(context).getInt(KEY_HEARTBEAT_AUTH_FAIL_COUNT, 0) + 1;
        prefs(context).edit().putInt(KEY_HEARTBEAT_AUTH_FAIL_COUNT, count).apply();
        return count;
    }

    static void clearHeartbeatAuthFailure(Context context) {
        prefs(context).edit().remove(KEY_HEARTBEAT_AUTH_FAIL_COUNT).apply();
    }

    private static void appendListItem(Context context, String key, String message, int maxItems) {
        String clean = message == null ? "" : message.replace('\n', ' ').trim();
        if (clean.isEmpty()) {
            return;
        }
        String stamp = new SimpleDateFormat("MM-dd HH:mm", Locale.US).format(new Date());
        String line = stamp + "  " + clean;
        String current = prefs(context).getString(key, "");
        String[] oldLines = current.isEmpty() ? new String[0] : current.split("\\n");
        StringBuilder next = new StringBuilder(line);
        for (int i = 0; i < oldLines.length && i < maxItems - 1; i++) {
            if (!oldLines[i].trim().isEmpty()) {
                next.append('\n').append(oldLines[i]);
            }
        }
        prefs(context).edit().putString(key, next.toString()).apply();
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String urlEncode(String value) {
        try {
            return java.net.URLEncoder.encode(value == null ? "" : value, "UTF-8");
        } catch (Exception ignored) {
            return "";
        }
    }

    private static long parseControlTimeMillis(String value) {
        String text = clean(value);
        if (text.isEmpty()) {
            return 0L;
        }
        int dot = text.indexOf('.');
        if (dot > 0) {
            int zoneStart = text.indexOf('+', dot);
            if (zoneStart < 0) {
                zoneStart = text.indexOf('-', dot);
            }
            if (zoneStart < 0) {
                zoneStart = text.indexOf('Z', dot);
            }
            if (zoneStart > dot + 4) {
                text = text.substring(0, dot + 4) + text.substring(zoneStart);
            }
        }
        String normalized = text.replace("Z", "+0000");
        if (normalized.matches(".*[+-]\\d\\d:\\d\\d$")) {
            normalized = normalized.substring(0, normalized.length() - 3) + normalized.substring(normalized.length() - 2);
        }
        String[] patterns = {
                "yyyy-MM-dd'T'HH:mm:ss.SSSSSSZ",
                "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
                "yyyy-MM-dd'T'HH:mm:ssZ",
                "yyyy-MM-dd HH:mm:ss"
        };
        for (String pattern : patterns) {
            try {
                SimpleDateFormat format = new SimpleDateFormat(pattern, Locale.US);
                format.setTimeZone(TimeZone.getTimeZone("UTC"));
                Date parsed = format.parse(normalized);
                if (parsed != null) {
                    return parsed.getTime();
                }
            } catch (ParseException ignored) {
            }
        }
        return 0L;
    }

    private static void migrate(Context context) {
        SharedPreferences preferences = prefs(context);
        int version = preferences.getInt(KEY_CONFIG_VERSION, 0);
        String current = preferences.getString(KEY_RECEIVER_URL, "");
        String normalized = normalizeReceiverUrl(current);
        boolean legacyEndpoint = normalized.contains(":8765/") || normalized.endsWith(":8765/link");
        boolean missingAndroidPath = !normalized.contains("/android/link");
        if (version < CONFIG_VERSION || legacyEndpoint || missingAndroidPath) {
            String nextUrl = legacyEndpoint ? BridgeConfig.DEFAULT_RECEIVER_URL : normalized;
            preferences.edit()
                    .putString(KEY_RECEIVER_URL, nextUrl)
                    .putInt(KEY_CONFIG_VERSION, CONFIG_VERSION)
                    .apply();
            if (legacyEndpoint || missingAndroidPath) {
                appendEvent(context, "已自动规范主控服务地址");
            }
        }
    }

    static String normalizeReceiverUrl(String value) {
        String url = value == null ? "" : value.trim();
        if (url.isEmpty()) {
            url = BridgeConfig.DEFAULT_RECEIVER_URL;
        }
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        if (url.endsWith("/android/health")) {
            url = url.substring(0, url.length() - "/health".length()) + "/link";
        } else if (url.endsWith("/health")) {
            url = url.substring(0, url.length() - "/health".length()) + "/link";
        } else if (url.endsWith("/android/link")) {
            return url;
        } else if (url.endsWith("/android")) {
            url = url + "/link";
        } else if (url.endsWith("/link")) {
            url = url.substring(0, url.length() - "/link".length()) + "/android/link";
        } else if (!url.endsWith("/link")) {
            url = url + "/android/link";
        }
        return url;
    }

    static String androidBaseUrlFromReceiverUrl(String receiverUrl) {
        if (receiverUrl.endsWith("/android/link")) {
            return receiverUrl.substring(0, receiverUrl.length() - "/link".length());
        }
        if (receiverUrl.endsWith("/link")) {
            return receiverUrl.substring(0, receiverUrl.length() - "/link".length());
        }
        if (receiverUrl.endsWith("/android")) {
            return receiverUrl;
        }
        return receiverUrl + "/android";
    }

    private static String androidBaseUrl(Context context) {
        return androidBaseUrlFromReceiverUrl(getReceiverUrl(context));
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
