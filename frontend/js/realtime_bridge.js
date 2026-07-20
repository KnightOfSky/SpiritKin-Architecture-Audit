// SpiritKin realtime bridge — shared WS reconnect / command POST / URL helpers.
// Classic script (works for classic pages, module pages read window.SPIRITKIN_BRIDGE,
// and WebView2 embeds). Load after js/realtime_contract.js.
// TODO(debt-#9/#10): frontend has no build system (no package.json; CDN importmap)
// and zero JS tests — shared code is limited to window-global scripts like this
// one until a bundler/test runner is introduced.
(function (root) {
  "use strict";

  const contract = root.SPIRITKIN_CONTRACT || null;
  const ports = (contract && contract.defaultPorts) || {
    frontend: 8787,
    event_bridge: 8765,
    command_gateway: 8788,
  };

  function defaultHost() {
    return (root.location && root.location.hostname) || "127.0.0.1";
  }

  function defaultWsUrl() {
    return `ws://${defaultHost()}:${ports.event_bridge}`;
  }

  function defaultCommandUrl() {
    return `http://${defaultHost()}:${ports.command_gateway}/command`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  // Backend accepts either header; send both so every gateway build works.
  function commandHeaders(token, extra) {
    const headers = { "Content-Type": "application/json", ...(extra || {}) };
    const value = (token || "").trim();
    if (value) {
      headers["X-SpiritKin-Token"] = value;
      headers.Authorization = `Bearer ${value}`;
    }
    return headers;
  }

  // Non-throwing on HTTP-level failure: callers (avatar_3d) must still apply
  // d.events / d.reply from error payloads. Network / JSON parse errors throw.
  async function postCommand(url, body, options) {
    const opts = options || {};
    const response = await fetch(url, {
      method: "POST",
      headers: commandHeaders(opts.token, opts.headers),
      body: JSON.stringify(body),
    });
    const data = response.status === 204 ? { ok: true } : await response.json();
    return { ok: !!(response.ok && data.ok), status: response.status, data };
  }

  // Unified reconnect semantics (exponential backoff, capped at 10s).
  function createRealtimeConnection(options) {
    const getUrl = options.getUrl;
    const onEvent = options.onEvent;
    const onOpen = options.onOpen || null;
    const onStatus = options.onStatus || function () {};
    const getToken = options.getToken || function () { return ""; };

    let socket = null;
    let reconnectTimer = null;
    let reconnectAttempts = 0;
    let manualClose = false;
    let authenticated = false;

    function scheduleReconnect() {
      if (manualClose || reconnectTimer) return;
      const baseDelayMs = Math.min(30000, 1000 * Math.pow(1.8, reconnectAttempts));
      const delayMs = Math.round(baseDelayMs * (0.85 + Math.random() * 0.3));
      reconnectAttempts += 1;
      onStatus("reconnecting", { delayMs, attempts: reconnectAttempts });
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        open(true);
      }, delayMs);
    }

    function detach(ws) {
      if (!ws) return;
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
    }

    function open(isRetry) {
      manualClose = false;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (socket) {
        detach(socket);
        try {
          socket.close();
        } catch {
          /* already closed */
        }
        socket = null;
      }
      const url = getUrl();
      authenticated = false;
      onStatus("connecting", { url, isRetry: !!isRetry });
      let ws;
      try {
        ws = new WebSocket(url);
      } catch (err) {
        onStatus("error", { url, error: err });
        scheduleReconnect();
        return;
      }
      socket = ws;
      ws.onopen = () => {
        const token = String(getToken() || "").trim();
        ws.send(JSON.stringify({ type: "runtime.auth", token }));
        onStatus("authenticating", { url });
      };
      ws.onmessage = (evt) => {
        try {
          const event = JSON.parse(evt.data);
          if (!authenticated) {
            authenticated = true;
            reconnectAttempts = 0;
            onStatus("connected", { url });
            if (onOpen) Promise.resolve(onOpen(ws)).catch(() => {});
          }
          onEvent(event, evt);
        } catch {
          /* malformed frame — ignore, matches prior per-page behavior */
        }
      };
      ws.onerror = () => {
        onStatus("error", { url });
      };
      ws.onclose = (event) => {
        if (socket === ws) socket = null;
        authenticated = false;
        if (event.code === 1008) {
          manualClose = true;
          onStatus("unauthorized", { url, code: event.code, reason: event.reason });
          return;
        }
        scheduleReconnect();
      };
    }

    function close() {
      manualClose = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (socket) {
        detach(socket);
        try {
          socket.close();
        } catch {
          /* already closed */
        }
        socket = null;
      }
    }

    return {
      connect(isRetry) {
        open(!!isRetry);
      },
      close,
      send(payload) {
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send(typeof payload === "string" ? payload : JSON.stringify(payload));
          return true;
        }
        return false;
      },
      get connected() {
        return !!socket && socket.readyState === WebSocket.OPEN;
      },
    };
  }

  root.SPIRITKIN_BRIDGE = Object.freeze({
    defaultHost,
    defaultWsUrl,
    defaultCommandUrl,
    escapeHtml,
    commandHeaders,
    postCommand,
    createRealtimeConnection,
  });
})(typeof window !== "undefined" ? window : globalThis);
