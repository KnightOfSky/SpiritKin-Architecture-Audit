package com.spiritkin.mobilelinkbridge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

public class HeartbeatService extends Service {
    private static final String CHANNEL_ID = "spiritkin_bridge";
    private static final int NOTIFICATION_ID = 20260614;
    private static final long INTERVAL_MS = 1000L;
    private static final long FAILURE_INTERVAL_MS = 10000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private static boolean serviceRunning = false;
    private boolean running = false;
    private boolean syncInFlight = false;

    static boolean isServiceRunning() {
        return serviceRunning;
    }

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (!running) {
                return;
            }
            if (syncInFlight) {
                handler.postDelayed(this, INTERVAL_MS);
                return;
            }
            syncInFlight = true;
            CommandSync.sync(HeartbeatService.this, new CommandSync.Callback() {
                @Override
                public void onDone(boolean ok, String message) {
                    syncInFlight = false;
                    BridgeSettings.appendEvent(HeartbeatService.this, "后台同步: " + message);
                    if (!BridgeSettings.isPaired(HeartbeatService.this)
                            || BridgeSettings.isPairingExpired(BridgeSettings.getPairingExpiresAt(HeartbeatService.this))) {
                        stopSelf();
                        return;
                    }
                    if (running) {
                        handler.postDelayed(tick, ok ? INTERVAL_MS : FAILURE_INTERVAL_MS);
                    }
                }
            });
        }
    };

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        running = true;
        serviceRunning = true;
        BridgeSettings.setHeartbeatEnabled(this, true);
        startForeground(NOTIFICATION_ID, notification("手机端正在后台同步"));
        if (!BridgeSettings.isPaired(this)
                || BridgeSettings.isPairingExpired(BridgeSettings.getPairingExpiresAt(this))) {
            BridgeSettings.setHeartbeatEnabled(this, false);
            BridgeSettings.appendEvent(this, "未绑定或绑定过期，后台同步未启动");
            stopSelf();
            return START_NOT_STICKY;
        }
        handler.removeCallbacks(tick);
        handler.post(tick);
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        serviceRunning = false;
        syncInFlight = false;
        if (!BridgeSettings.isPaired(this)
                || BridgeSettings.isPairingExpired(BridgeSettings.getPairingExpiresAt(this))) {
            BridgeSettings.setHeartbeatEnabled(this, false);
        }
        handler.removeCallbacks(tick);
        BridgeSettings.appendEvent(this, "后台同步已停止");
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void ensureChannel() {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "SpiritKin 手机端同步",
                NotificationManager.IMPORTANCE_LOW);
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.createNotificationChannel(channel);
        }
    }

    private Notification notification(String text) {
        if (Build.VERSION.SDK_INT >= 26) {
            return new Notification.Builder(this, CHANNEL_ID)
                    .setContentTitle("SpiritKin Android 手机端")
                    .setContentText(text)
                    .setSmallIcon(android.R.drawable.stat_notify_sync)
                    .setOngoing(true)
                    .build();
        }
        return new Notification.Builder(this)
                .setContentTitle("SpiritKin Android 手机端")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setOngoing(true)
                .build();
    }
}
