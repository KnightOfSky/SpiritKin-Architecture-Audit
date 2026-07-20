"use strict";

const SETTINGS_KEY = "spiritkinPddSettings";
const RUNTIME_KEY = "spiritkinPddRuntime";
const RESULTS_KEY = "spiritkinPddResults";
const ALARM_NAME = "spiritkin-pdd-poll";
const DEFAULT_SETTINGS = {
  settingsVersion: 2,
  controlPlaneUrl: "http://127.0.0.1:8791",
  workspaceId: "default",
  extensionId: "",
  token: "",
  autoPoll: true,
  autoCloseTabs: true,
  claimLimit: 5,
  pageTimeoutMs: 45000
};
const DEFAULT_RUNTIME = {
  running: false,
  stopping: false,
  phase: "idle",
  current: null,
  total: 0,
  completed: 0,
  failed: 0,
  lastSyncAt: "",
  lastError: "",
  logs: []
};

let runPromise = null;

function nowIso() {
  return new Date().toISOString();
}

function normalizeBaseUrl(value) {
  return String(value || DEFAULT_SETTINGS.controlPlaneUrl).trim().replace(/\/+$/, "");
}

function newExtensionId() {
  return `browser-${crypto.randomUUID().slice(0, 12)}`;
}

async function getStored(key, fallback) {
  const data = await chrome.storage.local.get(key);
  return { ...fallback, ...(data[key] || {}) };
}

async function getSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_KEY);
  const persisted = stored[SETTINGS_KEY] || {};
  const settings = { ...DEFAULT_SETTINGS, ...persisted };
  let changed = false;
  if (Number(persisted.settingsVersion || 0) < 2) {
    settings.settingsVersion = 2;
    settings.autoPoll = true;
    changed = true;
  }
  if (!settings.extensionId) {
    settings.extensionId = newExtensionId();
    changed = true;
  }
  const controlPlaneUrl = normalizeBaseUrl(settings.controlPlaneUrl);
  if (settings.controlPlaneUrl !== controlPlaneUrl) {
    settings.controlPlaneUrl = controlPlaneUrl;
    changed = true;
  }
  if (changed) await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
  return settings;
}

async function saveSettings(next) {
  const current = await getSettings();
  const settings = {
    ...current,
    ...next,
    controlPlaneUrl: normalizeBaseUrl(next.controlPlaneUrl || current.controlPlaneUrl),
    claimLimit: Math.max(1, Math.min(20, Number(next.claimLimit || current.claimLimit || 5))),
    pageTimeoutMs: Math.max(15000, Math.min(120000, Number(next.pageTimeoutMs || current.pageTimeoutMs || 45000)))
  };
  await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
  await configureAlarm(settings);
  return settings;
}

async function getRuntime() {
  return getStored(RUNTIME_KEY, DEFAULT_RUNTIME);
}

async function setRuntime(patch) {
  const runtime = { ...(await getRuntime()), ...patch };
  await chrome.storage.local.set({ [RUNTIME_KEY]: runtime });
  chrome.runtime.sendMessage({ type: "SPIRITKIN_PDD_STATE_CHANGED" }).catch(() => {});
  return runtime;
}

async function addLog(message, level = "info") {
  const runtime = await getRuntime();
  const logs = [{ at: nowIso(), level, message: String(message) }, ...(runtime.logs || [])].slice(0, 80);
  return setRuntime({ logs });
}

async function getResults() {
  const data = await chrome.storage.local.get(RESULTS_KEY);
  return Array.isArray(data[RESULTS_KEY]) ? data[RESULTS_KEY] : [];
}

async function recordResult(entry) {
  const results = [{ ...entry, recordedAt: nowIso() }, ...(await getResults())].slice(0, 20);
  await chrome.storage.local.set({ [RESULTS_KEY]: results });
  return results;
}

async function publicState() {
  const settings = await getSettings();
  return {
    settings: { ...settings, token: "", hasToken: Boolean(settings.token) },
    runtime: await getRuntime(),
    results: await getResults()
  };
}

