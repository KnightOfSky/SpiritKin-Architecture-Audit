package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Looper;
import java.net.URLEncoder;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class ArtifactManager {
    interface ListCallback {
        void onDone(boolean ok, String message, List<UploadItem> items);
    }

    interface ActionCallback {
        void onDone(boolean ok, String message);
    }

    interface ImageCallback {
        void onDone(boolean ok, Bitmap bitmap);
    }

    static final class UploadItem {
        final String artifactId;
        final int fileIndex;
        final String name;
        final String mimeType;
        final long sizeBytes;
        final String createdAt;
        final String purpose;

        UploadItem(String artifactId, int fileIndex, String name, String mimeType, long sizeBytes, String createdAt, String purpose) {
            this.artifactId = artifactId;
            this.fileIndex = fileIndex;
            this.name = name;
            this.mimeType = mimeType;
            this.sizeBytes = sizeBytes;
            this.createdAt = createdAt;
            this.purpose = purpose;
        }
    }

    private ArtifactManager() {
    }

    static void fetchUploads(Context context, ListCallback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String url = BridgeSettings.getAndroidBaseUrl(context) + "/artifacts?format=lines&limit=120";
                    BridgeHttpClient.HttpResult result = BridgeHttpClient.getText(context, url);
                    if (!result.ok()) {
                        finishList(callback, false, "云端图片刷新失败 HTTP " + result.code, new ArrayList<UploadItem>());
                        return;
                    }
                    List<UploadItem> items = parseUploadLines(result.body);
                    String message = items.isEmpty() ? "云端暂无本机上传图片" : "已刷新云端图片: " + items.size() + " 张";
                    finishList(callback, true, message, items);
                } catch (Exception e) {
                    finishList(callback, false, "云端图片刷新失败: " + e.getClass().getSimpleName(), new ArrayList<UploadItem>());
                }
            }
        }).start();
    }

    static void deleteUploadFile(Context context, String artifactId, int fileIndex, ActionCallback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String body = "{\"artifact_id\":\"" + escapeJson(artifactId) + "\",\"file_index\":" + fileIndex + "}";
                    BridgeHttpClient.HttpResult result = BridgeHttpClient.postJson(context, BridgeSettings.getAndroidBaseUrl(context) + "/artifacts/delete-file", body);
                    if (!result.ok()) {
                        finishAction(callback, false, "云端图片删除失败 HTTP " + result.code);
                        return;
                    }
                    BridgeSettings.appendEvent(context, "已删除云端图片 " + shortId(artifactId) + " #" + (fileIndex + 1));
                    finishAction(callback, true, "已删除云端图片");
                } catch (Exception e) {
                    finishAction(callback, false, "云端图片删除失败: " + e.getClass().getSimpleName());
                }
            }
        }).start();
    }

    static void fetchPreview(Context context, String artifactId, int fileIndex, ImageCallback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String url = BridgeSettings.getAndroidBaseUrl(context)
                            + "/artifact/" + URLEncoder.encode(artifactId, "UTF-8")
                            + "?file_index=" + fileIndex;
                    BridgeHttpClient.HttpResult result = BridgeHttpClient.getBytes(context, url);
                    if (!result.ok() || result.bytes.length == 0) {
                        finishImage(callback, false, null);
                        return;
                    }
                    Bitmap bitmap = BitmapFactory.decodeByteArray(result.bytes, 0, result.bytes.length);
                    finishImage(callback, bitmap != null, bitmap);
                } catch (Exception e) {
                    finishImage(callback, false, null);
                }
            }
        }).start();
    }

    static String summary(List<UploadItem> items) {
        if (items == null || items.isEmpty()) {
            return "暂无云端图片。新增图片请从相册选择图片，然后分享到 SpiritKin Android 手机端。";
        }
        Map<String, Integer> counts = new LinkedHashMap<String, Integer>();
        long bytes = 0;
        for (UploadItem item : items) {
            Integer old = counts.get(item.artifactId);
            counts.put(item.artifactId, old == null ? 1 : old + 1);
            bytes += item.sizeBytes;
        }
        return counts.size() + " 个图片组 · " + items.size() + " 张图片 · " + formatBytes(bytes);
    }

    static String shortId(String artifactId) {
        if (artifactId == null) {
            return "";
        }
        String text = artifactId.trim();
        if (text.length() <= 18) {
            return text;
        }
        return text.substring(0, 10) + "..." + text.substring(text.length() - 5);
    }

    static String formatItem(UploadItem item) {
        StringBuilder out = new StringBuilder();
        out.append("图片组 ").append(shortId(item.artifactId)).append(" · 第 ").append(item.fileIndex + 1).append(" 张");
        if (item.name != null && !item.name.trim().isEmpty()) {
            out.append("\n").append(item.name.trim());
        }
        String detail = formatBytes(item.sizeBytes);
        if (item.createdAt != null && item.createdAt.length() >= 16) {
            detail += " · " + item.createdAt.substring(5, 16).replace('T', ' ');
        }
        out.append("\n").append(detail);
        return out.toString();
    }

    private static List<UploadItem> parseUploadLines(String text) {
        ArrayList<UploadItem> items = new ArrayList<UploadItem>();
        if (text == null || text.trim().isEmpty()) {
            return items;
        }
        String[] lines = text.split("\\r?\\n");
        for (String line : lines) {
            if (line == null || line.trim().isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] parts = line.split("\\t", -1);
            if (parts.length < 7) {
                continue;
            }
            items.add(new UploadItem(
                    parts[0],
                    parseInt(parts[1]),
                    parts[2],
                    parts[3],
                    parseLong(parts[4]),
                    parts[5],
                    parts[6]));
        }
        return items;
    }

    private static int parseInt(String value) {
        try {
            return Integer.parseInt(value == null ? "0" : value.trim());
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private static long parseLong(String value) {
        try {
            return Long.parseLong(value == null ? "0" : value.trim());
        } catch (NumberFormatException e) {
            return 0L;
        }
    }

    private static String formatBytes(long bytes) {
        if (bytes >= 1024L * 1024L) {
            return String.format(java.util.Locale.US, "%.1f MB", bytes / 1024.0 / 1024.0);
        }
        if (bytes >= 1024L) {
            return String.format(java.util.Locale.US, "%.1f KB", bytes / 1024.0);
        }
        return bytes + " B";
    }

    private static void finishList(ListCallback callback, boolean ok, String message, List<UploadItem> items) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(ok, message, items);
            }
        });
    }

    private static void finishAction(ActionCallback callback, boolean ok, String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(ok, message);
            }
        });
    }

    private static void finishImage(ImageCallback callback, boolean ok, Bitmap bitmap) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(ok, bitmap);
            }
        });
    }

    private static String escapeJson(String value) {
        if (value == null) {
            return "";
        }
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r");
    }

}
