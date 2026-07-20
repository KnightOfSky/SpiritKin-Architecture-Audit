package com.spiritkin.mobilelinkbridge;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.graphics.Bitmap;
import android.graphics.Typeface;
import android.graphics.drawable.StateListDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.os.Build;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.List;

public class MainActivity extends Activity {
    // Generated from design/tokens.json v4 through values/colors.xml and values-night/colors.xml.
    private int BG;
    private int CARD;
    private int TEXT;
    private int MUTED;
    private int LINE;
    private int PRIMARY;
    private int OK;
    // 收敛后的散落色（原为内联 Color.rgb 裸值）。
    private int HERO_BG;
    private int HERO_EYEBROW;
    private int HERO_TITLE;
    private int HERO_SUBTITLE;
    private int HERO_VERSION;
    private int ROW_ALT;
    private int PREVIEW_BG;
    private int BTN_FACE;
    private int BTN_FACE_PRESSED;
    private int BTN_PRIMARY_PRESSED;
    private int BTN_DISABLED;
    private int PILL_TEXT;

    private int themeColor(int resourceId) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return getResources().getColor(resourceId, getTheme());
        }
        return getResources().getColor(resourceId);
    }

    private void initPalette() {
        BG = themeColor(R.color.fantasy_canvas);
        CARD = themeColor(R.color.fantasy_surface);
        TEXT = themeColor(R.color.fantasy_text);
        MUTED = themeColor(R.color.fantasy_muted);
        LINE = themeColor(R.color.fantasy_line);
        PRIMARY = themeColor(R.color.fantasy_accent);
        OK = themeColor(R.color.fantasy_success_fg);
        HERO_BG = themeColor(R.color.fantasy_surface_2);
        HERO_EYEBROW = themeColor(R.color.fantasy_copper);
        HERO_TITLE = TEXT;
        HERO_SUBTITLE = MUTED;
        HERO_VERSION = themeColor(R.color.fantasy_faint);
        ROW_ALT = themeColor(R.color.fantasy_surface_2);
        PREVIEW_BG = themeColor(R.color.fantasy_surface_3);
        BTN_FACE = themeColor(R.color.fantasy_surface_2);
        BTN_FACE_PRESSED = themeColor(R.color.fantasy_surface_3);
        BTN_PRIMARY_PRESSED = themeColor(R.color.fantasy_accent_2);
        BTN_DISABLED = themeColor(R.color.fantasy_line);
        PILL_TEXT = themeColor(R.color.fantasy_on_accent);
    }

    private EditText urlInput;
    private EditText workspaceInput;
    private EditText tokenInput;
    private TextView statusView;
    private TextView recentView;
    private TextView receiverSummaryView;
    private TextView workerSummaryView;
    private TextView workerCapabilityView;
    private TextView pddAutomationView;
    private Button pairButton;
    private TextView uploadHistoryView;
    private TextView cloudUploadSummaryView;
    private LinearLayout cloudUploadList;
    private TextView linkHistoryView;
    private TextView cloudLinkSummaryView;
    private LinearLayout cloudLinkList;
    private Button ecommerceWorkflowHeader;
    private LinearLayout ecommerceWorkflowBody;
    private Button imageModuleHeader;
    private LinearLayout imageModuleBody;
    private Button linkModuleHeader;
    private LinearLayout linkModuleBody;
    private Button pddModuleHeader;
    private LinearLayout pddModuleBody;
    private Button commonWorkflowHeader;
    private LinearLayout commonWorkflowBody;
    private Button diagnosticsHeader;
    private LinearLayout diagnosticsBody;
    private Button connectionSettingsHeader;
    private LinearLayout connectionSettingsBody;
    private Button recentHeader;
    private LinearLayout recentBody;
    private ScrollView[] navigationPages;
    private Button[] navigationButtons;
    private int selectedNavigationPage;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        selectedNavigationPage = savedInstanceState == null ? 0 : savedInstanceState.getInt("selected_navigation_page", 0);
        initPalette();
        buildUi();
        handlePairingIntent(getIntent());
        refresh();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handlePairingIntent(intent);
    }

    @Override
    protected void onResume() {
        super.onResume();
        ensureBackgroundSync();
        refresh();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        outState.putInt("selected_navigation_page", selectedNavigationPage);
        super.onSaveInstanceState(outState);
    }

    private void buildUi() {
        LinearLayout appRoot = new LinearLayout(this);
        appRoot.setOrientation(LinearLayout.VERTICAL);
        appRoot.setBackgroundColor(BG);

        FrameLayout pageHost = new FrameLayout(this);
        appRoot.addView(pageHost, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1f));

        LinearLayout statusRoot = pageRoot();
        LinearLayout workflowRoot = pageRoot();
        LinearLayout connectionRoot = pageRoot();
        addPageHeader(workflowRoot, "工作流", "管理自动化、商品素材、链接和设备诊断");
        addPageHeader(connectionRoot, "连接", "绑定主控、管理访问凭据和后台同步");

        navigationPages = new ScrollView[] {
                pageScroll(statusRoot),
                pageScroll(workflowRoot),
                pageScroll(connectionRoot)
        };
        for (ScrollView page : navigationPages) {
            pageHost.addView(page, new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT));
        }

        LinearLayout navigationBar = new LinearLayout(this);
        navigationBar.setOrientation(LinearLayout.HORIZONTAL);
        navigationBar.setGravity(Gravity.CENTER);
        navigationBar.setPadding(dp(8), dp(4), dp(8), dp(6));
        navigationBar.setBackgroundColor(CARD);
        navigationButtons = new Button[] {
                navigationButton("状态", android.R.drawable.ic_menu_info_details, 0),
                navigationButton("工作流", android.R.drawable.ic_menu_agenda, 1),
                navigationButton("连接", android.R.drawable.ic_menu_manage, 2)
        };
        for (Button button : navigationButtons) {
            navigationBar.addView(button, weightWrap());
        }
        appRoot.addView(navigationBar, matchWrap());

        LinearLayout hero = card();
        hero.setBackgroundColor(HERO_BG);
        hero.setPadding(dp(18), dp(18), dp(18), dp(16));
        statusRoot.addView(hero, matchWrap());

        TextView eyebrow = label("手机执行端", HERO_EYEBROW, 11, true);
        hero.addView(eyebrow, matchWrap());

        TextView title = label("SpiritKin Android 手机端", HERO_TITLE, 25, true);
        title.setPadding(0, dp(6), 0, dp(2));
        hero.addView(title, matchWrap());

        TextView subtitle = label("手机执行端：同步主控命令、上传商品素材、配合自动化工作流", HERO_SUBTITLE, 13, false);
        subtitle.setPadding(0, dp(4), 0, dp(12));
        hero.addView(subtitle, matchWrap());

        LinearLayout statusRow = new LinearLayout(this);
        statusRow.setOrientation(LinearLayout.HORIZONTAL);
        hero.addView(statusRow, matchWrap());
        statusRow.addView(statusPill("主控同步", OK), weightWrap());
        statusRow.addView(statusPill("无线调试可选", PRIMARY), weightWrap());

        TextView version = label("版本 " + appVersion(), HERO_VERSION, 12, false);
        version.setPadding(0, dp(12), 0, 0);
        hero.addView(version, matchWrap());

        LinearLayout receiverCard = section("连接主控");
        connectionRoot.addView(receiverCard, sectionParams());

        receiverSummaryView = label("--", TEXT, 14, true);
        receiverSummaryView.setPadding(0, 0, 0, dp(10));
        receiverCard.addView(receiverSummaryView, matchWrap());

        TextView receiverIntro = label("手机绑定到工作区后会自动后台同步主控命令；主控端下发工作流、截图授权、PDD 操作后，本机无需手动点同步。", MUTED, 13, false);
        receiverIntro.setPadding(0, 0, 0, dp(8));
        receiverCard.addView(receiverIntro, matchWrap());

        pairButton = actionButton("请求配对码并绑定", true);
        receiverCard.addView(pairButton, spacedWrap());
        pairButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                String requestId = BridgeSettings.getPairingRequestId(MainActivity.this);
                boolean paired = BridgeSettings.isPaired(MainActivity.this)
                        && !BridgeSettings.isPairingExpired(BridgeSettings.getPairingExpiresAt(MainActivity.this));
                if (paired) {
                    new android.app.AlertDialog.Builder(MainActivity.this)
                            .setTitle("撤销本机绑定？")
                            .setMessage("撤销后，这台手机会停止接收当前工作区的主控命令，需要重新请求配对码才能恢复。")
                            .setNegativeButton("取消", null)
                            .setPositiveButton("撤销绑定", new android.content.DialogInterface.OnClickListener() {
                                @Override
                                public void onClick(android.content.DialogInterface dialog, int which) {
                                    unpairCurrentDevice();
                                }
                            })
                            .show();
                    return;
                }
                markClicked(pairButton, requestId.trim().isEmpty() ? "请求中..." : "检查中...");
                BridgeSettings.setReceiverUrl(MainActivity.this, urlInput.getText().toString());
                if (requestId.trim().isEmpty()) {
                    stopService(new Intent(MainActivity.this, HeartbeatService.class));
                    BridgeSettings.setHeartbeatEnabled(MainActivity.this, false);
                    BridgeSettings.applyPairing(
                            MainActivity.this,
                            urlInput.getText().toString(),
                            workspaceInput.getText().toString(),
                            "",
                            BridgeSettings.getPairingExpiresAt(MainActivity.this));
                    setBusy("正在向主控发送绑定请求...");
                } else {
                    setBusy("正在检查主控是否已批准绑定...");
                }
                PairingClient.Callback callback = new PairingClient.Callback() {
                    @Override
                    public void onDone(boolean ok, String message) {
                        LinkSender.toast(MainActivity.this, message);
                        if (ok && BridgeSettings.isPaired(MainActivity.this)) {
                            ensureBackgroundSync();
                            syncCommands(false);
                        }
                        refreshWithStatus(message);
                    }
                };
                if (requestId.trim().isEmpty()) {
                    PairingClient.requestAndBindWithDiscovery(MainActivity.this, callback);
                } else {
                    PairingClient.checkPendingApproval(MainActivity.this, callback);
                }
            }
        });

        connectionSettingsHeader = collapseHeader("高级连接设置", false);
        receiverCard.addView(connectionSettingsHeader, matchWrap());
        connectionSettingsBody = collapseBody(false);
        receiverCard.addView(connectionSettingsBody, matchWrap());
        connectionSettingsHeader.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                toggleSection(connectionSettingsHeader, connectionSettingsBody, "高级连接设置");
            }
        });

        urlInput = new EditText(this);
        urlInput.setSingleLine(true);
        urlInput.setHint("主控地址，例如 https://control.spiritkinai.cn/android/link");
        urlInput.setTextColor(TEXT);
        urlInput.setTextSize(15);
        urlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        urlInput.setPadding(dp(10), 0, dp(10), 0);
        connectionSettingsBody.addView(urlInput, matchWrap());

        workspaceInput = new EditText(this);
        workspaceInput.setSingleLine(true);
        workspaceInput.setHint("工作区，例如 local-ecommerce");
        workspaceInput.setTextColor(TEXT);
        workspaceInput.setTextSize(15);
        workspaceInput.setPadding(dp(10), 0, dp(10), 0);
        connectionSettingsBody.addView(workspaceInput, spacedWrap());

        tokenInput = new EditText(this);
        tokenInput.setSingleLine(true);
        tokenInput.setHint("手动 Android 配对码，只能使用一次");
        tokenInput.setTextColor(TEXT);
        tokenInput.setTextSize(15);
        tokenInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        tokenInput.setPadding(dp(10), 0, dp(10), 0);
        connectionSettingsBody.addView(tokenInput, spacedWrap());

        Button saveButton = actionButton("保存连接设置", false);
        LinearLayout.LayoutParams saveParams = matchWrap();
        saveParams.setMargins(0, dp(12), 0, 0);
        connectionSettingsBody.addView(saveButton, saveParams);
        saveButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                markClicked(saveButton, "保存中...");
                BridgeSettings.setReceiverUrl(MainActivity.this, urlInput.getText().toString());
                BridgeSettings.applyPairing(
                        MainActivity.this,
                        urlInput.getText().toString(),
                        workspaceInput.getText().toString(),
                        tokenInput.getText().toString());
                BridgeSettings.appendEvent(MainActivity.this, "已保存主控连接设置");
                refresh();
                LinkSender.toast(MainActivity.this, "已保存");
                restoreButton(saveButton);
            }
        });

        Button healthButton = actionButton("检测主控连接", true);
        receiverCard.addView(healthButton, spacedWrap());
        healthButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                markClicked(healthButton, "检测中...");
                saveCurrentUrl();
                setBusy("正在检测主控连接...");
                ReceiverDiscovery.findAndSave(MainActivity.this, new ReceiverDiscovery.Callback() {
                    @Override
                    public void onDone(boolean ok, String receiverUrl, String message) {
                        LinkSender.toast(MainActivity.this, message);
                        refreshWithStatus(message);
                        if (ok && canSyncWithController()) {
                            syncCommands(false);
                        }
                        restoreButton(healthButton);
                    }
                });
            }
        });

        Button batteryButton = actionButton("允许后台持续同步", false);
        connectionSettingsBody.addView(batteryButton, spacedWrap());
        batteryButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                markClicked(batteryButton, "打开中...");
                openBatteryOptimizationSettings();
                refreshWithStatus("请允许本应用后台运行，避免系统停止 heartbeat");
                restoreButton(batteryButton);
            }
        });

        Button manualPairButton = actionButton("使用已填配对码绑定", false);
        connectionSettingsBody.addView(manualPairButton, spacedWrap());
        manualPairButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                markClicked(manualPairButton, "绑定中...");
                BridgeSettings.applyPairing(
                        MainActivity.this,
                        urlInput.getText().toString(),
                        workspaceInput.getText().toString(),
                        tokenInput.getText().toString());
                setBusy("正在使用已填配对码绑定到工作区...");
                PairingClient.bindWithDiscovery(MainActivity.this, new PairingClient.Callback() {
                    @Override
                    public void onDone(boolean ok, String message) {
                        LinkSender.toast(MainActivity.this, message);
                        if (ok && BridgeSettings.isPaired(MainActivity.this)) {
                            ensureBackgroundSync();
                            syncCommands(false);
                        }
                        refreshWithStatus(message);
                        restoreButton(manualPairButton);
                    }
                });
            }
        });

        Button updateButton = actionButton("检查安装包更新", false);
        LinearLayout.LayoutParams updateParams = matchWrap();
        updateParams.setMargins(0, dp(10), 0, 0);
        receiverCard.addView(updateButton, updateParams);
        updateButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                markClicked(updateButton, "检查中...");
                saveCurrentUrl();
                setBusy("正在检查更新...");
                AppUpdater.checkAndInstall(MainActivity.this, new AppUpdater.Callback() {
                    @Override
                    public void onDone(boolean ok, String message) {
                        LinkSender.toast(MainActivity.this, message);
                        refreshWithStatus(message);
                        restoreButton(updateButton);
                    }
                });
            }
        });

        LinearLayout bridgeCard = section("设备状态");
        statusRoot.addView(bridgeCard, sectionParams());
        bridgeCard.addView(infoRow("手机", Build.MANUFACTURER + " " + Build.MODEL + " · Android " + Build.VERSION.RELEASE));
        bridgeCard.addView(infoRow("角色", "本机是 Android 执行端；桌面端和 iOS 是主控端"));
        bridgeCard.addView(infoRow("用法", "绑定后本机会自动后台同步主控命令；重装或升级后请重新开启无障碍，PDD 自动化才可执行"));
        workerSummaryView = infoRow("Worker 状态", "--");
        bridgeCard.addView(workerSummaryView);
        workerCapabilityView = infoRow("能力注册", "--");
        bridgeCard.addView(workerCapabilityView);
        pddAutomationView = infoRow("PDD 自动化", PddAutomationService.status(this));
        bridgeCard.addView(pddAutomationView);
        Button bridgeSyncButton = actionButton("立即同步一次", true);
        bridgeCard.addView(bridgeSyncButton, matchWrap());
        bridgeSyncButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                markClicked(bridgeSyncButton, "同步中...");
                saveCurrentUrl();
                syncCommands(true);
                new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        restoreButton(bridgeSyncButton);
                    }
                }, 1200L);
            }
        });
        Button accessibilityButton = actionButton("打开无障碍设置", false);
        bridgeCard.addView(accessibilityButton, matchWrap());
        accessibilityButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            }
        });

        LinearLayout controlCard = section("工作流与模块");
        workflowRoot.addView(controlCard, sectionParams());
        TextView moduleIntro = label("按工作流管理模块。展开工作流后，再管理它需要的图片、链接、自动化能力。", MUTED, 13, false);
        moduleIntro.setPadding(0, 0, 0, dp(10));
        controlCard.addView(moduleIntro, matchWrap());
        ecommerceWorkflowHeader = collapseHeader("自动化上架工作流", true);
        controlCard.addView(ecommerceWorkflowHeader, matchWrap());
        ecommerceWorkflowBody = collapseBody(true);
        controlCard.addView(ecommerceWorkflowBody, matchWrap());
        ecommerceWorkflowHeader.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                toggleSection(ecommerceWorkflowHeader, ecommerceWorkflowBody, "自动化上架工作流");
            }
        });

        TextView workflowIntro = label("用于商品上架：先准备商品图片和商品链接，再由 PDD 自动化执行手机端步骤。", MUTED, 13, false);
        workflowIntro.setPadding(0, dp(8), 0, dp(8));
        ecommerceWorkflowBody.addView(workflowIntro, matchWrap());

        imageModuleHeader = collapseHeader("商品图片模块", false);
        ecommerceWorkflowBody.addView(imageModuleHeader, matchWrap());
        imageModuleBody = collapseBody(false);
        ecommerceWorkflowBody.addView(imageModuleBody, matchWrap());
        imageModuleHeader.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                toggleSection(imageModuleHeader, imageModuleBody, "商品图片模块");
            }
        });
        TextView uploadIntro = label("相册分享进来的图片会上传到云端图片库；只有图片 URL/路径文本时，才使用上面的“登记图片 URL/路径”。", MUTED, 13, false);
        uploadIntro.setPadding(0, 0, 0, dp(8));
        imageModuleBody.addView(uploadIntro, matchWrap());
        Button sendImageRefButton = actionButton("登记图片 URL/路径", false);
        imageModuleBody.addView(sendImageRefButton, matchWrap());
        sendImageRefButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                markClicked(sendImageRefButton, "登记中...");
                saveCurrentUrl();
                String text = readClipboard();
                ArtifactSender.postClipboardImageRef(MainActivity.this, text, new ArtifactSender.Callback() {
                    @Override
                    public void onDone(boolean ok, String message) {
                        LinkSender.toast(MainActivity.this, message);
                        refreshWithStatus(message);
                        restoreButton(sendImageRefButton);
                    }
                });
            }
        });
        uploadHistoryView = label("--", MUTED, 13, false);
        uploadHistoryView.setLineSpacing(0, 1.08f);
        imageModuleBody.addView(uploadHistoryView, matchWrap());
        Button clearUploadsButton = actionButton("清空本机上传日志", false);
        LinearLayout.LayoutParams clearUploadParams = matchWrap();
        clearUploadParams.setMargins(0, dp(10), 0, 0);
        imageModuleBody.addView(clearUploadsButton, clearUploadParams);
        clearUploadsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                BridgeSettings.clearRecentUploads(MainActivity.this);
                refreshWithStatus("已清空本机上传记录");
            }
        });
        Button cleanupCacheButton = actionButton("清理图片缓存", false);
        imageModuleBody.addView(cleanupCacheButton, spacedWrap());
        cleanupCacheButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                int deleted = CommandSync.cleanupCachedArtifacts(MainActivity.this);
                refreshWithStatus("已清理本机图片缓存: " + deleted + " 个");
            }
        });
        TextView cloudIntro = label("这里管理本机上传到云端的商品图片。删除这里的图片会从云端移除；新增或替换图片请从相册分享到本应用，替换时先删除旧图。缩略图用于确认要删的是哪张。", MUTED, 13, false);
        cloudIntro.setPadding(0, dp(14), 0, dp(8));
        imageModuleBody.addView(cloudIntro, matchWrap());
        cloudUploadSummaryView = label("未刷新云端图片", MUTED, 13, true);
        imageModuleBody.addView(cloudUploadSummaryView, matchWrap());
        cloudUploadList = new LinearLayout(this);
        cloudUploadList.setOrientation(LinearLayout.VERTICAL);
        cloudUploadList.setPadding(0, dp(8), 0, 0);
        imageModuleBody.addView(cloudUploadList, matchWrap());
        Button refreshCloudUploadsButton = actionButton("刷新云端图片", true);
        LinearLayout.LayoutParams refreshCloudParams = matchWrap();
        refreshCloudParams.setMargins(0, dp(10), 0, 0);
        imageModuleBody.addView(refreshCloudUploadsButton, refreshCloudParams);
        refreshCloudUploadsButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                refreshCloudUploads(refreshCloudUploadsButton);
            }
        });

        linkModuleHeader = collapseHeader("商品链接模块", false);
        ecommerceWorkflowBody.addView(linkModuleHeader, spacedWrap());
        linkModuleBody = collapseBody(false);
        ecommerceWorkflowBody.addView(linkModuleBody, matchWrap());
        linkModuleHeader.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                toggleSection(linkModuleHeader, linkModuleBody, "商品链接模块");
            }
        });
        TextView shareIntro = label("从拼多多或微信分享文本到本应用，或发送剪贴板里的商品链接。云端链接会显示在这里。", MUTED, 13, false);
        shareIntro.setPadding(0, 0, 0, dp(12));
        linkModuleBody.addView(shareIntro, matchWrap());

        Button sendClipboardButton = actionButton("发送剪贴板链接", true);
        linkModuleBody.addView(sendClipboardButton, matchWrap());
        sendClipboardButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                markClicked(sendClipboardButton, "发送中...");
                saveCurrentUrl();
                String text = readClipboard();
                String link = LinkSender.extractPddLink(text);
                if (link.isEmpty()) {
                    BridgeSettings.appendEvent(MainActivity.this, "剪贴板未找到拼多多链接");
                    refreshWithStatus("剪贴板未找到拼多多链接");
                    LinkSender.toast(MainActivity.this, "未找到拼多多链接");
                    restoreButton(sendClipboardButton);
                    return;
                }
                setBusy("正在发送: " + link);
                LinkSender.postLink(MainActivity.this, text, new LinkSender.Callback() {
                    @Override
                    public void onDone(boolean ok, String message) {
                        LinkSender.toast(MainActivity.this, message);
                        refreshWithStatus(message);
                        restoreButton(sendClipboardButton);
                    }
                });
            }
        });

        TextView linkIntro = label("这里管理本机回传到云端的商品链接。删除云端链接不会影响已生成的旧工作流任务。", MUTED, 13, false);
        linkIntro.setPadding(0, 0, 0, dp(8));
        linkModuleBody.addView(linkIntro, matchWrap());

        cloudLinkSummaryView = label("未刷新云端链接", MUTED, 13, true);
        linkModuleBody.addView(cloudLinkSummaryView, matchWrap());
        cloudLinkList = new LinearLayout(this);
        cloudLinkList.setOrientation(LinearLayout.VERTICAL);
        cloudLinkList.setPadding(0, dp(8), 0, 0);
        linkModuleBody.addView(cloudLinkList, matchWrap());

        Button refreshCloudLinksButton = actionButton("刷新云端链接", true);
        LinearLayout.LayoutParams refreshLinksParams = matchWrap();
        refreshLinksParams.setMargins(0, dp(10), 0, 0);
        linkModuleBody.addView(refreshCloudLinksButton, refreshLinksParams);
        refreshCloudLinksButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                refreshCloudLinks(refreshCloudLinksButton);
            }
        });

        TextView localLinkIntro = label("本机发送日志，只用于查看最近操作。", MUTED, 13, false);
        localLinkIntro.setPadding(0, dp(14), 0, dp(8));
        linkModuleBody.addView(localLinkIntro, matchWrap());
        linkHistoryView = label("--", MUTED, 13, false);
        linkHistoryView.setLineSpacing(0, 1.08f);
        linkModuleBody.addView(linkHistoryView, matchWrap());
        Button clearLinksButton = actionButton("清空本机链接日志", false);
        LinearLayout.LayoutParams clearLinksParams = matchWrap();
        clearLinksParams.setMargins(0, dp(10), 0, 0);
        linkModuleBody.addView(clearLinksButton, clearLinksParams);
        clearLinksButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                BridgeSettings.clearRecentLinks(MainActivity.this);
                refreshWithStatus("已清空本机链接记录");
            }
        });

        pddModuleHeader = collapseHeader("PDD 自动化模块", false);
        ecommerceWorkflowBody.addView(pddModuleHeader, spacedWrap());
        pddModuleBody = collapseBody(false);
        ecommerceWorkflowBody.addView(pddModuleBody, matchWrap());
        pddModuleHeader.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                toggleSection(pddModuleHeader, pddModuleBody, "PDD 自动化模块");
            }
        });
        pddModuleBody.addView(infoRow("状态", PddAutomationService.status(this)), matchWrap());
        pddModuleBody.addView(infoRow("用途", "打开 PDD、分享商品图、执行上架步骤。这个模块会由主控工作流下发命令。"));
        Button pddAccessibilityButton = actionButton("打开无障碍设置", false);
        pddModuleBody.addView(pddAccessibilityButton, spacedWrap());
        pddAccessibilityButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            }
        });

        commonWorkflowHeader = collapseHeader("通用手机端能力", false);
        controlCard.addView(commonWorkflowHeader, spacedWrap());
        commonWorkflowBody = collapseBody(false);
        controlCard.addView(commonWorkflowBody, matchWrap());
        commonWorkflowHeader.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                toggleSection(commonWorkflowHeader, commonWorkflowBody, "通用手机端能力");
            }
        });
        commonWorkflowBody.addView(infoRow("命令同步", "已归并到连接主控和设备状态；绑定后后台自动同步，设备状态里的“立即同步一次”仅用于排查"));
        commonWorkflowBody.addView(infoRow("组合工作流", "以后新增工作流时，本机仍作为 Android 执行端。"));

        diagnosticsHeader = collapseHeader("验收与排查", false);
        controlCard.addView(diagnosticsHeader, spacedWrap());
        diagnosticsBody = collapseBody(false);
        controlCard.addView(diagnosticsBody, matchWrap());
        diagnosticsHeader.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                toggleSection(diagnosticsHeader, diagnosticsBody, "验收与排查");
            }
        });
        diagnosticsBody.addView(infoRow("屏幕截图", ScreenCaptureStore.isAuthorized() ? "已授权" : "需要授权"));
        Button screenshotButton = actionButton("请求屏幕截图授权", false);
        diagnosticsBody.addView(screenshotButton, spacedWrap());
        screenshotButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                ScreenCaptureStore.requestCapture(MainActivity.this);
                refreshWithStatus("已打开屏幕截图授权");
            }
        });
        Button diagnosticAccessibilityButton = actionButton("打开无障碍设置", false);
        diagnosticsBody.addView(diagnosticAccessibilityButton, spacedWrap());
        diagnosticAccessibilityButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            }
        });

        LinearLayout recentCard = section("连接状态");
        statusRoot.addView(recentCard, sectionParams());
        statusView = label("--", TEXT, 14, true);
        statusView.setPadding(0, 0, 0, dp(10));
        recentCard.addView(statusView, matchWrap());
        recentView = label("", MUTED, 13, false);

        setContentView(appRoot);
        selectNavigationPage(selectedNavigationPage);
        statusRoot.requestFocus();
    }

    private void refreshCloudUploads(Button sourceButton) {
        if (sourceButton != null) {
            markClicked(sourceButton, "刷新中...");
        }
        saveCurrentUrl();
        setBusy("正在刷新云端图片...");
        ArtifactManager.fetchUploads(this, new ArtifactManager.ListCallback() {
            @Override
            public void onDone(boolean ok, String message, List<ArtifactManager.UploadItem> items) {
                renderCloudUploads(items);
                LinkSender.toast(MainActivity.this, message);
                refreshWithStatus(message);
                if (sourceButton != null) {
                    restoreButton(sourceButton);
                }
            }
        });
    }

    private void renderCloudUploads(List<ArtifactManager.UploadItem> items) {
        if (cloudUploadSummaryView != null) {
            cloudUploadSummaryView.setText(ArtifactManager.summary(items));
        }
        if (cloudUploadList == null) {
            return;
        }
        cloudUploadList.removeAllViews();
        if (items == null || items.isEmpty()) {
            TextView empty = label("暂无云端图片", MUTED, 13, false);
            empty.setPadding(0, dp(6), 0, dp(2));
            cloudUploadList.addView(empty, matchWrap());
            return;
        }
        for (final ArtifactManager.UploadItem item : items) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.VERTICAL);
            row.setPadding(dp(8), dp(8), dp(8), dp(8));
            row.setBackgroundColor(ROW_ALT);
            LinearLayout.LayoutParams rowParams = matchWrap();
            rowParams.setMargins(0, 0, 0, dp(8));
            cloudUploadList.addView(row, rowParams);

            TextView text = label(ArtifactManager.formatItem(item), TEXT, 13, false);
            text.setLineSpacing(0, 1.08f);
            row.addView(text, matchWrap());

            final ImageView preview = new ImageView(this);
            preview.setBackgroundColor(PREVIEW_BG);
            preview.setScaleType(ImageView.ScaleType.CENTER_CROP);
            preview.setContentDescription("素材预览：第 " + (item.fileIndex + 1) + " 张");
            LinearLayout.LayoutParams previewParams = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    dp(132));
            previewParams.setMargins(0, dp(8), 0, 0);
            row.addView(preview, previewParams);
            ArtifactManager.fetchPreview(this, item.artifactId, item.fileIndex, new ArtifactManager.ImageCallback() {
                @Override
                public void onDone(boolean ok, Bitmap bitmap) {
                    if (ok && bitmap != null) {
                        preview.setImageBitmap(bitmap);
                    }
                }
            });

            Button deleteButton = actionButton("删除第 " + (item.fileIndex + 1) + " 张", false);
            LinearLayout.LayoutParams deleteParams = matchWrap();
            deleteParams.setMargins(0, dp(8), 0, 0);
            row.addView(deleteButton, deleteParams);
            deleteButton.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View view) {
                    deleteCloudUpload(item, deleteButton);
                }
            });
        }
    }

    private void deleteCloudUpload(final ArtifactManager.UploadItem item, final Button button) {
        markClicked(button, "删除中...");
        setBusy("正在删除云端图片...");
        ArtifactManager.deleteUploadFile(this, item.artifactId, item.fileIndex, new ArtifactManager.ActionCallback() {
            @Override
            public void onDone(boolean ok, String message) {
                LinkSender.toast(MainActivity.this, message);
                refreshWithStatus(message);
                if (ok) {
                    refreshCloudUploads(null);
                } else {
                    restoreButton(button);
                }
            }
        });
    }

    private void refreshCloudLinks(Button sourceButton) {
        if (sourceButton != null) {
            markClicked(sourceButton, "刷新中...");
        }
        saveCurrentUrl();
        setBusy("正在刷新云端链接...");
        LinkManager.fetchLinks(this, new LinkManager.ListCallback() {
            @Override
            public void onDone(boolean ok, String message, List<LinkManager.LinkItem> items) {
                renderCloudLinks(items);
                LinkSender.toast(MainActivity.this, message);
                refreshWithStatus(message);
                if (sourceButton != null) {
                    restoreButton(sourceButton);
                }
            }
        });
    }

    private void renderCloudLinks(List<LinkManager.LinkItem> items) {
        if (cloudLinkSummaryView != null) {
            cloudLinkSummaryView.setText(LinkManager.summary(items));
        }
        if (cloudLinkList == null) {
            return;
        }
        cloudLinkList.removeAllViews();
        if (items == null || items.isEmpty()) {
            TextView empty = label("暂无云端链接", MUTED, 13, false);
            empty.setPadding(0, dp(6), 0, dp(2));
            cloudLinkList.addView(empty, matchWrap());
            return;
        }
        for (final LinkManager.LinkItem item : items) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.VERTICAL);
            row.setPadding(dp(8), dp(8), dp(8), dp(8));
            row.setBackgroundColor(ROW_ALT);
            LinearLayout.LayoutParams rowParams = matchWrap();
            rowParams.setMargins(0, 0, 0, dp(8));
            cloudLinkList.addView(row, rowParams);

            TextView text = label(LinkManager.formatItem(item), TEXT, 13, false);
            text.setLineSpacing(0, 1.08f);
            row.addView(text, matchWrap());

            Button copyButton = actionButton("复制链接", false);
            LinearLayout.LayoutParams copyParams = matchWrap();
            copyParams.setMargins(0, dp(8), 0, 0);
            row.addView(copyButton, copyParams);
            copyButton.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View view) {
                    copyText(item.link, "商品链接");
                    refreshWithStatus("已复制商品链接");
                }
            });

            Button deleteButton = actionButton("删除链接", false);
            row.addView(deleteButton, spacedWrap());
            deleteButton.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View view) {
                    deleteCloudLink(item, deleteButton);
                }
            });
        }
    }

    private void deleteCloudLink(final LinkManager.LinkItem item, final Button button) {
        markClicked(button, "删除中...");
        setBusy("正在删除云端链接...");
        LinkManager.deleteLink(this, item.linkId, new LinkManager.ActionCallback() {
            @Override
            public void onDone(boolean ok, String message) {
                LinkSender.toast(MainActivity.this, message);
                refreshWithStatus(message);
                if (ok) {
                    refreshCloudLinks(null);
                } else {
                    restoreButton(button);
                }
            }
        });
    }

    private void toggleHeartbeat(Button button) {
        boolean enabled = BridgeSettings.isHeartbeatEnabled(this);
        markClicked(button, enabled ? "停止同步..." : "启动同步...");
        if (enabled) {
            stopService(new Intent(this, HeartbeatService.class));
            BridgeSettings.setHeartbeatEnabled(this, false);
            LinkSender.toast(this, "已停止后台同步");
            refreshWithStatus("已停止后台同步");
        } else {
            startHeartbeatService();
            BridgeSettings.setHeartbeatEnabled(this, true);
            LinkSender.toast(this, "已开始后台同步");
            refreshWithStatus("已开始后台同步");
        }
        restoreButton(button);
    }

    private void syncFromController(final Button button) {
        markClicked(button, "同步中...");
        saveCurrentUrl();
        setBusy("正在同步主控配置...");
        syncCommands(true);
        new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(new Runnable() {
            @Override
            public void run() {
                restoreButton(button);
            }
        }, 1200L);
    }

    private LinearLayout pageRoot() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(18), dp(16), dp(22));
        root.setFocusableInTouchMode(true);
        return root;
    }

    private ScrollView pageScroll(LinearLayout root) {
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(BG);
        scroll.setFillViewport(true);
        scroll.addView(root, new ScrollView.LayoutParams(
                ScrollView.LayoutParams.MATCH_PARENT,
                ScrollView.LayoutParams.WRAP_CONTENT));
        return scroll;
    }

    private void addPageHeader(LinearLayout root, String title, String subtitle) {
        TextView titleView = label(title, TEXT, 24, true);
        titleView.setPadding(0, dp(2), 0, dp(4));
        root.addView(titleView, matchWrap());
        TextView subtitleView = label(subtitle, MUTED, 13, false);
        subtitleView.setPadding(0, 0, 0, dp(4));
        root.addView(subtitleView, matchWrap());
    }

    private Button navigationButton(String label, int iconResource, final int pageIndex) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(12);
        button.setAllCaps(false);
        button.setGravity(Gravity.CENTER);
        button.setMinHeight(dp(56));
        button.setPadding(dp(8), dp(4), dp(8), dp(4));
        button.setCompoundDrawablesWithIntrinsicBounds(0, iconResource, 0, 0);
        button.setCompoundDrawablePadding(dp(2));
        button.setContentDescription(label);
        button.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                selectNavigationPage(pageIndex);
            }
        });
        return button;
    }

    private void selectNavigationPage(int requestedIndex) {
        if (navigationPages == null || navigationPages.length == 0) {
            return;
        }
        int index = Math.max(0, Math.min(requestedIndex, navigationPages.length - 1));
        selectedNavigationPage = index;
        for (int i = 0; i < navigationPages.length; i++) {
            boolean selected = i == index;
            navigationPages[i].setVisibility(selected ? View.VISIBLE : View.GONE);
            if (navigationButtons != null && i < navigationButtons.length) {
                navigationButtons[i].setSelected(selected);
                int itemColor = selected ? PRIMARY : MUTED;
                navigationButtons[i].setTextColor(itemColor);
                for (android.graphics.drawable.Drawable drawable : navigationButtons[i].getCompoundDrawables()) {
                    if (drawable != null) {
                        drawable.setTint(itemColor);
                    }
                }
                navigationButtons[i].setBackgroundColor(selected ? ROW_ALT : CARD);
            }
        }
    }

    private Button collapseHeader(String title, boolean expanded) {
        Button button = actionButton((expanded ? "收起 " : "展开 ") + title, false);
        button.setTag(Boolean.valueOf(expanded));
        return button;
    }

    private LinearLayout collapseBody(boolean expanded) {
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(8), dp(8), dp(8), dp(8));
        body.setBackgroundColor(ROW_ALT);
        body.setVisibility(expanded ? View.VISIBLE : View.GONE);
        return body;
    }

    private void toggleSection(Button header, LinearLayout body, String title) {
        boolean expanded = body.getVisibility() == View.VISIBLE;
        setSectionExpanded(header, body, title, !expanded);
    }

    private void setSectionExpanded(Button header, LinearLayout body, String title, boolean expanded) {
        if (header != null) {
            header.setTag(Boolean.valueOf(expanded));
            header.setText((expanded ? "收起 " : "展开 ") + title);
        }
        if (body != null) {
            body.setVisibility(expanded ? View.VISIBLE : View.GONE);
        }
    }

    private void copyText(String text, String label) {
        ClipboardManager manager = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (manager != null) {
            manager.setPrimaryClip(ClipData.newPlainText(label, text == null ? "" : text));
            LinkSender.toast(this, "已复制");
        }
    }

    private LinearLayout section(String title) {
        LinearLayout box = card();
        TextView heading = label(title, TEXT, 16, true);
        heading.setPadding(0, 0, 0, dp(10));
        box.addView(heading, matchWrap());
        return box;
    }

    private LinearLayout card() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(14), dp(14), dp(14), dp(14));
        box.setBackgroundColor(CARD);
        return box;
    }

    private TextView label(String text, int color, int size, boolean bold) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(color);
        view.setTextSize(size);
        if (bold) {
            view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        }
        return view;
    }

    private TextView statusPill(String text, int color) {
        TextView view = label("● " + text, PILL_TEXT, 12, true);
        view.setGravity(Gravity.CENTER);
        view.setPadding(dp(8), dp(8), dp(8), dp(8));
        view.setBackgroundColor(color);
        LinearLayout.LayoutParams p = weightWrap();
        p.setMargins(dp(3), dp(3), dp(3), dp(3));
        view.setLayoutParams(p);
        return view;
    }

    private TextView infoRow(String title, String value) {
        TextView view = label(title + "\n" + value, MUTED, 13, false);
        view.setPadding(0, dp(8), 0, dp(8));
        return view;
    }

    private Button actionButton(String text, boolean primary) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextSize(13);
        button.setTextColor(primary ? PILL_TEXT : TEXT);
        button.setAllCaps(false);
        button.setMinHeight(dp(48));
        button.setGravity(Gravity.CENTER);
        button.setPadding(dp(10), 0, dp(10), 0);
        button.setBackground(buttonBackground(primary));
        return button;
    }

    private StateListDrawable buttonBackground(boolean primary) {
        int normal = primary ? PRIMARY : BTN_FACE;
        int pressed = primary ? BTN_PRIMARY_PRESSED : BTN_FACE_PRESSED;
        int disabled = BTN_DISABLED;
        StateListDrawable drawable = new StateListDrawable();
        drawable.addState(new int[] {-android.R.attr.state_enabled}, colorDrawable(disabled));
        drawable.addState(new int[] {android.R.attr.state_pressed}, colorDrawable(pressed));
        drawable.addState(new int[] {}, colorDrawable(normal));
        return drawable;
    }

    private android.graphics.drawable.ColorDrawable colorDrawable(int color) {
        return new android.graphics.drawable.ColorDrawable(color);
    }

    private void markClicked(Button button, String text) {
        if (button == null) {
            return;
        }
        Object original = button.getTag();
        if (!(original instanceof String)) {
            button.setTag(button.getText().toString());
        }
        button.setEnabled(false);
        button.setText(text);
    }

    private void restoreButton(Button button) {
        if (button == null) {
            return;
        }
        Object original = button.getTag();
        if (original instanceof String) {
            button.setText((String) original);
        }
        button.setEnabled(true);
    }

    private void saveCurrentUrl() {
        BridgeSettings.setReceiverUrl(this, urlInput.getText().toString());
    }

    private void refresh() {
        refreshWithStatus("就绪");
        ensureBackgroundSync();
        if (canSyncWithController()) {
            syncCommands(false);
        }
    }

    private void refreshWithStatus(String status) {
        String receiverUrl = BridgeSettings.getReceiverUrl(this);
        urlInput.setText(receiverUrl);
        workspaceInput.setText(BridgeSettings.getWorkspaceId(this));
        tokenInput.setText(BridgeSettings.getPairingToken(this));
        receiverSummaryView.setText(receiverUrl);
        String pair = BridgeSettings.isPaired(this) ? "已绑定" : "未绑定";
        String expiresAt = BridgeSettings.getPairingExpiresAt(this);
        boolean pairingExpired = BridgeSettings.isPairingExpired(expiresAt);
        String validity = BridgeSettings.pairingValidityText(expiresAt);
        if (BridgeSettings.isPaired(this) && !expiresAt.trim().isEmpty()) {
            pair += " · " + validity;
        }
        String requestId = BridgeSettings.getPairingRequestId(this);
        if (!requestId.trim().isEmpty() && (!BridgeSettings.isPaired(this) || pairingExpired)) {
            pair += " · 绑定请求已发送，等待主控批准";
        } else if (!BridgeSettings.isPaired(this) || pairingExpired) {
            pair += " · 点请求配对码并绑定";
        }
        if (pairButton != null) {
            if (BridgeSettings.isPaired(this) && !pairingExpired) {
                pairButton.setText("撤销本机绑定 · " + validity);
                pairButton.setEnabled(true);
            } else if (!requestId.trim().isEmpty()) {
                pairButton.setText("等待主控批准中 · 点此检查");
                pairButton.setEnabled(true);
            } else {
                pairButton.setText("请求配对码并绑定");
                pairButton.setEnabled(true);
            }
        }
        statusView.setText(status + " · " + pair);
        if (workerSummaryView != null) {
            workerSummaryView.setText("Worker 状态\n" + androidWorkerStatusText(pairingExpired));
        }
        if (workerCapabilityView != null) {
            workerCapabilityView.setText("能力注册\n" + BridgeModuleRegistry.workerSummaryText(this));
        }
        if (pddAutomationView != null) {
            pddAutomationView.setText("PDD 自动化\n" + PddAutomationService.status(this));
        }
        String events = BridgeSettings.getRecentEvents(this);
        recentView.setText(events.isEmpty() ? "暂无记录" : events);
        if (uploadHistoryView != null) {
            String uploads = BridgeSettings.getRecentUploads(this);
            uploadHistoryView.setText(uploads.isEmpty() ? "暂无上传记录" : uploads);
        }
        if (linkHistoryView != null) {
            String links = BridgeSettings.getRecentLinks(this);
            linkHistoryView.setText(links.isEmpty() ? "暂无链接记录" : links);
        }
    }

    private void setBusy(String status) {
        statusView.setText(status);
    }

    private void unpairCurrentDevice() {
        markClicked(pairButton, "撤销中...");
        PairingClient.unpair(MainActivity.this, new PairingClient.Callback() {
            @Override
            public void onDone(boolean ok, String message) {
                LinkSender.toast(MainActivity.this, message);
                stopService(new Intent(MainActivity.this, HeartbeatService.class));
                refreshWithStatus(message);
                restoreButton(pairButton);
            }
        });
    }

    private void syncCommands(boolean showToast) {
        if (!canSyncWithController()) {
            String message = BridgeSettings.getPairingRequestId(this).trim().isEmpty()
                    ? "请先请求配对码并完成绑定"
                    : "绑定请求已发送，等待主控批准";
            if (showToast) {
                LinkSender.toast(this, message);
            }
            refreshWithStatus(message);
            return;
        }
        CommandSync.sync(this, new CommandSync.Callback() {
            @Override
            public void onDone(boolean ok, String message) {
                if (showToast) {
                    LinkSender.toast(MainActivity.this, message);
                }
                refreshWithStatus(message);
            }
        });
    }

    private boolean canSyncWithController() {
        return BridgeSettings.isPaired(this)
                && !BridgeSettings.isPairingExpired(BridgeSettings.getPairingExpiresAt(this));
    }

    private String androidWorkerStatusText(boolean pairingExpired) {
        String binding = BridgeSettings.isPaired(this) && !pairingExpired ? "已绑定" : "未绑定";
        String sync = HeartbeatService.isServiceRunning() ? "后台同步运行" : "后台同步停止";
        String automation = PddAutomationService.isActive()
                ? "无障碍已连接"
                : (PddAutomationService.isSystemEnabled(this) ? "无障碍已授权" : "无障碍未授权");
        String screenshot = ScreenCaptureStore.isAuthorized() ? "截图已授权" : "截图未授权";
        return "android_control_worker · " + binding + " · " + sync + "\n"
                + "workspace " + BridgeSettings.getWorkspaceId(this) + " · version " + appVersion() + "\n"
                + automation + " · " + screenshot;
    }

    private void ensureBackgroundSync() {
        if (!canSyncWithController()) {
            return;
        }
        if (!HeartbeatService.isServiceRunning()) {
            startHeartbeatService();
            BridgeSettings.setHeartbeatEnabled(this, true);
            BridgeSettings.appendEvent(this, "已开启后台自动同步");
        }
    }

    private void openBatteryOptimizationSettings() {
        try {
            PowerManager manager = (PowerManager) getSystemService(POWER_SERVICE);
            if (Build.VERSION.SDK_INT >= 23
                    && manager != null
                    && !manager.isIgnoringBatteryOptimizations(getPackageName())) {
                Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
                intent.setData(Uri.parse("package:" + getPackageName()));
                startActivity(intent);
                return;
            }
        } catch (Exception ignored) {
        }
        try {
            startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
        } catch (Exception ignored) {
            startActivity(new Intent(Settings.ACTION_SETTINGS));
        }
    }

    private void startHeartbeatService() {
        Intent intent = new Intent(this, HeartbeatService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
    }

    private String readClipboard() {
        ClipboardManager manager = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (manager == null || !manager.hasPrimaryClip()) {
            return "";
        }
        ClipData clip = manager.getPrimaryClip();
        if (clip == null || clip.getItemCount() == 0) {
            return "";
        }
        CharSequence text = clip.getItemAt(0).coerceToText(this);
        return text == null ? "" : text.toString();
    }

    private String appVersion() {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            return info.versionName + " (" + info.versionCode + ")";
        } catch (Exception e) {
            return "unknown";
        }
    }

    private void handlePairingIntent(Intent intent) {
        Uri uri = intent == null ? null : intent.getData();
        if (!PairingClient.canHandle(uri)) {
            return;
        }
        setBusy("正在处理配对链接...");
        PairingClient.applyPairingUri(this, uri, new PairingClient.Callback() {
            @Override
            public void onDone(boolean ok, String message) {
                LinkSender.toast(MainActivity.this, message);
                if (ok && BridgeSettings.isPaired(MainActivity.this)) {
                    ensureBackgroundSync();
                }
                refreshWithStatus(message);
            }
        });
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private LinearLayout.LayoutParams spacedWrap() {
        LinearLayout.LayoutParams params = matchWrap();
        params.setMargins(0, dp(8), 0, 0);
        return params;
    }

    private LinearLayout.LayoutParams sectionParams() {
        LinearLayout.LayoutParams params = matchWrap();
        params.setMargins(0, dp(12), 0, 0);
        return params;
    }

    private LinearLayout.LayoutParams weightWrap() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                0,
                LinearLayout.LayoutParams.WRAP_CONTENT,
                1f);
        params.setMargins(dp(4), 0, dp(4), 0);
        return params;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
