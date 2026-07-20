package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class AppUpdater {
    interface Callback {
        void onDone(boolean ok, String message);
    }

    private static final Pattern VERSION_CODE = Pattern.compile("\\\"version_code\\\"\\s*:\\s*(\\d+)");
    private static final Pattern VERSION_NAME = Pattern.compile("\\\"version_name\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    private static final Pattern DOWNLOAD_URL = Pattern.compile("\\\"download_url\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    private static final Pattern PACKAGE_NAME = Pattern.compile("\\\"package_name\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    private static final Pattern APP_ID = Pattern.compile("\\\"app_id\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    private static final Pattern SHA256 = Pattern.compile("\\\"sha256\\\"\\s*:\\s*\\\"([A-Fa-f0-9]{64})\\\"");
    private static final Pattern SIZE_BYTES = Pattern.compile("\\\"size_bytes\\\"\\s*:\\s*(\\d+)");
    private static final Pattern MIN_SDK = Pattern.compile("\\\"min_sdk\\\"\\s*:\\s*(\\d+)");
    private static final Pattern MAX_SDK = Pattern.compile("\\\"max_sdk\\\"\\s*:\\s*(\\d+)");
    private static final int MAX_APK_BYTES = 40 * 1024 * 1024;

    private AppUpdater() {
    }

    static void checkAndInstall(Context context, Callback callback) {
        ReceiverDiscovery.findAndSave(context, new ReceiverDiscovery.Callback() {
            @Override
            public void onDone(boolean ok, String receiverUrl, String message) {
                if (!ok) {
                    finish(callback, false, "更新失败: " + message);
                    return;
                }
                checkAndInstallFromCurrentReceiver(context, callback);
            }
        });
    }

    private static void checkAndInstallFromCurrentReceiver(Context context, Callback callback) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    UpdateInfo update = fetchUpdate(context);
                    if (update.versionCode <= currentVersionCode(context)) {
                        finish(callback, true, "已是最新版本: " + currentVersionName(context) + "\nManifest: " + update.manifestUrl);
                        return;
                    }
                    verifyCompatibility(context, update);
                    File apk = downloadApk(context, update);
                    verifyDownloadedApk(context, apk, update);
                    BridgeSettings.appendEvent(context, "已下载更新 " + update.versionName + " · " + apk.getName());
                    finish(callback, true, openInstaller(context, apk, update));
                } catch (Exception e) {
                    String message = "更新失败: " + (e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
                    BridgeSettings.appendEvent(context, message);
                    finish(callback, false, message);
                }
            }
        }).start();
    }

    private static UpdateInfo fetchUpdate(Context context) throws Exception {
        String manifestUrl = updateManifestUrl(context);
        HttpURLConnection conn = (HttpURLConnection) new URL(manifestUrl).openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(10000);
        BridgeSettings.addAuthHeaders(conn, context);
        int code = conn.getResponseCode();
        String body = readResponse(code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream());
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("manifest HTTP " + code + " · " + manifestUrl);
        }
        String downloadUrl = field(DOWNLOAD_URL, body);
        int versionCode = intField(VERSION_CODE, body);
        String versionName = field(VERSION_NAME, body);
        String packageName = field(PACKAGE_NAME, body);
        if (packageName.isEmpty()) {
            packageName = field(APP_ID, body);
        }
        String sha256 = field(SHA256, body).toLowerCase(Locale.US);
        long sizeBytes = longField(SIZE_BYTES, body);
        int minSdk = intField(MIN_SDK, body);
        int maxSdk = intField(MAX_SDK, body);
        if (downloadUrl.isEmpty() || versionCode <= 0) {
            throw new IllegalStateException("invalid update manifest · " + manifestUrl);
        }
        return new UpdateInfo(versionCode, versionName, downloadUrl, manifestUrl, packageName, sha256, sizeBytes, minSdk, maxSdk);
    }

    private static File downloadApk(Context context, UpdateInfo update) throws Exception {
        File dir = new File(context.getCacheDir(), "spiritkin-artifacts");
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IllegalStateException("cache mkdir failed");
        }
        File file = new File(dir, "spiritkin-control-bridge-update-" + update.versionCode + ".apk");
        HttpURLConnection conn = (HttpURLConnection) new URL(update.downloadUrl).openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(5000);
        conn.setReadTimeout(30000);
        BridgeSettings.addAuthHeaders(conn, context);
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            InputStream error = conn.getErrorStream();
            if (error != null) {
                error.close();
            }
            throw new IllegalStateException("download HTTP " + code + " · " + update.downloadUrl);
        }
        InputStream input = conn.getInputStream();
        FileOutputStream output = new FileOutputStream(file);
        try {
            byte[] buffer = new byte[8192];
            int total = 0;
            int read;
            while ((read = input.read(buffer)) != -1) {
                total += read;
                if (total > MAX_APK_BYTES) {
                    throw new IllegalStateException("apk too large");
                }
                output.write(buffer, 0, read);
            }
        } finally {
            output.close();
            input.close();
        }
        return file;
    }

    private static void verifyCompatibility(Context context, UpdateInfo update) throws Exception {
        String expectedPackage = update.packageName.isEmpty() ? context.getPackageName() : update.packageName;
        if (!context.getPackageName().equals(expectedPackage)) {
            throw new IllegalStateException("manifest package mismatch: " + expectedPackage);
        }
        if (update.minSdk > 0 && Build.VERSION.SDK_INT < update.minSdk) {
            throw new IllegalStateException("Android " + Build.VERSION.SDK_INT + " too old, requires API " + update.minSdk);
        }
        if (update.maxSdk > 0 && Build.VERSION.SDK_INT > update.maxSdk) {
            throw new IllegalStateException("Android " + Build.VERSION.SDK_INT + " unsupported, max API " + update.maxSdk);
        }
        if (update.sizeBytes > 0 && update.sizeBytes > MAX_APK_BYTES) {
            throw new IllegalStateException("manifest apk too large");
        }
    }

    private static void verifyDownloadedApk(Context context, File apk, UpdateInfo update) throws Exception {
        if (update.sizeBytes > 0 && apk.length() != update.sizeBytes) {
            throw new IllegalStateException("apk size mismatch");
        }
        if (!update.sha256.isEmpty()) {
            String actual = sha256(apk);
            if (!actual.equalsIgnoreCase(update.sha256)) {
                throw new IllegalStateException("apk sha256 mismatch");
            }
        }
        PackageInfo info = context.getPackageManager().getPackageArchiveInfo(apk.getAbsolutePath(), 0);
        if (info == null) {
            throw new IllegalStateException("downloaded apk is not installable");
        }
        String expectedPackage = update.packageName.isEmpty() ? context.getPackageName() : update.packageName;
        if (!expectedPackage.equals(info.packageName)) {
            throw new IllegalStateException("downloaded package mismatch: " + info.packageName);
        }
        if (archiveVersionCode(info) != update.versionCode) {
            throw new IllegalStateException("downloaded version mismatch");
        }
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        InputStream input = new java.io.FileInputStream(file);
        try {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
        } finally {
            input.close();
        }
        byte[] bytes = digest.digest();
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            out.append(String.format(Locale.US, "%02x", b & 0xff));
        }
        return out.toString();
    }

    private static String openInstaller(Context context, File apk, UpdateInfo update) {
        if (Build.VERSION.SDK_INT >= 26 && !context.getPackageManager().canRequestPackageInstalls()) {
            Intent browser = new Intent(Intent.ACTION_VIEW, Uri.parse(update.downloadUrl));
            browser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                startActivity(context, browser);
                return "系统仍未授予本应用安装权限，已改用浏览器下载: " + update.versionName;
            } catch (Exception ignored) {
                Intent settings = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES);
                settings.setData(Uri.parse("package:" + context.getPackageName()));
                settings.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(context, settings);
                return "已下载 " + update.versionName + "，请允许本应用安装未知应用；若仍返回本页，请用浏览器打开 " + update.downloadUrl;
            }
        }
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(CacheFileProvider.uriForFile(context, apk), "application/vnd.android.package-archive");
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        startActivity(context, intent);
        return "已下载并打开系统安装器: " + update.versionName;
    }

    private static void startActivity(Context context, Intent intent) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                context.startActivity(intent);
            }
        });
    }

    private static String updateManifestUrl(Context context) {
        return BridgeSettings.getAndroidBaseUrl(context) + "/apk/manifest";
    }

    private static long currentVersionCode(Context context) throws Exception {
        PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
        return archiveVersionCode(info);
    }

    private static long archiveVersionCode(PackageInfo info) {
        if (Build.VERSION.SDK_INT >= 28) {
            return info.getLongVersionCode();
        }
        return info.versionCode;
    }

    private static String currentVersionName(Context context) throws Exception {
        return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).versionName;
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

    private static String field(Pattern pattern, String text) {
        Matcher matcher = pattern.matcher(text == null ? "" : text);
        return matcher.find() ? matcher.group(1) : "";
    }

    private static int intField(Pattern pattern, String text) {
        String value = field(pattern, text);
        return value.isEmpty() ? 0 : Integer.parseInt(value);
    }

    private static long longField(Pattern pattern, String text) {
        String value = field(pattern, text);
        return value.isEmpty() ? 0 : Long.parseLong(value);
    }

    private static void finish(Callback callback, boolean ok, String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                callback.onDone(ok, message);
            }
        });
    }

    private static final class UpdateInfo {
        final int versionCode;
        final String versionName;
        final String downloadUrl;
        final String manifestUrl;
        final String packageName;
        final String sha256;
        final long sizeBytes;
        final int minSdk;
        final int maxSdk;

        UpdateInfo(
                int versionCode,
                String versionName,
                String downloadUrl,
                String manifestUrl,
                String packageName,
                String sha256,
                long sizeBytes,
                int minSdk,
                int maxSdk) {
            this.versionCode = versionCode;
            this.versionName = versionName == null || versionName.isEmpty() ? String.valueOf(versionCode) : versionName;
            this.downloadUrl = downloadUrl;
            this.manifestUrl = manifestUrl;
            this.packageName = packageName == null ? "" : packageName;
            this.sha256 = sha256 == null ? "" : sha256;
            this.sizeBytes = sizeBytes;
            this.minSdk = minSdk;
            this.maxSdk = maxSdk;
        }
    }
}
