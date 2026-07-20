(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.SPIRITKIN_OPENING_BUBBLE = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STATES = Object.freeze(["hidden", "entering", "visible", "dismissing", "cooldown"]);
  const PRIORITIES = Object.freeze({ safety: 500, recovery: 400, task: 300, care: 200, greeting: 100 });
  const DEFAULT_DURATIONS = Object.freeze({ safety: 12000, recovery: 10000, task: 9000, care: 8000, greeting: 6000 });

  function cleanText(value, limit) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function normalize(payload, now) {
    if (!payload || typeof payload !== "object") return null;
    const kind = cleanText(payload.kind, 32).toLowerCase();
    const bubbleId = cleanText(payload.bubble_id, 160);
    const text = cleanText(payload.text, 280);
    if (!bubbleId || !text || !Object.prototype.hasOwnProperty.call(PRIORITIES, kind)) return null;
    const action = payload.action && typeof payload.action === "object" ? {
      type: cleanText(payload.action.type, 48),
      label: cleanText(payload.action.label, 40) || "打开对话",
      prompt: cleanText(payload.action.prompt, 500),
      source_id: cleanText(payload.action.source_id, 160),
    } : null;
    return {
      bubble_id: bubbleId,
      kind,
      priority: Number(payload.priority || PRIORITIES[kind]),
      text,
      action,
      created_at: Number(payload.created_at || now / 1000),
      expires_at: Number(payload.expires_at || 0),
      motion_policy: cleanText(payload.motion_policy, 24) || "subtle",
      emotion: cleanText(payload.emotion, 32) || "neutral",
      action_hint: cleanText(payload.action_hint, 48),
      essential: payload.essential === true,
      requires_confirmation: payload.requires_confirmation !== false,
      duration_ms: Math.max(0, Number(payload.duration_ms || DEFAULT_DURATIONS[kind])),
    };
  }

  class OpeningBubbleController {
    constructor(options) {
      const opts = options || {};
      this.state = "hidden";
      this.current = null;
      this.queue = [];
      this.context = {};
      this.lastReason = "";
      this.now = typeof opts.now === "function" ? opts.now : () => Date.now();
      this.setTimer = typeof opts.setTimer === "function" ? opts.setTimer : (fn, ms) => setTimeout(fn, ms);
      this.clearTimer = typeof opts.clearTimer === "function" ? opts.clearTimer : id => clearTimeout(id);
      this.onStateChange = typeof opts.onStateChange === "function" ? opts.onStateChange : () => {};
      this.onFeedback = typeof opts.onFeedback === "function" ? opts.onFeedback : () => {};
      this.onAction = typeof opts.onAction === "function" ? opts.onAction : () => {};
      this.reducedMotion = typeof opts.reducedMotion === "function" ? opts.reducedMotion : () => false;
      this.storage = opts.storage || null;
      this.storageKey = opts.storageKey || "spiritkin_opening_bubble_seen_v1";
      this.enterTimer = null;
      this.dismissTimer = null;
      this.cooldownTimer = null;
      this.seen = this._loadSeen();
      this._notify();
    }

    present(rawPayload) {
      const now = this.now();
      const payload = normalize(rawPayload, now);
      if (!payload) return { accepted: false, reason: "invalid" };
      if (payload.expires_at && now / 1000 >= payload.expires_at) return { accepted: false, reason: "expired" };
      if (this.current && this.current.bubble_id === payload.bubble_id) return { accepted: false, reason: "duplicate" };
      if (this.seen.has(payload.bubble_id)) return { accepted: false, reason: "duplicate" };
      if (this._blocked()) {
        this._enqueue(payload);
        return { accepted: true, reason: "queued_context" };
      }
      if (this.current && payload.priority <= this.current.priority) {
        this._enqueue(payload);
        return { accepted: true, reason: "queued_priority" };
      }
      this._show(payload, this.current ? "superseded" : "presented");
      return { accepted: true, reason: "presented" };
    }

    dismiss(reason, options) {
      if (!this.current || !["entering", "visible"].includes(this.state)) return false;
      const opts = options || {};
      const bubble = this.current;
      this._clearTimers();
      this.lastReason = cleanText(reason, 48) || "dismissed";
      this._transition("dismissing");
      if (opts.feedback !== false) this.onFeedback(bubble, this.lastReason);
      const delay = this.reducedMotion() ? 0 : 160;
      this.dismissTimer = this.setTimer(() => {
        this.dismissTimer = null;
        this.current = null;
        this._transition("cooldown");
        this.cooldownTimer = this.setTimer(() => {
          this.cooldownTimer = null;
          this._transition("hidden");
          this._flushQueue();
        }, Math.max(0, Number(opts.cooldownMs ?? 300)));
      }, delay);
      return true;
    }

    activate() {
      if (!this.current || this.state !== "visible" || !this.current.action) return false;
      const bubble = this.current;
      this.onAction(bubble, bubble.action);
      this.onFeedback(bubble, "accepted");
      return this.dismiss("accepted", { feedback: false, cooldownMs: 0 });
    }

    setContext(nextContext) {
      this.context = { ...this.context, ...(nextContext || {}) };
      if (this._blocked() && this.current && ["entering", "visible"].includes(this.state)) {
        this._enqueue(this.current);
        this.dismiss("context_yield", { feedback: false, cooldownMs: 0 });
      } else if (!this._blocked() && this.state === "hidden") {
        this._flushQueue();
      }
      this._notify();
    }

    snapshot() {
      return {
        state: this.state,
        current: this.current ? { ...this.current, action: this.current.action ? { ...this.current.action } : null } : null,
        queue: this.queue.map(item => ({ ...item, action: item.action ? { ...item.action } : null })),
        context: { ...this.context },
        lastReason: this.lastReason,
      };
    }

    _show(payload, reason) {
      this._clearTimers();
      this.queue = this.queue.filter(item => item.bubble_id !== payload.bubble_id);
      this.current = payload;
      this.lastReason = reason;
      this.seen.add(payload.bubble_id);
      this._saveSeen();
      this._transition("entering");
      const enterDelay = this.reducedMotion() ? 0 : 180;
      this.enterTimer = this.setTimer(() => {
        this.enterTimer = null;
        if (!this.current || this.current.bubble_id !== payload.bubble_id) return;
        this._transition("visible");
        if (payload.duration_ms > 0) {
          this.dismissTimer = this.setTimer(() => {
            this.dismissTimer = null;
            this.dismiss("timeout", { feedback: true });
          }, payload.duration_ms);
        }
      }, enterDelay);
    }

    _enqueue(payload) {
      const withoutDuplicate = this.queue.filter(item => item.bubble_id !== payload.bubble_id);
      withoutDuplicate.push(payload);
      withoutDuplicate.sort((left, right) => right.priority - left.priority || right.created_at - left.created_at);
      this.queue = withoutDuplicate.slice(0, 8);
      this._notify();
    }

    _flushQueue() {
      if (this._blocked() || this.current || this.state !== "hidden") return;
      const now = this.now() / 1000;
      const nextIndex = this.queue.findIndex(item => !item.expires_at || item.expires_at > now);
      if (nextIndex < 0) {
        this.queue = [];
        this._notify();
        return;
      }
      const [next] = this.queue.splice(nextIndex, 1);
      this._show(next, "dequeued");
    }

    _blocked() {
      return Boolean(this.context.userTyping || this.context.confirmation || this.context.error || this.context.speaking);
    }

    _transition(nextState) {
      if (!STATES.includes(nextState)) throw new Error(`invalid opening bubble state: ${nextState}`);
      this.state = nextState;
      this._notify();
    }

    _clearTimers() {
      ["enterTimer", "dismissTimer", "cooldownTimer"].forEach(key => {
        if (this[key] !== null) this.clearTimer(this[key]);
        this[key] = null;
      });
    }

    _loadSeen() {
      if (!this.storage || typeof this.storage.getItem !== "function") return new Set();
      try {
        const values = JSON.parse(this.storage.getItem(this.storageKey) || "[]");
        return new Set(Array.isArray(values) ? values.map(value => cleanText(value, 160)).filter(Boolean) : []);
      } catch (_) {
        return new Set();
      }
    }

    _saveSeen() {
      if (!this.storage || typeof this.storage.setItem !== "function") return;
      try {
        this.storage.setItem(this.storageKey, JSON.stringify([...this.seen].slice(-64)));
      } catch (_) {
        // Storage can be disabled in private WebView profiles; in-memory dedupe still applies.
      }
    }

    _notify() {
      this.onStateChange(this.snapshot());
    }
  }

  return Object.freeze({ OpeningBubbleController, PRIORITIES, STATES, normalize });
});
