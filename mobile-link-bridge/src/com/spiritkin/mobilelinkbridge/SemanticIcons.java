package com.spiritkin.mobilelinkbridge;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

public final class SemanticIcons {
    private static final Map<String, String> NAMES;

    static {
        Map<String, String> names = new LinkedHashMap<>();
        names.put("action.add", "add"); names.put("action.close", "close"); names.put("action.more", "more_horiz");
        names.put("action.refresh", "refresh"); names.put("action.search", "search"); names.put("action.send", "send");
        names.put("action.attach", "attach_file"); names.put("action.copy", "content_copy"); names.put("action.edit", "edit");
        names.put("action.delete", "delete"); names.put("action.play", "play_arrow"); names.put("action.stop", "stop");
        names.put("action.resume", "restart_alt"); names.put("action.settings", "settings"); names.put("action.terminal", "terminal");
        names.put("navigation.expand", "expand_more"); names.put("navigation.collapse", "expand_less");
        names.put("navigation.back", "arrow_back"); names.put("navigation.forward", "arrow_forward");
        names.put("entity.chat", "chat_bubble"); names.put("entity.project", "folder"); names.put("entity.workflow", "account_tree");
        names.put("entity.mobile", "smartphone"); names.put("state.info", "info"); names.put("state.success", "check_circle");
        names.put("state.warning", "warning"); names.put("state.danger", "error"); names.put("state.unknown", "help");
        names.put("state.loading", "progress_activity");
        NAMES = Collections.unmodifiableMap(names);
    }

    private SemanticIcons() {}

    public static String materialName(String semanticId) {
        String name = NAMES.get(semanticId);
        return name != null ? name : NAMES.get("state.unknown");
    }
}
