package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.os.Handler;
import android.os.Looper;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.URL;
import java.util.LinkedHashSet;
import java.util.Set;

final class ReceiverDiscovery {
    interface Callback {
        void onDone(boolean ok, String receiverUrl, String message);
    }

    private ReceiverDiscovery() {
    }

    static void findAndSave(Context context, Callback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                StringBuilder tried = new StringBuilder();
                for (String receiverUrl : candidates(context)) {
                    if (receiverUrl == null || receiverUrl.trim().isEmpty()) {
                        continue;
                    }
                    String normalized = normalize(receiverUrl);
                    appendTried(tried, normalized);
                    if (isHealthy(context, normalized)) {
                        BridgeSettings.setReceiverUrl(context, normalized);
                        String message = "已自动连接: " + displayBase(normalized);
                        BridgeSettings.appendEvent(context, message);
                        finish(callback, true, normalized, message);
                        return;
                    }
                }
                String message = tried.length() == 0
                        ? "未找到可用主控服务"
                        : "未找到可用主控服务，已尝试: " + tried.toString();
                BridgeSettings.appendEvent(context, message);
                finish(callback, false, "", message);
            }
        }).start();
    }

    private static Set<String> candidates(Context context) {
        LinkedHashSet<String> out = new LinkedHashSet<String>();
        out.add(BridgeSettings.getReceiverUrl(context));
        for (String url : BridgeConfig.FALLBACK_RECEIVER_URLS) {
            out.add(url);
        }
        for (String host : localLanCandidates(context)) {
            out.add("http://" + host + ":" + BridgeConfig.DEFAULT_RECEIVER_PORT + "/android/link");
        }
        return out;
    }

    private static Set<String> localLanCandidates(Context context) {
        LinkedHashSet<String> out = new LinkedHashSet<String>();
        ConnectivityManager manager = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (manager == null) {
            return out;
        }
        Network network = manager.getActiveNetwork();
        if (network == null) {
            return out;
        }
        LinkProperties props = manager.getLinkProperties(network);
        if (props == null) {
            return out;
        }
        for (LinkAddress linkAddress : props.getLinkAddresses()) {
            InetAddress address = linkAddress.getAddress();
            if (!(address instanceof Inet4Address)) {
                continue;
            }
            String ip = address.getHostAddress();
            if (ip == null || ip.startsWith("127.") || ip.startsWith("169.254.")) {
                continue;
            }
            int dot = ip.lastIndexOf('.');
            if (dot <= 0) {
                continue;
            }
            String prefix = ip.substring(0, dot + 1);
            out.add(prefix + "1");
            out.add(prefix + "2");
            out.add(prefix + "10");
            out.add(prefix + "100");
        }
        return out;
    }

    private static boolean isHealthy(Context context, String receiverUrl) {
        HttpURLConnection conn = null;
        try {
            String healthUrl = BridgeSettings.androidBaseUrlFromReceiverUrl(receiverUrl) + "/health";
            conn = (HttpURLConnection) new URL(healthUrl).openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(1800);
            conn.setReadTimeout(2500);
            BridgeSettings.addAuthHeaders(conn, context);
            int code = conn.getResponseCode();
            InputStream input = code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream();
            if (input != null) {
                input.close();
            }
            return code >= 200 && code < 300;
        } catch (Exception ignored) {
            return false;
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private static String normalize(String receiverUrl) {
        return BridgeSettings.normalizeReceiverUrl(receiverUrl);
    }

    private static void appendTried(StringBuilder out, String receiverUrl) {
        String text = displayBase(receiverUrl);
        if (out.indexOf(text) >= 0) {
            return;
        }
        if (out.length() > 0) {
            out.append(", ");
        }
        out.append(text);
    }

    private static String displayBase(String receiverUrl) {
        String base = BridgeSettings.androidBaseUrlFromReceiverUrl(receiverUrl);
        return base.replace("/android", "");
    }

    private static void finish(Callback callback, boolean ok, String receiverUrl, String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(ok, receiverUrl, message);
            }
        });
    }
}
