(function (root) {
  "use strict";

  function firstValue(source, keys, fallback = null) {
    for (const key of keys) {
      const value = source && source[key];
      if (value !== undefined && value !== null && value !== "") return value;
    }
    return fallback;
  }

  function numeric(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(String(value).replace(/[^0-9.-]/g, ""));
    return Number.isFinite(parsed) ? parsed : null;
  }

  function fen(value) {
    const parsed = numeric(value);
    return parsed === null ? null : Math.round(parsed) / 100;
  }

  function urlFrom(value) {
    if (typeof value === "string") return value;
    if (!value || typeof value !== "object") return "";
    return String(firstValue(value, ["url", "imageUrl", "image_url", "thumbUrl", "thumb_url", "src"], "") || "");
  }

  function uniqueUrls(values) {
    const output = [];
    for (const value of Array.isArray(values) ? values : []) {
      const url = urlFrom(value).trim();
      if (!/^https?:\/\//i.test(url) || output.includes(url)) continue;
      output.push(url);
    }
    return output;
  }

  function normalizeSpecs(rawSpecs) {
    return (Array.isArray(rawSpecs) ? rawSpecs : [])
      .map((spec) => {
        if (typeof spec === "string") return { specName: "规格", optionName: spec, image: "" };
        if (!spec || typeof spec !== "object") return null;
        const specName = String(firstValue(spec, ["spec_key", "specKey", "name", "key"], "规格") || "规格");
        const optionName = String(firstValue(spec, ["spec_value", "specValue", "value", "label"], "") || "");
        if (!optionName) return null;
        return { specName, optionName, image: urlFrom(firstValue(spec, ["spec_image", "specImage", "image"], "")) };
      })
      .filter(Boolean);
  }

  function normalizeSkus(goods) {
    const rawSkus = firstValue(goods, ["skus", "skuList", "sku_list"], []);
    const skuList = (Array.isArray(rawSkus) ? rawSkus : []).map((sku, index) => {
      const specifications = normalizeSpecs(sku && sku.specs);
      const rawPrice = firstValue(sku, ["groupPrice", "group_price", "price"], null);
      const rawStock = firstValue(sku, ["quantity", "stock", "inventory"], null);
      return {
        id: String(firstValue(sku, ["skuId", "skuID", "sku_id", "id"], `sku_${index + 1}`)),
        combination: specifications.map((item) => item.optionName).join(" - ") || `SKU ${index + 1}`,
        price: rawPrice === null ? null : fen(rawPrice),
        stock: numeric(rawStock),
        active: firstValue(sku, ["isOnsale", "is_onsale"], 1) !== 0,
        image: urlFrom(firstValue(sku, ["thumbUrl", "thumb_url", "image"], "")),
        specifications
      };
    });
    const groups = new Map();
    for (const sku of skuList) {
      for (const spec of sku.specifications) {
        if (!groups.has(spec.specName)) groups.set(spec.specName, []);
        const options = groups.get(spec.specName);
        if (!options.some((item) => item.name === spec.optionName)) {
          options.push({ name: spec.optionName, image: spec.image || "" });
        }
      }
    }
    return {
      hasValidSku: skuList.length > 0,
      specifications: Array.from(groups, ([name, options]) => ({ name, options })),
      skuList
    };
  }

  function validateProduct(product, source) {
    const goodsId = String(product.goodsId || "");
    const skuList = product.skuInfo && Array.isArray(product.skuInfo.skuList) ? product.skuInfo.skuList : [];
    const checks = {
      rawData: source === "rawData",
      numericGoodsId: /^\d+$/.test(goodsId),
      title: Boolean(String(product.title || "").trim()),
      mainImages: Array.isArray(product.mainImages) && product.mainImages.length >= 2,
      detailImages: Array.isArray(product.detailImages) && product.detailImages.length >= 1,
      skuPresent: skuList.length > 0,
      skuPriceComplete: skuList.length > 0 && skuList.every((sku) => Number.isFinite(sku.price)),
      skuStockComplete: skuList.length > 0 && skuList.every((sku) => Number.isFinite(sku.stock))
    };
    const required = ["rawData", "numericGoodsId", "title", "mainImages", "detailImages", "skuPresent", "skuPriceComplete", "skuStockComplete"];
    const missing = required.filter((key) => !checks[key]);
    return { ok: missing.length === 0, checks, missing };
  }

  function normalizeRawData(rawData, pageUrl) {
    const initData = rawData && rawData.store && rawData.store.initDataObj;
    if (!initData || initData.needLogin) {
      const error = new Error(initData && initData.needLogin ? "PDD login is required" : "window.rawData is unavailable");
      error.code = initData && initData.needLogin ? "login_required" : "raw_data_unavailable";
      throw error;
    }
    const goods = initData.goods || initData.goodsDetail || initData.product;
    if (!goods || typeof goods !== "object") {
      const error = new Error("PDD goods payload is unavailable");
      error.code = "goods_payload_unavailable";
      throw error;
    }
    const mainImages = uniqueUrls(firstValue(goods, ["banner", "images", "gallery", "topGallery"], []));
    const detailImages = uniqueUrls(firstValue(goods, ["detailGallery", "detail_gallery", "detailImages", "detail_images"], []));
    const skuInfo = normalizeSkus(goods);
    const product = {
      schema: "spiritkin.pdd_product_data.v1",
      url: String(pageUrl || ""),
      goodsId: String(firstValue(goods, ["goodsID", "goodsId", "goods_id", "id"], "")),
      title: String(firstValue(goods, ["goodsName", "goods_name", "title", "name"], "")),
      price: fen(firstValue(goods, ["minGroupPrice", "min_group_price", "groupPrice"], null)),
      originalPrice: fen(firstValue(goods, ["marketPrice", "market_price", "normalPrice"], null)),
      sales: firstValue(goods, ["salesTip", "sales_tip", "sales"], ""),
      seller: firstValue(goods, ["mallName", "mall_name", "shopName"], ""),
      mainImage: mainImages[0] || "",
      images: mainImages,
      mainImages,
      detailImages,
      videoUrl: urlFrom(firstValue(goods, ["videoUrl", "video_url"], "")),
      attributes: firstValue(goods, ["goodsProperty", "goods_property", "properties"], []),
      skuInfo,
      skus: skuInfo.skuList,
      extraction: {
        source: "rawData",
        extractedAt: new Date().toISOString(),
        migrationSource: "AutoProcessAP/pdd-batch-extractor"
      }
    };
    product.listingGate = validateProduct(product, "rawData");
    return product;
  }

  root.SpiritKinPddNormalizer = {
    normalizeRawData,
    normalizeSkus,
    uniqueUrls,
    validateProduct
  };
})(typeof globalThis !== "undefined" ? globalThis : window);
