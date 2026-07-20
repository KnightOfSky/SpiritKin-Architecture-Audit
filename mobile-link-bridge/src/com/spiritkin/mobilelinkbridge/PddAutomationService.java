package com.spiritkin.mobilelinkbridge;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.ComponentName;
import android.content.Context;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Bundle;
import android.provider.Settings;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.ArrayList;
import java.util.List;

public class PddAutomationService extends AccessibilityService {
    private static volatile PddAutomationService activeService;
    private static volatile String lastPackage = "";
    private static volatile long lastEventAt = 0L;
    private static volatile String lastResult = "idle";

    private static final String PDD_PACKAGE = "com.xunmeng.pinduoduo";

    @Override
    public void onServiceConnected() {
        super.onServiceConnected();
        activeService = this;
        BridgeSettings.appendEvent(this, "PDD 无障碍自动化已启用");
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) {
            return;
        }
        CharSequence packageName = event.getPackageName();
        lastPackage = packageName == null ? "" : packageName.toString();
        lastEventAt = System.currentTimeMillis();
    }

    @Override
    public void onInterrupt() {
        BridgeSettings.appendEvent(this, "PDD 无障碍自动化被中断");
    }

    @Override
    public boolean onUnbind(android.content.Intent intent) {
        activeService = null;
        BridgeSettings.appendEvent(this, "PDD 无障碍自动化已关闭");
        return super.onUnbind(intent);
    }

    static boolean isActive() {
        return activeService != null;
    }

    static boolean isSystemEnabled(Context context) {
        if (context == null) {
            return false;
        }
        String enabled = Settings.Secure.getString(
                context.getContentResolver(),
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        if (enabled == null || enabled.trim().isEmpty()) {
            return false;
        }
        ComponentName service = new ComponentName(context, PddAutomationService.class);
        String expected = service.flattenToString();
        String expectedShort = service.flattenToShortString();
        String[] services = enabled.split(":");
        for (String item : services) {
            String value = item == null ? "" : item.trim();
            if (value.equalsIgnoreCase(expected) || value.equalsIgnoreCase(expectedShort)) {
                return true;
            }
        }
        return false;
    }

    static String status(Context context) {
        if (isActive()) {
            return "无障碍已连接，前台包: " + (lastPackage.isEmpty() ? "unknown" : lastPackage) + "，最近结果: " + lastResult;
        }
        if (isSystemEnabled(context)) {
            return "系统已授权，但服务未连接；请返回应用后点同步，或关闭再开启无障碍";
        }
        return "无障碍未启用；重装或升级后系统会关闭它，请在系统无障碍中重新打开 SpiritKin PDD Automation";
    }

    static String status() {
        return isActive()
                ? "无障碍已连接，前台包: " + (lastPackage.isEmpty() ? "unknown" : lastPackage) + "，最近结果: " + lastResult
                : "无障碍未连接";
    }

    static String foregroundPackage() {
        return lastPackage == null ? "" : lastPackage;
    }

    static String tap(float x, float y) {
        PddAutomationService service = activeService;
        if (service == null) {
            lastResult = "accessibility_disabled";
            return "无障碍未连接，无法点击";
        }
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, 80))
                .build();
        boolean dispatched = service.dispatchGesture(gesture, null, null);
        lastResult = dispatched ? "tap_dispatched" : "tap_failed";
        return dispatched ? "已下发点击: " + x + "," + y : "点击下发失败";
    }

    static String dumpCurrentUi() {
        PddAutomationService service = activeService;
        if (service == null) {
            return "PDD 无障碍未启用";
        }
        AccessibilityNodeInfo root = service.getRootInActiveWindow();
        if (root == null) {
            return "无可读取窗口";
        }
        StringBuilder out = new StringBuilder();
        service.dumpNode(root, out, 0, 220);
        return out.toString();
    }

    static String beginListingTask(String artifactId, String title, String price, String description, boolean allowSubmit) {
        PddAutomationService service = activeService;
        if (service == null) {
            lastResult = "accessibility_disabled";
            return "PDD 无障碍未启用，无法执行字段级自动化";
        }
        if (!PDD_PACKAGE.equals(lastPackage) && !lastPackage.isEmpty()) {
            lastResult = "not_in_pdd:" + lastPackage;
            return "当前前台不是拼多多: " + lastPackage;
        }

        List<String> steps = new ArrayList<String>();
        AccessibilityNodeInfo root = service.getRootInActiveWindow();
        if (root == null) {
            lastResult = "no_root";
            return "无可读取窗口，无法执行";
        }

        service.dismissKnownDialogs(root, steps);
        boolean wroteAny = false;
        wroteAny = service.fillNear(root, new String[] {"标题", "商品标题", "名称", "商品名称"}, title, "标题", steps) || wroteAny;
        wroteAny = service.fillNear(root, new String[] {"价格", "售价", "拼单价", "单买价"}, price, "价格", steps) || wroteAny;
        wroteAny = service.fillNear(root, new String[] {"描述", "详情", "卖点", "商品介绍"}, description, "描述", steps) || wroteAny;

        if (!wroteAny) {
            service.tryScroll(root, steps);
            root = service.getRootInActiveWindow();
            if (root != null) {
                wroteAny = service.fillNear(root, new String[] {"标题", "商品标题", "名称", "商品名称"}, title, "标题", steps) || wroteAny;
                wroteAny = service.fillNear(root, new String[] {"价格", "售价", "拼单价", "单买价"}, price, "价格", steps) || wroteAny;
                wroteAny = service.fillNear(root, new String[] {"描述", "详情", "卖点", "商品介绍"}, description, "描述", steps) || wroteAny;
            }
        }

        if (!allowSubmit) {
            lastResult = wroteAny ? "filled_without_submit" : "no_fields_found";
            return "PDD 字段执行完成但未提交: " + join(steps);
        }

        root = service.getRootInActiveWindow();
        boolean submitted = root != null && service.clickByText(root, new String[] {"发布", "提交", "确认发布", "立即发布"}, steps);
        lastResult = submitted ? "submitted" : "submit_not_found";
        return submitted
                ? "PDD 已尝试点击发布: " + join(steps)
                : "未找到发布/提交按钮，未提交: " + join(steps);
    }

    private void dismissKnownDialogs(AccessibilityNodeInfo root, List<String> steps) {
        if (clickByText(root, new String[] {"取消", "关闭", "我知道了", "稍后再说"}, steps)) {
            sleep(350);
        }
    }

    private boolean fillNear(AccessibilityNodeInfo root, String[] labels, String value, String name, List<String> steps) {
        if (value == null || value.trim().isEmpty()) {
            return false;
        }
        AccessibilityNodeInfo editable = findEditableNearLabel(root, labels);
        if (editable == null) {
            editable = findFirstEditable(root);
        }
        if (editable == null) {
            steps.add(name + ":未找到输入框");
            return false;
        }
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
        boolean ok = editable.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        if (!ok) {
            editable.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
            ok = editable.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        }
        steps.add(name + (ok ? ":已填写" : ":填写失败"));
        sleep(250);
        return ok;
    }

    private AccessibilityNodeInfo findEditableNearLabel(AccessibilityNodeInfo root, String[] labels) {
        AccessibilityNodeInfo label = findByText(root, labels);
        if (label == null) {
            return null;
        }
        AccessibilityNodeInfo parent = label.getParent();
        for (int depth = 0; parent != null && depth < 4; depth++) {
            AccessibilityNodeInfo editable = findFirstEditable(parent);
            if (editable != null) {
                return editable;
            }
            parent = parent.getParent();
        }
        return null;
    }

    private AccessibilityNodeInfo findFirstEditable(AccessibilityNodeInfo node) {
        if (node == null) {
            return null;
        }
        CharSequence className = node.getClassName();
        String cls = className == null ? "" : className.toString();
        if (node.isEditable() || cls.contains("EditText")) {
            return node;
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo found = findFirstEditable(node.getChild(i));
            if (found != null) {
                return found;
            }
        }
        return null;
    }

    private boolean clickByText(AccessibilityNodeInfo root, String[] texts, List<String> steps) {
        AccessibilityNodeInfo node = findByText(root, texts);
        if (node == null) {
            return false;
        }
        AccessibilityNodeInfo target = node;
        for (int depth = 0; target != null && depth < 5; depth++) {
            if (target.isClickable() && target.isEnabled()) {
                boolean ok = target.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                steps.add("点击 " + textOf(node) + (ok ? ":成功" : ":失败"));
                sleep(350);
                return ok;
            }
            target = target.getParent();
        }
        return false;
    }

    private AccessibilityNodeInfo findByText(AccessibilityNodeInfo node, String[] texts) {
        if (node == null) {
            return null;
        }
        String haystack = (textOf(node) + " " + descOf(node)).toLowerCase();
        for (String text : texts) {
            if (!text.trim().isEmpty() && haystack.contains(text.toLowerCase())) {
                return node;
            }
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo found = findByText(node.getChild(i), texts);
            if (found != null) {
                return found;
            }
        }
        return null;
    }

    private void tryScroll(AccessibilityNodeInfo node, List<String> steps) {
        if (node == null) {
            return;
        }
        if (node.isScrollable()) {
            if (node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)) {
                steps.add("滚动:成功");
                sleep(500);
                return;
            }
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            tryScroll(node.getChild(i), steps);
        }
    }

    private void dumpNode(AccessibilityNodeInfo node, StringBuilder out, int depth, int[] remaining) {
        if (node == null || remaining[0] <= 0 || depth > 12) {
            return;
        }
        remaining[0]--;
        Rect rect = new Rect();
        node.getBoundsInScreen(rect);
        for (int i = 0; i < depth; i++) {
            out.append("  ");
        }
        out.append(node.getClassName())
                .append(" text=\"").append(textOf(node)).append("\"")
                .append(" desc=\"").append(descOf(node)).append("\"")
                .append(" editable=").append(node.isEditable())
                .append(" clickable=").append(node.isClickable())
                .append(" scrollable=").append(node.isScrollable())
                .append(" bounds=").append(rect.toShortString())
                .append("\n");
        for (int i = 0; i < node.getChildCount(); i++) {
            dumpNode(node.getChild(i), out, depth + 1, remaining);
        }
    }

    private void dumpNode(AccessibilityNodeInfo node, StringBuilder out, int depth, int remaining) {
        dumpNode(node, out, depth, new int[] {remaining});
    }

    private static String textOf(AccessibilityNodeInfo node) {
        CharSequence text = node == null ? null : node.getText();
        return text == null ? "" : text.toString();
    }

    private static String descOf(AccessibilityNodeInfo node) {
        CharSequence text = node == null ? null : node.getContentDescription();
        return text == null ? "" : text.toString();
    }

    private static String join(List<String> steps) {
        if (steps == null || steps.isEmpty()) {
            return "无步骤";
        }
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < steps.size(); i++) {
            if (i > 0) {
                out.append("；");
            }
            out.append(steps.get(i));
        }
        return out.toString();
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }
}
