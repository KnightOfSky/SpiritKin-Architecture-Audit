package com.spiritkin.mobilelinkbridge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Base64;
import android.view.WindowManager;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

public class ScreenCaptureService extends Service {
    private static final String CHANNEL_ID = "spiritkin_screen_capture";
    private static final int NOTIFICATION_ID = 7302;
    private static volatile boolean sessionActive = false;

    private final Object sessionLock = new Object();
    private Handler mainHandler;
    private MediaProjection projection;
    private VirtualDisplay display;
    private ImageReader reader;
    private int sessionWidth;
    private int sessionHeight;

    static boolean isSessionActive() {
        return sessionActive;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        mainHandler = new Handler(Looper.getMainLooper());
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(NOTIFICATION_ID, notification());
        String action = intent == null ? "" : intent.getAction();
        if (ScreenCaptureStore.ACTION_START_SESSION.equals(action)) {
            int resultCode = intent.getIntExtra(ScreenCaptureStore.EXTRA_RESULT_CODE, 0);
            Intent resultData = intent.getParcelableExtra(ScreenCaptureStore.EXTRA_RESULT_DATA);
            boolean ok = startSession(resultCode, resultData);
            if (!ok) {
                stopForeground(true);
                stopSelf(startId);
                return START_NOT_STICKY;
            }
            return START_STICKY;
        }
        if (ScreenCaptureStore.ACTION_CAPTURE_SCREENSHOT.equals(action)) {
            if (!isSessionActive()) {
                BridgeSettings.appendEvent(this, "屏幕截图失败: 会话未授权或已失效，请先请求授权");
                stopForeground(true);
                stopSelf(startId);
                return START_NOT_STICKY;
            }
            new Thread(new Runnable() {
                @Override
                public void run() {
                    captureAndUpload();
                }
            }, "SpiritKinScreenCapture").start();
            return START_STICKY;
        }
        if (ScreenCaptureStore.ACTION_STOP_SESSION.equals(action)) {
            stopSession("屏幕截图会话已停止", true);
            stopForeground(true);
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        return isSessionActive() ? START_STICKY : START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        stopSession("", true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification notification() {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "SpiritKin Screen Capture", NotificationManager.IMPORTANCE_LOW);
            NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
            return new Notification.Builder(this, CHANNEL_ID)
                    .setContentTitle("SpiritKin 屏幕截图会话")
                    .setContentText("保持授权以便主控上传诊断截图")
                    .setSmallIcon(android.R.drawable.ic_menu_camera)
                    .build();
        }
        return new Notification.Builder(this)
                .setContentTitle("SpiritKin 屏幕截图会话")
                .setContentText("保持授权以便主控上传诊断截图")
                .setSmallIcon(android.R.drawable.ic_menu_camera)
                .build();
    }

    private boolean startSession(int resultCode, Intent data) {
        if (resultCode == 0 || data == null) {
            BridgeSettings.appendEvent(this, "屏幕截图会话启动失败: 授权数据为空");
            return false;
        }
        stopSession("", true);
        try {
            MediaProjectionManager manager = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
            if (manager == null) {
                BridgeSettings.appendEvent(this, "屏幕截图会话启动失败: MediaProjection 不可用");
                return false;
            }
            WindowManager windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
            int width = getResources().getDisplayMetrics().widthPixels;
            int height = getResources().getDisplayMetrics().heightPixels;
            int density = getResources().getDisplayMetrics().densityDpi;
            if (windowManager == null || width <= 0 || height <= 0) {
                BridgeSettings.appendEvent(this, "屏幕截图会话启动失败: 屏幕尺寸不可用");
                return false;
            }
            MediaProjection nextProjection = manager.getMediaProjection(resultCode, data);
            if (nextProjection == null) {
                BridgeSettings.appendEvent(this, "屏幕截图会话启动失败: 授权已失效");
                return false;
            }
            ImageReader nextReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3);
            nextProjection.registerCallback(new MediaProjection.Callback() {
                @Override
                public void onStop() {
                    Handler handler = mainHandler == null ? new Handler(Looper.getMainLooper()) : mainHandler;
                    handler.post(new Runnable() {
                        @Override
                        public void run() {
                            onProjectionStopped();
                        }
                    });
                }
            }, mainHandler == null ? new Handler(Looper.getMainLooper()) : mainHandler);
            VirtualDisplay nextDisplay = nextProjection.createVirtualDisplay(
                    "SpiritKinScreenCapture",
                    width,
                    height,
                    density,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    nextReader.getSurface(),
                    null,
                    mainHandler == null ? new Handler(Looper.getMainLooper()) : mainHandler);
            synchronized (sessionLock) {
                projection = nextProjection;
                reader = nextReader;
                display = nextDisplay;
                sessionWidth = width;
                sessionHeight = height;
                sessionActive = true;
            }
            BridgeSettings.appendEvent(this, "屏幕截图会话已就绪，可直接上传截图");
            syncStateSoon();
            return true;
        } catch (Exception e) {
            stopSession("", false);
            BridgeSettings.appendEvent(this, "屏幕截图会话启动失败: " + e.getClass().getSimpleName());
            syncStateSoon();
            return false;
        }
    }

