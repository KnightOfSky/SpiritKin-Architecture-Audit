(function () {
  "use strict";

  const RESULT_ID = "__spiritkin_pdd_result__";
  const REQUEST_EVENT = "spiritkin-pdd-extract-request";
  const RESULT_EVENT = "spiritkin-pdd-extract-result";

  function imageUrls(selector) {
    return Array.from(document.querySelectorAll(selector))
      .map((image) => image.currentSrc || image.src || image.dataset.src || "")
      .filter((url, index, values) => /^https?:\/\//i.test(url) && values.indexOf(url) === index);
  }

  function goodsIdFromUrl() {
    const url = new URL(window.location.href);
    return url.searchParams.get("goods_id") || "";
  }

  function domFallback() {
    const title = document.querySelector('meta[property="og:title"]')?.content
      || document.querySelector("h1")?.textContent?.trim()
      || document.title.replace(/\s*[-_|].*$/, "").trim();
    const mainImages = imageUrls('.QFNLpbqP img, img[src*="pddpic"]');
    const detailImages = imageUrls('.Blmqu2TV img, [class*="detail"] img');
    const priceText = document.querySelector('[class*="price"]')?.textContent || "";
    const priceMatch = priceText.match(/\d+(?:\.\d+)?/);
    const product = {
      schema: "spiritkin.pdd_product_data.v1",
      url: window.location.href,
      goodsId: goodsIdFromUrl(),
      title: title || "",
      price: priceMatch ? Number(priceMatch[0]) : null,
      originalPrice: null,
      mainImage: mainImages[0] || "",
      images: mainImages,
      mainImages,
      detailImages,
      attributes: [],
      skuInfo: { hasValidSku: false, specifications: [], skuList: [] },
      skus: [],
      extraction: {
        source: "DOM",
        extractedAt: new Date().toISOString(),
        migrationSource: "AutoProcessAP/pdd-batch-extractor"
      }
    };
    product.listingGate = {
      ok: false,
      checks: { rawData: false, numericGoodsId: /^\d+$/.test(product.goodsId), title: Boolean(product.title) },
      missing: ["rawData", "skuPresent", "skuPriceComplete", "skuStockComplete"]
    };
    return product;
  }

  function enrichRawProduct(product) {
    const mainImages = Array.from(new Set([...(product.mainImages || []), ...imageUrls('.QFNLpbqP img, img[src*="pddpic"]')]));
    const detailImages = Array.from(new Set([...(product.detailImages || []), ...imageUrls('.Blmqu2TV img, [class*="detail"] img')]));
    product.mainImages = mainImages;
    product.images = mainImages;
    product.mainImage = mainImages[0] || product.mainImage || "";
    product.detailImages = detailImages;
    const skuList = product.skuInfo?.skuList || [];
    const checks = {
      rawData: true,
      numericGoodsId: /^\d+$/.test(String(product.goodsId || "")),
      title: Boolean(String(product.title || "").trim()),
      mainImages: mainImages.length >= 2,
      detailImages: detailImages.length >= 1,
      skuPresent: skuList.length > 0,
      skuPriceComplete: skuList.length > 0 && skuList.every((sku) => Number.isFinite(sku.price)),
      skuStockComplete: skuList.length > 0 && skuList.every((sku) => Number.isFinite(sku.stock))
    };
    const missing = Object.keys(checks).filter((key) => !checks[key]);
    product.listingGate = { ok: missing.length === 0, checks, missing };
    return product;
  }

  function requestRawData(timeoutMs = 10000) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (payload) => {
        if (settled) return;
        settled = true;
        window.removeEventListener(RESULT_EVENT, onResult);
        resolve(payload);
      };
      const onResult = () => {
        try {
          const node = document.getElementById(RESULT_ID);
          finish(JSON.parse(node?.textContent || "{}"));
        } catch (error) {
          finish({ ok: false, error: String(error), errorCode: "bridge_decode_failed" });
        }
      };
      window.addEventListener(RESULT_EVENT, onResult, { once: true });
      window.dispatchEvent(new CustomEvent(REQUEST_EVENT));
      setTimeout(() => finish({ ok: false, error: "rawData bridge timed out", errorCode: "bridge_timeout" }), timeoutMs);
    });
  }

  async function extractProduct() {
    const raw = await requestRawData();
    if (raw.ok && raw.product) return { success: true, product: enrichRawProduct(raw.product), source: "rawData" };
    if (raw.errorCode === "login_required") {
      return { success: false, error: raw.error, errorCode: raw.errorCode, needsLogin: true };
    }
    const fallback = domFallback();
    if (fallback.title && fallback.goodsId) {
      return { success: true, product: fallback, source: "DOM", warning: raw.error || "rawData unavailable" };
    }
    return { success: false, error: raw.error || "No usable product data found", errorCode: raw.errorCode || "extraction_failed" };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "SPIRITKIN_PDD_PING") {
      sendResponse({ ok: true, url: window.location.href });
      return false;
    }
    if (message?.type === "SPIRITKIN_PDD_EXTRACT") {
      extractProduct().then(sendResponse).catch((error) => {
        sendResponse({ success: false, error: String(error), errorCode: "content_exception" });
      });
      return true;
    }
    return false;
  });
})();
