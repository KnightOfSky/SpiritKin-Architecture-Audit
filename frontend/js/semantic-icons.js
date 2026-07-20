(function () {
  "use strict";

  const names = Object.freeze({
    "action.add": "plus",
    "action.close": "x",
    "action.more": "ellipsis",
    "action.refresh": "refresh-cw",
    "action.search": "search",
    "action.send": "send",
    "action.attach": "paperclip",
    "action.copy": "copy",
    "action.edit": "pencil",
    "action.delete": "trash-2",
    "action.play": "play",
    "action.stop": "square",
    "action.resume": "rotate-ccw",
    "action.settings": "settings",
    "action.terminal": "terminal",
    "navigation.expand": "chevron-down",
    "navigation.collapse": "chevron-up",
    "navigation.back": "arrow-left",
    "navigation.forward": "arrow-right",
    "entity.chat": "message-square",
    "entity.project": "folder",
    "entity.workflow": "workflow",
    "entity.mobile": "smartphone",
    "state.info": "info",
    "state.success": "circle-check",
    "state.warning": "triangle-alert",
    "state.danger": "circle-x",
    "state.unknown": "circle-help",
    "state.loading": "loader-circle"
  });

  function iconUrl(semanticId) {
    const name = names[semanticId];
    return name ? `icons/lucide/${name}.svg` : "";
  }

  function hydrate(root = document) {
    root.querySelectorAll("[data-semantic-icon]").forEach((control) => {
      if (control.querySelector(":scope > .semantic-icon")) return;
      const url = iconUrl(control.dataset.semanticIcon);
      if (!url) return;
      const icon = document.createElement("span");
      icon.className = "semantic-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.style.setProperty("--semantic-icon-url", `url(\"${url}\")`);
      control.prepend(icon);
    });
  }

  window.SPIRITKIN_ICONS = Object.freeze({ names, iconUrl, hydrate });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => hydrate(), { once: true });
  } else {
    hydrate();
  }
})();
