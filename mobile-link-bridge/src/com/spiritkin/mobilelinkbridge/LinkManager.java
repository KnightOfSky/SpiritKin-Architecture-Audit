package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import java.util.ArrayList;
import java.util.List;

final class LinkManager {
    interface ListCallback {
        void onDone(boolean ok, String message, List<LinkItem> items);
    }

    interface ActionCallback {
        void onDone(boolean ok, String message);
    }

    static final class LinkItem {
        final String linkId;
        final String link;
        final String receivedAt;
        final String source;

        LinkItem(String linkId, String link, String receivedAt, String source) {
            this.linkId = linkId;
            this.link = link;
            this.receivedAt = receivedAt;
            this.source = source;
        }
    }

    private LinkManager() {
    }

    static void fetchLinks(Context context, ListCallback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    BridgeHttpClient.HttpResult result = BridgeHttpClient.getText(context, BridgeSettings.getAndroidBaseUrl(context) + "/links?format=lines&limit=120");
                    if (!result.ok()) {
                        finishList(callback, false, "云端链接刷新失败 HTTP " + result.code, new ArrayList<LinkItem>());
                        return;
                    }
                    List<LinkItem> items = parseLines(result.body);
                    String message = items.isEmpty() ? "云端暂无本机链接" : "已刷新云端链接: " + items.size() + " 条";
                    finishList(callback, true, message, items);
                } catch (Exception e) {
                    finishList(callback, false, "云端链接刷新失败: " + e.getClass().getSimpleName(), new ArrayList<LinkItem>());
                }
            }
        }).start();
    }

    static void deleteLink(Context context, String linkId, ActionCallback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String body = "{\"link_id\":\"" + escapeJson(linkId) + "\"}";
                    BridgeHttpClient.HttpResult result = BridgeHttpClient.postJson(context, BridgeSettings.getAndroidBaseUrl(context) + "/links/delete", body);
                    if (!result.ok()) {
                        finishAction(callback, false, "云端链接删除失败 HTTP " + result.code);
                        return;
                    }
                    BridgeSettings.appendEvent(context, "已删除云端链接 " + shortId(linkId));
                    finishAction(callback, true, "已删除云端链接");
                } catch (Exception e) {
                    finishAction(callback, false, "云端链接删除失败: " + e.getClass().getSimpleName());
                }
            }
        }).start();
    }

    static String summary(List<LinkItem> items) {
        if (items == null || items.isEmpty()) {
            return "暂无云端链接。复制商品链接后点“发送剪贴板链接”，或从 PDD/微信分享文本到本应用。";
        }
        return items.size() + " 条商品链接";
    }

    static String formatItem(LinkItem item) {
        StringBuilder out = new StringBuilder();
        out.append(shorten(item.link, 92));
        String detail = "链接编号 " + shortId(item.linkId);
        if (item.receivedAt != null && item.receivedAt.length() >= 16) {
            detail += " · " + item.receivedAt.substring(5, 16).replace('T', ' ');
        }
        out.append("\n").append(detail);
        return out.toString();
    }

    static String shortId(String id) {
        String text = id == null ? "" : id.trim();
        if (text.length() <= 18) {
            return text;
        }
        return text.substring(0, 10) + "..." + text.substring(text.length() - 5);
    }

    private static List<LinkItem> parseLines(String text) {
        ArrayList<LinkItem> items = new ArrayList<LinkItem>();
        if (text == null || text.trim().isEmpty()) {
            return items;
        }
        String[] lines = text.split("\\r?\\n");
        for (String line : lines) {
            if (line == null || line.trim().isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] parts = line.split("\\t", -1);
            if (parts.length < 4) {
                continue;
            }
            items.add(new LinkItem(parts[0], parts[1], parts[2], parts[3]));
        }
        return items;
    }

    private static void finishList(ListCallback callback, boolean ok, String message, List<LinkItem> items) {
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

    private static String escapeJson(String value) {
        if (value == null) {
            return "";
        }
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r");
    }

    private static String shorten(String value, int max) {
        String text = value == null ? "" : value.trim();
        if (text.length() <= max) {
            return text;
        }
        return text.substring(0, Math.max(0, max - 1)) + "...";
    }

}
