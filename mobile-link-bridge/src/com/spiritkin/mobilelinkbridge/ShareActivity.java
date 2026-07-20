package com.spiritkin.mobilelinkbridge;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.TextView;
import java.util.ArrayList;

public class ShareActivity extends Activity {
    private TextView view;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        view = new TextView(this);
        view.setTextSize(16);
        view.setPadding(36, 36, 36, 36);
        setContentView(view);

        handleIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    private void handleIntent(Intent intent) {
        if (intent == null) {
            finishWithMessage("SpiritKin Android 手机端\n未收到分享内容");
            return;
        }
        String action = intent.getAction();
        String type = intent.getType() == null ? "" : intent.getType();
        if (Intent.ACTION_SEND_MULTIPLE.equals(action) && type.startsWith("image/")) {
            ArrayList<Uri> uris = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            view.setText("SpiritKin Android 手机端\n正在上传多张图片...");
            BridgeSettings.appendEvent(this, "收到多图分享");
            ArtifactSender.postImageUris(this, uris, new ArtifactSender.Callback() {
                @Override
                public void onDone(boolean ok, String message) {
                    LinkSender.toast(ShareActivity.this, message);
                    finishWithMessage("SpiritKin Android 手机端\n" + message);
                }
            });
            return;
        }
        if (Intent.ACTION_SEND.equals(action) && type.startsWith("image/")) {
            Uri uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            view.setText("SpiritKin Android 手机端\n正在上传图片...");
            BridgeSettings.appendEvent(this, "收到图片分享");
            ArtifactSender.postImageUris(this, ArtifactSender.single(uri), new ArtifactSender.Callback() {
                @Override
                public void onDone(boolean ok, String message) {
                    LinkSender.toast(ShareActivity.this, message);
                    finishWithMessage("SpiritKin Android 手机端\n" + message);
                }
            });
            return;
        }
        view.setText("SpiritKin Android 手机端\n正在回传分享文本...");
        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        BridgeSettings.appendEvent(this, "收到分享文本");
        LinkSender.postLink(this, text, new LinkSender.Callback() {
            @Override
            public void onDone(boolean ok, String message) {
                LinkSender.toast(ShareActivity.this, message);
                finishWithMessage("SpiritKin Android 手机端\n" + message);
            }
        });
    }

    private void finishWithMessage(String message) {
        view.setText(message);
        finish();
    }
}
