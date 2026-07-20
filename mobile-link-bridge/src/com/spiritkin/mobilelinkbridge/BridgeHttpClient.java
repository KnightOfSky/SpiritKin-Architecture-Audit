package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class BridgeHttpClient {
    private BridgeHttpClient() {
    }

    static HttpResult getText(Context context, String url) throws Exception {
        HttpURLConnection conn = open(context, url, "GET", 5000, 10000);
        int code = conn.getResponseCode();
        String body = readText(code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream());
        return new HttpResult(code, body);
    }

    static HttpResult getBytes(Context context, String url) throws Exception {
        HttpURLConnection conn = open(context, url, "GET", 5000, 10000);
        int code = conn.getResponseCode();
        byte[] body = readBytes(code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream());
        return new HttpResult(code, body);
    }

    static HttpResult postJson(Context context, String url, String bodyText) throws Exception {
        return postJson(context, url, bodyText == null ? new byte[0] : bodyText.getBytes(StandardCharsets.UTF_8), 5000, 10000);
    }

    static HttpResult postJson(Context context, String url, byte[] body, int connectTimeoutMs, int readTimeoutMs) throws Exception {
        byte[] payload = body == null ? new byte[0] : body;
        HttpURLConnection conn = open(context, url, "POST", connectTimeoutMs, readTimeoutMs);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Content-Length", String.valueOf(payload.length));
        OutputStream out = conn.getOutputStream();
        try {
            out.write(payload);
        } finally {
            out.close();
        }
        int code = conn.getResponseCode();
        String response = readText(code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream());
        return new HttpResult(code, response);
    }

    static String readText(InputStream input) throws Exception {
        if (input == null) {
            return "";
        }
        try {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[2048];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            input.close();
        }
    }

    private static HttpURLConnection open(Context context, String url, String method, int connectTimeoutMs, int readTimeoutMs) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod(method);
        conn.setConnectTimeout(connectTimeoutMs);
        conn.setReadTimeout(readTimeoutMs);
        BridgeSettings.addAuthHeaders(conn, context);
        return conn;
    }

    private static byte[] readBytes(InputStream input) throws Exception {
        if (input == null) {
            return new byte[0];
        }
        try {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        } finally {
            input.close();
        }
    }

    static final class HttpResult {
        final int code;
        final String body;
        final byte[] bytes;

        HttpResult(int code, String body) {
            this.code = code;
            this.body = body == null ? "" : body;
            this.bytes = new byte[0];
        }

        HttpResult(int code, byte[] bytes) {
            this.code = code;
            this.body = "";
            this.bytes = bytes == null ? new byte[0] : bytes;
        }

        boolean ok() {
            return code >= 200 && code < 300;
        }
    }
}
