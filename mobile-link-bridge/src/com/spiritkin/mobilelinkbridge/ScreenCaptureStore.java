package com.spiritkin.mobilelinkbridge;

import android.content.Context;
import android.content.Intent;
import android.os.Build;

final class ScreenCaptureStore {
    static final String ACTION_CAPTURE_READY = "com.spiritkin.mobilelinkbridge.SCREEN_CAPTURE_READY";
    static final String ACTION_START_SESSION = "com.spiritkin.mobilelinkbridge.START_CAPTURE_SESSION";
    static final String ACTION_CAPTURE_SCREENSHOT = "com.spiritkin.mobilelinkbridge.CAPTURE_SCREENSHOT";
    static final String ACTION_STOP_SESSION = "com.spiritkin.mobilelinkbridge.STOP_CAPTURE_SESSION";
    static final String EXTRA_RESULT_CODE = "result_code";
    static final String EXTRA_RESULT_DATA = "result_data";

    private static int resultCode = 0;
    private static Intent resultData = null;

    private ScreenCaptureStore() {
    }

    static synchronized void setAuthorization(int code, Intent data) {
        resultCode = code;
        resultData = data == null ? null : new Intent(data);
    }

    static synchronized boolean isAuthorized() {
        return ScreenCaptureService.isSessionActive();
    }

    static synchronized boolean hasAuthorizationToken() {
        return resultCode != 0 && resultData != null;
    }

    static synchronized void clearAuthorization() {
        resultCode = 0;
        resultData = null;
    }

    static synchronized int resultCode() {
        return resultCode;
    }

    static synchronized Intent resultData() {
        return resultData == null ? null : new Intent(resultData);
    }

    static void requestCapture(Context context) {
        Intent intent = new Intent(context, ScreenCaptureActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
    }

    static void startCaptureSession(Context context, int code, Intent data) {
        Intent intent = new Intent(context, ScreenCaptureService.class);
        intent.setAction(ACTION_START_SESSION);
        intent.putExtra(EXTRA_RESULT_CODE, code);
        intent.putExtra(EXTRA_RESULT_DATA, data == null ? null : new Intent(data));
        startService(context, intent);
    }

    static void captureScreenshot(Context context) {
        Intent intent = new Intent(context, ScreenCaptureService.class);
        intent.setAction(ACTION_CAPTURE_SCREENSHOT);
        startService(context, intent);
    }

    static void stopCaptureSession(Context context) {
        Intent intent = new Intent(context, ScreenCaptureService.class);
        intent.setAction(ACTION_STOP_SESSION);
        startService(context, intent);
    }

    private static void startService(Context context, Intent intent) {
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }
}
