package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class ArtifactSender {
    private static final Pattern IMAGE_REF = Pattern.compile("(https?://[^\\s\"'<>]+\\.(?:png|jpg|jpeg|webp|gif)(?:\\?[^\\s\"'<>]*)?|[^\\s\"'<>]+\\.(?:png|jpg|jpeg|webp|gif))", Pattern.CASE_INSENSITIVE);
    private static final int MAX_IMAGE_BYTES = 20 * 1024 * 1024;

    interface Callback {
        void onDone(boolean ok, String message);
    }

    private ArtifactSender() {
    }

    static String extractImageRef(String text) {
        if (text == null) {
            return "";
        }
        Matcher matcher = IMAGE_REF.matcher(text);
        return matcher.find() ? matcher.group(1) : "";
    }

    static void postClipboardImageRef(Context context, String rawText, Callback callback) {
        String imageRef = extractImageRef(rawText);
        if (imageRef.isEmpty()) {
            finish(callback, false, "剪贴板未找到图片 URL 或图片路径");
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String deviceId = Build.MANUFACTURER + "-" + Build.MODEL;
                    String bodyText = "{\"source\":\"android_bridge\",\"device_id\":\"" + escapeJson(deviceId)
                            + "\",\"purpose\":\"android_image_ref\",\"tags\":[\"android\",\"image_ref\"],\"files\":[{\"path\":\"android-image-ref.txt\",\"mime_type\":\"text/plain\",\"text\":\""
                            + escapeJson(imageRef) + "\"}]}";
                    PostResult result = postArtifactBody(context, bodyText);
                    boolean ok = result.ok();
                    String userItem = "图片链接 · " + shorten(imageRef, 54) + (ok ? " · 已上传" : " · 失败");
                    if (ok) {
                        BridgeSettings.appendUpload(context, userItem);
                    }
                    String message = ok ? "已上传图片链接，已加入我的上传记录" : "图片链接上传失败 HTTP " + result.code;
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, ok, message);
                } catch (Exception e) {
                    String message = "图片链接登记失败: " + e.getClass().getSimpleName();
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, false, message);
                }
            }
        }).start();
    }

    static void postImageUris(Context context, List<Uri> uris, Callback callback) {
        if (uris == null || uris.isEmpty()) {
            finish(callback, false, "未收到图片");
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    StringBuilder files = new StringBuilder("[");
                    int count = 0;
                    for (Uri uri : uris) {
                        byte[] bytes = readUriBytes(context, uri);
                        if (bytes.length == 0) {
                            continue;
                        }
                        if (count > 0) {
                            files.append(',');
                        }
                        String mimeType = context.getContentResolver().getType(uri);
                        if (mimeType == null || mimeType.trim().isEmpty()) {
                            mimeType = "image/jpeg";
                        }
                        String name = "android-share-" + System.currentTimeMillis() + "-" + (count + 1) + extensionForMime(mimeType);
                        files.append("{\"name\":\"")
                                .append(escapeJson(name))
                                .append("\",\"mime_type\":\"")
                                .append(escapeJson(mimeType))
                                .append("\",\"base64\":\"")
                                .append(Base64.encodeToString(bytes, Base64.NO_WRAP))
                                .append("\"}");
                        count++;
                    }
                    files.append(']');
                    if (count == 0) {
                        finish(callback, false, "图片读取失败");
                        return;
                    }
                    String deviceId = Build.MANUFACTURER + "-" + Build.MODEL;
                    String bodyText = "{\"source\":\"android_bridge\",\"device_id\":\"" + escapeJson(deviceId)
                            + "\",\"purpose\":\"android_shared_image\",\"tags\":[\"android\",\"shared_image\"],\"files\":"
                            + files.toString() + "}";
                    PostResult result = postArtifactBody(context, bodyText);
                    boolean ok = result.ok();
                    if (ok) {
                        BridgeSettings.appendUpload(context, "图片文件 · " + count + " 张 · 已上传");
                    }
                    String message = ok ? "已上传图片 " + count + " 张，已加入我的上传记录" : "图片上传失败 HTTP " + result.code;
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, ok, message);
                } catch (Exception e) {
                    String message = "图片上传失败: " + e.getClass().getSimpleName();
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, false, message);
                }
            }
        }).start();
    }

    static int postTextArtifact(Context context, String name, String purpose, String text) throws Exception {
        return postTextArtifactResult(context, name, purpose, text).code;
    }

    static PostResult postTextArtifactResult(Context context, String name, String purpose, String text) throws Exception {
        String deviceId = Build.MANUFACTURER + "-" + Build.MODEL;
        String bodyText = "{\"source\":\"android_bridge\",\"device_id\":\"" + escapeJson(deviceId)
                + "\",\"workspace_id\":\"" + escapeJson(BridgeSettings.getWorkspaceId(context))
                + "\",\"purpose\":\"" + escapeJson(purpose)
                + "\",\"tags\":[\"android\",\"" + escapeJson(purpose) + "\"],\"files\":[{\"name\":\""
                + escapeJson(name)
                + "\",\"mime_type\":\"text/plain\",\"text\":\""
                + escapeJson(text) + "\"}]}";
        return postArtifactBody(context, bodyText);
    }

    static PostResult postArtifactJson(Context context, String bodyText) throws Exception {
        return postArtifactBody(context, bodyText);
    }

    static List<Uri> single(Uri uri) {
        ArrayList<Uri> result = new ArrayList<Uri>();
        if (uri != null) {
            result.add(uri);
        }
        return result;
    }

    private static PostResult postArtifactBody(Context context, String bodyText) throws Exception {
        byte[] body = bodyText.getBytes(StandardCharsets.UTF_8);
        try {
            return postArtifactBodyOnce(context, body);
        } catch (Exception first) {
            if (!discoverReceiverBlocking(context)) {
                throw first;
            }
            return postArtifactBodyOnce(context, body);
        }
    }

    private static PostResult postArtifactBodyOnce(Context context, byte[] body) throws Exception {
        BridgeHttpClient.HttpResult result = BridgeHttpClient.postJson(context, BridgeSettings.getArtifactUrl(context), body, 5000, 10000);
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

    private static byte[] readUriBytes(Context context, Uri uri) throws Exception {
        InputStream input = context.getContentResolver().openInputStream(uri);
        if (input == null) {
            return new byte[0];
        }
        try {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int total = 0;
            int read;
            while ((read = input.read(buffer)) != -1) {
                total += read;
                if (total > MAX_IMAGE_BYTES) {
                    throw new IllegalArgumentException("image too large");
                }
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        } finally {
            input.close();
        }
    }

    private static String extensionForMime(String mimeType) {
        String value = String.valueOf(mimeType == null ? "" : mimeType).toLowerCase();
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

    private static void finish(Callback callback, boolean ok, String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(ok, message);
            }
        });
    }

    private static String artifactSummary(String body, String fallbackRef) {
        String artifactId = jsonField(body, "artifact_id");
        String workspaceId = jsonField(body, "workspace_id");
        String downloadUrl = jsonField(body, "download_url");
        String fileName = jsonField(body, "name");
        String relativePath = jsonField(body, "relative_path");
        StringBuilder out = new StringBuilder();
        if (!artifactId.isEmpty()) {
            out.append("素材编号 ").append(shorten(artifactId, 28));
        }
        if (!workspaceId.isEmpty()) {
            appendPart(out, "工作区 " + workspaceId);
        }
        if (!fileName.isEmpty()) {
            appendPart(out, "文件 " + fileName);
        }
        if (!relativePath.isEmpty()) {
            appendPart(out, "路径 " + relativePath);
        }
        if (!downloadUrl.isEmpty()) {
            appendPart(out, "下载 " + downloadUrl);
        }
        if (out.length() == 0 && fallbackRef != null && !fallbackRef.trim().isEmpty()) {
            out.append(fallbackRef.trim());
        }
        return out.length() == 0 ? "已进入云端图片库" : out.toString();
    }

    private static String shorten(String value, int max) {
        String text = value == null ? "" : value.trim();
        if (text.length() <= max) {
            return text;
        }
        return text.substring(0, Math.max(0, max - 1)) + "...";
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
        return String.valueOf(value == null ? "" : value).replace("\\", "\\\\").replace("\"", "\\\"");
    }

    static final class PostResult {
        final int code;
        final String body;

        PostResult(int code, String body) {
            this.code = code;
            this.body = body == null ? "" : body;
        }

        boolean ok() {
            return code >= 200 && code < 300;
        }
    }
}
