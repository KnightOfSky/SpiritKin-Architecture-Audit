package com.spiritkin.mobilelinkbridge;

import android.app.Activity;
import android.content.Intent;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;

public class ScreenCaptureActivity extends Activity {
    private static final int REQUEST_MEDIA_PROJECTION = 7301;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        MediaProjectionManager manager = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        if (manager == null) {
            BridgeSettings.appendEvent(this, "屏幕截图授权不可用");
            finish();
            return;
        }
        startActivityForResult(manager.createScreenCaptureIntent(), REQUEST_MEDIA_PROJECTION);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_MEDIA_PROJECTION && resultCode == RESULT_OK && data != null) {
            ScreenCaptureStore.setAuthorization(resultCode, data);
            ScreenCaptureStore.startCaptureSession(this, resultCode, data);
            BridgeSettings.appendEvent(this, "已授权屏幕截图，会话启动中");
            CommandSync.sync(this, null);
            sendBroadcast(new Intent(ScreenCaptureStore.ACTION_CAPTURE_READY));
        } else {
            BridgeSettings.appendEvent(this, "屏幕截图授权已取消");
        }
        finish();
    }
}
