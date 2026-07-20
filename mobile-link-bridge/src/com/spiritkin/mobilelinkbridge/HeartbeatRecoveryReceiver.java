package com.spiritkin.mobilelinkbridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

public class HeartbeatRecoveryReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (context == null) {
            return;
        }
        if (!BridgeSettings.isPaired(context)
                || BridgeSettings.isPairingExpired(BridgeSettings.getPairingExpiresAt(context))) {
            BridgeSettings.setHeartbeatEnabled(context, false);
            return;
        }
        BridgeSettings.appendEvent(context, "系统事件触发后台同步恢复");
        Intent service = new Intent(context, HeartbeatService.class);
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                context.startForegroundService(service);
            } else {
                context.startService(service);
            }
            BridgeSettings.setHeartbeatEnabled(context, true);
        } catch (RuntimeException e) {
            BridgeSettings.appendEvent(context, "后台同步恢复被系统限制，请打开 App 或允许后台运行");
        }
    }
}
