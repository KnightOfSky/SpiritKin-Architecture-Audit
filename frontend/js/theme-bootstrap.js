(function () {
  "use strict";
  const root = document.documentElement;
  const params = new URLSearchParams(location.search);
  const normalize = (value) => {
    const mode = String(value || "").trim().toLowerCase();
    return mode === "light" || mode === "dark" || mode === "system" ? mode : "";
  };
  const storedPreference = () => {
    try { return normalize(localStorage.getItem("spiritkin_theme_mode")); }
    catch (_) { return ""; }
  };
  const apply = (mode, options) => {
    const normalized = normalize(mode) || "system";
    if (options && options.persist) {
      try { localStorage.setItem("spiritkin_theme_mode", normalized); } catch (_) {}
    }
    if (normalized === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", normalized);
    root.style.colorScheme = normalized === "system" ? "light dark" : normalized;
    return normalized;
  };
  const explicitUser = storedPreference();
  const host = normalize(params.get("host_theme") || params.get("theme"));
  apply(explicitUser || host || "system");
  window.SPIRITKIN_THEME = Object.freeze({ apply, preference: explicitUser || "system", host });
  window.addEventListener("spiritkin.theme", (event) => {
    const hostTheme = normalize(event.detail && event.detail.theme);
    if (!storedPreference() && hostTheme) apply(hostTheme);
  });
})();