async function api(path, options = {}) {
  const settings = await getSettings();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (settings.token) headers.Authorization = `Bearer ${settings.token}`;
  const response = await fetch(`${settings.controlPlaneUrl}${path}`, { ...options, headers });
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = {};
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Control plane HTTP ${response.status}`);
  }
  return payload;
}

async function pairExtension(pairingToken) {
  const settings = await getSettings();
  const response = await fetch(`${settings.controlPlaneUrl}/extension/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pairing_token: String(pairingToken || "").trim(), device_id: settings.extensionId })
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `Pairing failed (${response.status})`);
  const binding = payload.binding || {};
  await saveSettings({ token: binding.token || pairingToken, workspaceId: binding.workspace_id || settings.workspaceId });
  await addLog(`Paired with workspace ${binding.workspace_id || settings.workspaceId}`, "success");
  runClaimedQueue().catch(() => {});
  return binding;
}

async function testConnection() {
  const payload = await api("/extension/status", { method: "GET" });
  await saveSettings({ workspaceId: payload.workspace_id || "default", extensionId: payload.extension_id || undefined });
  await addLog("Control plane connection verified", "success");
  return payload;
}

async function claimLinks() {
  const settings = await getSettings();
  return api("/extension/links/claim", {
    method: "POST",
    body: JSON.stringify({ limit: settings.claimLimit, extension_id: settings.extensionId, workspace_id: settings.workspaceId })
  });
}

function tabComplete(tabId, timeoutMs) {
  return new Promise((resolve, reject) => {
    let timer = null;
    const finish = (error, tab) => {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      if (error) reject(error); else resolve(tab);
    };
    const listener = (updatedId, changeInfo, tab) => {
      if (updatedId === tabId && changeInfo.status === "complete") finish(null, tab);
    };
    chrome.tabs.onUpdated.addListener(listener);
    timer = setTimeout(() => finish(new Error("PDD page load timed out")), timeoutMs);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab.status === "complete") finish(null, tab);
    }).catch((error) => finish(error));
  });
}

async function contentMessage(tabId, message, attempts = 20) {
  let lastError = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      await chrome.tabs.sendMessage(tabId, { type: "SPIRITKIN_PDD_PING" });
      return await chrome.tabs.sendMessage(tabId, message);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw lastError || new Error("PDD content script is unavailable");
}

function resultSummary(product, source) {
  const skus = product?.skuInfo?.skuList || [];
  return {
    source: source || product?.extraction?.source || "unknown",
    goods_id: String(product?.goodsId || ""),
    main_image_count: Array.isArray(product?.mainImages) ? product.mainImages.length : 0,
    detail_image_count: Array.isArray(product?.detailImages) ? product.detailImages.length : 0,
    sku_count: Array.isArray(skus) ? skus.length : 0,
    listing_gate_ok: Boolean(product?.listingGate?.ok)
  };
}

async function postExtraction(link, extraction) {
  const payload = {
    link_id: link.link_id,
    success: Boolean(extraction.success),
    product_data: extraction.product || undefined,
    summary: extraction.success
      ? resultSummary(extraction.product, extraction.source)
      : { error_code: extraction.errorCode || "extraction_failed" },
    error: extraction.error || ""
  };
  return api("/extension/results", { method: "POST", body: JSON.stringify(payload) });
}