    private void onProjectionStopped() {
        synchronized (sessionLock) {
            if (!sessionActive && projection == null && display == null && reader == null) {
                return;
            }
        }
        stopSession("屏幕截图会话已结束，需要重新授权", false);
        syncStateSoon();
        stopForeground(true);
        stopSelf();
    }

    private void stopSession(String message, boolean stopProjection) {
        MediaProjection oldProjection;
        VirtualDisplay oldDisplay;
        ImageReader oldReader;
        synchronized (sessionLock) {
            oldProjection = projection;
            oldDisplay = display;
            oldReader = reader;
            projection = null;
            display = null;
            reader = null;
            sessionWidth = 0;
            sessionHeight = 0;
            sessionActive = false;
            ScreenCaptureStore.clearAuthorization();
        }
        if (oldDisplay != null) {
            oldDisplay.release();
        }
        if (oldReader != null) {
            oldReader.close();
        }
        if (stopProjection && oldProjection != null) {
            oldProjection.stop();
        }
        if (message != null && !message.trim().isEmpty()) {
            BridgeSettings.appendEvent(this, message);
        }
    }

    private void captureAndUpload() {
        ImageReader activeReader;
        int width;
        int height;
        synchronized (sessionLock) {
            activeReader = reader;
            width = sessionWidth;
            height = sessionHeight;
        }
        if (!isSessionActive() || activeReader == null || width <= 0 || height <= 0) {
            BridgeSettings.appendEvent(this, "屏幕截图失败: 会话未授权或已失效");
            return;
        }
        Image image = null;
        try {
            for (int i = 0; i < 6 && image == null; i++) {
                Thread.sleep(250L);
                image = activeReader.acquireLatestImage();
            }
            if (image == null) {
                BridgeSettings.appendEvent(this, "屏幕截图失败: 没有图像");
                return;
            }
            byte[] png = imageToPng(image, width, height);
            uploadPng(png);
        } catch (Exception e) {
            BridgeSettings.appendEvent(this, "屏幕截图失败: " + e.getClass().getSimpleName());
        } finally {
            if (image != null) {
                image.close();
            }
            syncStateSoon();
        }
    }

    private byte[] imageToPng(Image image, int width, int height) throws Exception {
        Image.Plane plane = image.getPlanes()[0];
        ByteBuffer buffer = plane.getBuffer();
        int pixelStride = plane.getPixelStride();
        int rowStride = plane.getRowStride();
        int rowPadding = rowStride - pixelStride * width;
        Bitmap bitmap = Bitmap.createBitmap(width + rowPadding / pixelStride, height, Bitmap.Config.ARGB_8888);
        bitmap.copyPixelsFromBuffer(buffer);
        Bitmap cropped = Bitmap.createBitmap(bitmap, 0, 0, width, height);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        cropped.compress(Bitmap.CompressFormat.PNG, 100, out);
        bitmap.recycle();
        cropped.recycle();
        return out.toByteArray();
    }

    private void uploadPng(byte[] png) throws Exception {
        String body = "{\"source\":\"android_bridge\",\"device_id\":\"" + escapeJson(Build.MANUFACTURER + "-" + Build.MODEL)
                + "\",\"workspace_id\":\"" + escapeJson(BridgeSettings.getWorkspaceId(this))
                + "\",\"purpose\":\"android_screenshot\",\"tags\":[\"android\",\"screenshot\",\"mediaprojection\"],\"files\":[{\"name\":\"android-screenshot-"
                + System.currentTimeMillis()
                + ".png\",\"mime_type\":\"image/png\",\"base64\":\""
                + Base64.encodeToString(png, Base64.NO_WRAP)
                + "\"}]}";
        ArtifactSender.PostResult result = ArtifactSender.postArtifactJson(this, body);
        BridgeSettings.appendEvent(this, result.ok() ? "已上传屏幕截图" : "屏幕截图上传失败 HTTP " + result.code);
    }

    private void syncStateSoon() {
        CommandSync.sync(this, null);
    }

    private static String escapeJson(String value) {
        return String.valueOf(value == null ? "" : value).replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
