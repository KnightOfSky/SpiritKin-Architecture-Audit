"use strict";

const extensionApi = typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id;
const $ = (selector) => document.querySelector(selector);
let state = null;

const mockState = {
  settings: {
    controlPlaneUrl: "http://127.0.0.1:8791",
    workspaceId: "local-ecommerce",
    extensionId: "browser-preview",
    hasToken: true,
    autoPoll: true,
    autoCloseTabs: true,
    claimLimit: 5
  },
  runtime: {
    running: true,
    phase: "extracting",
    total: 3,
    completed: 1,
    failed: 0,
    lastSyncAt: new Date().toISOString(),
    current: { title: "实木沙发现代组合简约小户型", url: "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283" },
    logs: [
      { at: new Date().toISOString(), level: "success", message: "Extracted 680378531283" },
      { at: new Date(Date.now() - 60000).toISOString(), level: "info", message: "Claimed 3 PDD web links" }
    ]
  },
  results: [
    {
      ok: true,
      recordedAt: new Date().toISOString(),
      summary: { goods_id: "680378531283", source: "rawData", main_image_count: 8, detail_image_count: 26, sku_count: 12, listing_gate_ok: true },
      product: { title: "实木沙发现代组合简约小户型客厅沙发", goodsId: "680378531283" }
    }
  ]
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function send(type, payload = {}) {
  if (!extensionApi) return Promise.resolve({ ok: true, data: mockState });
  return chrome.runtime.sendMessage({ type, ...payload }).then((response) => {
    if (!response?.ok) throw new Error(response?.error || "Extension request failed");
    return response;
  });
}

async function ensureOriginPermission() {
  if (!extensionApi || !chrome.permissions) return true;
  const raw = $("#controlPlaneUrl").value.trim();
  const origin = `${new URL(raw).origin}/*`;
  const granted = await chrome.permissions.contains({ origins: [origin] });
  return granted || chrome.permissions.request({ origins: [origin] });
}

function timeLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function phaseLabel(phase) {
  return ({ idle: "空闲", queued: "已领取", opening: "打开商品页", extracting: "提取 rawData", error: "需要处理" })[phase] || phase || "空闲";
}

function render(nextState) {
  state = nextState || mockState;
  const { settings, runtime, results } = state;
  const connected = settings.hasToken && !runtime.lastError;
  const connection = $("#connectionState");
  connection.className = `connection ${connected ? "connected" : runtime.lastError ? "error" : ""}`;
  connection.lastElementChild.textContent = connected ? settings.workspaceId || "已连接" : runtime.lastError ? "连接异常" : "未配对";
  $("#serviceText").textContent = connected
    ? `${settings.workspaceId} · ${settings.controlPlaneUrl}`
    : "使用 browser_extension Token 与控制面配对";
  $("#serviceBand").classList.toggle("error", Boolean(runtime.lastError));

  $("#syncBtn").disabled = runtime.running || !settings.hasToken;
  $("#stopBtn").classList.toggle("hidden", !runtime.running);
  $("#phaseText").textContent = phaseLabel(runtime.phase);
  $("#progressText").textContent = `${runtime.completed + runtime.failed} / ${runtime.total}`;
  const progress = runtime.total ? ((runtime.completed + runtime.failed) / runtime.total) * 100 : 0;
  $("#progressFill").style.width = `${Math.max(0, Math.min(100, progress))}%`;
  $("#totalMetric").textContent = runtime.total || 0;
  $("#completedMetric").textContent = runtime.completed || 0;
  $("#failedMetric").textContent = runtime.failed || 0;
  $("#lastSyncText").textContent = runtime.lastSyncAt ? `同步于 ${timeLabel(runtime.lastSyncAt)}` : "尚未同步";

  const current = $("#currentTask");
  current.classList.toggle("running", Boolean(runtime.current));
  current.innerHTML = runtime.current
    ? `<span class="task-marker" aria-hidden="true"></span><div><strong>${escapeHtml(runtime.current.title || "正在处理商品")}</strong><p>${escapeHtml(runtime.current.url || "")}</p></div>`
    : '<span class="task-marker" aria-hidden="true"></span><div><strong>暂无运行任务</strong><p>仅处理 yangkeduo.com / pinduoduo.com 网页链接</p></div>';

  const logs = Array.isArray(runtime.logs) ? runtime.logs : [];
  $("#logList").innerHTML = logs.length
    ? logs.map((item) => `<div class="log-row ${escapeHtml(item.level)}"><time>${escapeHtml(timeLabel(item.at))}</time><span>${escapeHtml(item.message)}</span></div>`).join("")
    : '<div class="empty-state">暂无运行记录</div>';

  $("#resultCount").textContent = results.length;
  $("#resultList").innerHTML = results.length ? results.map(resultHtml).join("") : '<div class="empty-state">暂无抓取结果</div>';
  $("#exportBtn").disabled = !results.some((item) => item.ok && item.product);

  $("#controlPlaneUrl").value = settings.controlPlaneUrl || "";
  $("#workspaceId").value = settings.workspaceId || "";
  $("#claimLimit").value = settings.claimLimit || 5;
  $("#autoPoll").checked = Boolean(settings.autoPoll);
  $("#autoCloseTabs").checked = Boolean(settings.autoCloseTabs);
  $("#extensionIdentity").textContent = settings.extensionId || "浏览器扩展尚未初始化";
  $("#pairingState").textContent = settings.hasToken ? `已绑定 ${settings.workspaceId}` : "仅保存扩展配对 Token";
}

function resultHtml(item) {
  if (!item.ok) {
    return `<article class="result-item"><div class="result-head"><div class="result-title"><strong>抓取失败</strong><span>${escapeHtml(item.error || item.errorCode || "unknown error")}</span></div><span class="state-label failed">失败</span></div><div class="result-actions"><button class="text-button" data-requeue="${escapeHtml(item.linkId || "")}">重新排队</button></div></article>`;
  }
  const summary = item.summary || {};
  const title = item.product?.title || summary.goods_id || "商品结果";
  return `<article class="result-item"><div class="result-head"><div class="result-title"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(summary.goods_id)} · ${escapeHtml(summary.source || "unknown")}</span></div><span class="state-label ${summary.listing_gate_ok ? "ready" : ""}">${summary.listing_gate_ok ? "可进入上架" : "数据待补"}</span></div><div class="result-meta"><span>主图 ${Number(summary.main_image_count || 0)}</span><span>详情 ${Number(summary.detail_image_count || 0)}</span><span>SKU ${Number(summary.sku_count || 0)}</span></div></article>`;
}

async function refresh() {
  const response = await send("SPIRITKIN_PDD_GET_STATE");
  render(response.data);
}

async function runAction(button, type, payload = {}) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "处理中...";
  try {
    const response = await send(type, payload);
    render(response.data || state);
    return response;
  } catch (error) {
    $("#formMessage").textContent = error.message;
    $("#formMessage").className = "form-message error";
    throw error;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => {
      const active = item === tab;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${tab.dataset.tab}`));
  });
});

$("#testConnectionBtn").addEventListener("click", async (event) => {
  await ensureOriginPermission();
  await send("SPIRITKIN_PDD_SAVE_SETTINGS", { settings: { controlPlaneUrl: $("#controlPlaneUrl").value.trim() } });
  await runAction(event.currentTarget, "SPIRITKIN_PDD_TEST_CONNECTION");
});
$("#syncBtn").addEventListener("click", (event) => runAction(event.currentTarget, "SPIRITKIN_PDD_SYNC"));
$("#stopBtn").addEventListener("click", (event) => runAction(event.currentTarget, "SPIRITKIN_PDD_STOP"));
$("#extractCurrentBtn").addEventListener("click", (event) => runAction(event.currentTarget, "SPIRITKIN_PDD_EXTRACT_ACTIVE"));
$("#openLoginBtn").addEventListener("click", () => {
  if (extensionApi) chrome.tabs.create({ url: "https://mobile.yangkeduo.com/personal.html", active: true });
});
$("#exportBtn").addEventListener("click", (event) => runAction(event.currentTarget, "SPIRITKIN_PDD_EXPORT_LATEST"));
$("#clearLogsBtn").addEventListener("click", async () => {
  if (extensionApi) {
    const runtime = { ...(state.runtime || {}), logs: [] };
    await chrome.storage.local.set({ spiritkinPddRuntime: runtime });
  }
  state.runtime.logs = [];
  render(state);
});
$("#pairBtn").addEventListener("click", async (event) => {
  await ensureOriginPermission();
  const pairingToken = $("#pairingToken").value.trim();
  if (!pairingToken) {
    $("#formMessage").textContent = "请输入一次性配对 Token";
    $("#formMessage").className = "form-message error";
    return;
  }
  await send("SPIRITKIN_PDD_SAVE_SETTINGS", { settings: { controlPlaneUrl: $("#controlPlaneUrl").value.trim() } });
  await runAction(event.currentTarget, "SPIRITKIN_PDD_PAIR", { pairingToken });
  $("#pairingToken").value = "";
});
$("#resultList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-requeue]");
  if (!button || !button.dataset.requeue) return;
  await runAction(button, "SPIRITKIN_PDD_REQUEUE", { linkId: button.dataset.requeue });
});
$("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await ensureOriginPermission();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  await runAction(button, "SPIRITKIN_PDD_SAVE_SETTINGS", {
    settings: {
      controlPlaneUrl: $("#controlPlaneUrl").value.trim(),
      claimLimit: Number($("#claimLimit").value),
      autoPoll: $("#autoPoll").checked,
      autoCloseTabs: $("#autoCloseTabs").checked
    }
  });
  $("#formMessage").textContent = "设置已保存";
  $("#formMessage").className = "form-message";
});

if (extensionApi) {
  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "SPIRITKIN_PDD_STATE_CHANGED") refresh().catch(() => {});
  });
  chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
    const supported = /^https:\/\/[^/]*(yangkeduo|pinduoduo)\.com\//i.test(tab?.url || "");
    $("#activePageState").textContent = supported ? "PDD 商品页" : "非 PDD 商品页";
    $("#extractCurrentBtn").disabled = !supported;
  });
}

refresh().catch((error) => {
  render(mockState);
  $("#serviceText").textContent = error.message;
});