async function processLink(link) {
  const settings = await getSettings();
  let tab = null;
  let extraction = null;
  try {
    await setRuntime({ current: { linkId: link.link_id, url: link.link, title: "Opening product page" }, phase: "opening" });
    tab = await chrome.tabs.create({ url: link.link, active: false });
    await tabComplete(tab.id, settings.pageTimeoutMs);
    await setRuntime({ current: { linkId: link.link_id, url: link.link, title: tab.title || "Extracting product" }, phase: "extracting" });
    extraction = await contentMessage(tab.id, { type: "SPIRITKIN_PDD_EXTRACT" });
    if (!extraction?.success) throw Object.assign(new Error(extraction?.error || "Product extraction failed"), { extraction });
    await postExtraction(link, extraction);
    await recordResult({ ok: true, linkId: link.link_id, url: link.link, summary: resultSummary(extraction.product, extraction.source), product: extraction.product });
    await addLog(`Extracted ${extraction.product.goodsId || link.link_id}`, "success");
    return { ok: true };
  } catch (error) {
    const failure = error.extraction || { success: false, error: String(error.message || error), errorCode: "queue_exception" };
    extraction = failure;
    await postExtraction(link, failure).catch(() => {});
    await recordResult({ ok: false, linkId: link.link_id, url: link.link, error: failure.error, errorCode: failure.errorCode });
    await addLog(failure.error || "Product extraction failed", "error");
    return { ok: false, needsLogin: Boolean(failure.needsLogin) };
  } finally {
    if (tab?.id && settings.autoCloseTabs && !extraction?.needsLogin) {
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
  }
}

async function runClaimedQueue() {
  if (runPromise) return runPromise;
  runPromise = (async () => {
    const claimed = await claimLinks();
    const links = Array.isArray(claimed.links) ? claimed.links : [];
    await setRuntime({ running: true, stopping: false, phase: links.length ? "queued" : "idle", total: links.length, completed: 0, failed: 0, lastSyncAt: nowIso(), lastError: "" });
    if (!links.length) await addLog("No pending PDD web links", "info");
    for (let index = 0; index < links.length; index += 1) {
      const runtime = await getRuntime();
      if (runtime.stopping) break;
      const outcome = await processLink(links[index]);
      const latest = await getRuntime();
      await setRuntime({
        completed: latest.completed + (outcome.ok ? 1 : 0),
        failed: latest.failed + (outcome.ok ? 0 : 1)
      });
      if (outcome.needsLogin) {
        await setRuntime({ stopping: true, lastError: "PDD login is required" });
        break;
      }
    }
    return setRuntime({ running: false, stopping: false, phase: "idle", current: null });
  })().catch(async (error) => {
    await addLog(error.message || String(error), "error");
    await setRuntime({ running: false, stopping: false, phase: "error", current: null, lastError: error.message || String(error) });
    throw error;
  }).finally(() => {
    runPromise = null;
  });
  return runPromise;
}

async function extractActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !/^https:\/\/[^/]*(yangkeduo|pinduoduo)\.com\//i.test(tab.url || "")) {
    throw new Error("Open a PDD product page before extracting the current tab");
  }
  const extraction = await contentMessage(tab.id, { type: "SPIRITKIN_PDD_EXTRACT" });
  if (!extraction?.success) throw new Error(extraction?.error || "Product extraction failed");
  await recordResult({ ok: true, linkId: "manual", url: tab.url, summary: resultSummary(extraction.product, extraction.source), product: extraction.product });
  await addLog(`Extracted current tab ${extraction.product.goodsId || ""}`, "success");
  return extraction;
}

async function exportLatest() {
  const latest = (await getResults()).find((item) => item.ok && item.product);
  if (!latest) throw new Error("No extracted product is available to export");
  const dataUrl = `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(latest.product, null, 2))}`;
  return chrome.downloads.download({ url: dataUrl, filename: `spiritkin-pdd-${latest.product.goodsId || "product"}.json`, saveAs: true });
}

async function requeueLink(linkId) {
  await api("/extension/links/requeue", { method: "POST", body: JSON.stringify({ link_id: linkId }) });
  await addLog(`Requeued ${linkId}`, "info");
  return publicState();
}

async function configureAlarm(settings) {
  await chrome.alarms.clear(ALARM_NAME);
  if (settings.autoPoll) chrome.alarms.create(ALARM_NAME, { periodInMinutes: 1 });
}

chrome.runtime.onInstalled.addListener(async () => {
  await getSettings();
  await setRuntime(DEFAULT_RUNTIME);
  await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  await configureAlarm(await getSettings());
});

chrome.runtime.onStartup.addListener(async () => {
  await configureAlarm(await getSettings());
  runClaimedQueue().catch(() => {});
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) runClaimedQueue().catch(() => {});
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const tasks = {
    SPIRITKIN_PDD_GET_STATE: () => publicState(),
    SPIRITKIN_PDD_SAVE_SETTINGS: () => saveSettings(message.settings || {}).then(publicState),
    SPIRITKIN_PDD_PAIR: () => pairExtension(message.pairingToken).then(publicState),
    SPIRITKIN_PDD_TEST_CONNECTION: () => testConnection().then(publicState),
    SPIRITKIN_PDD_SYNC: async () => {
      runClaimedQueue().catch(() => {});
      await new Promise((resolve) => setTimeout(resolve, 50));
      return publicState();
    },
    SPIRITKIN_PDD_STOP: () => setRuntime({ stopping: true }).then(publicState),
    SPIRITKIN_PDD_EXTRACT_ACTIVE: () => extractActiveTab().then(publicState),
    SPIRITKIN_PDD_EXPORT_LATEST: () => exportLatest().then(publicState),
    SPIRITKIN_PDD_REQUEUE: () => requeueLink(message.linkId)
  };
  const task = tasks[message?.type];
  if (!task) return false;
  task().then((data) => sendResponse({ ok: true, data })).catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});
