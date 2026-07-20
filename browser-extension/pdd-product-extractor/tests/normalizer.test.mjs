import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../normalizer.js", import.meta.url), "utf8");
const context = vm.createContext({ console, Date });
vm.runInContext(source, context);
const normalizer = context.SpiritKinPddNormalizer;

function rawGoods(overrides = {}) {
  return {
    store: {
      initDataObj: {
        goods: {
          goodsID: 680378531283,
          goodsName: "实木沙发现代组合简约小户型",
          minGroupPrice: 26000,
          marketPrice: 60000,
          banner: ["https://img.test/1.jpg", "https://img.test/2.jpg", "https://img.test/1.jpg"],
          detailGallery: [{ url: "https://img.test/detail.jpg" }],
          skus: [
            {
              skuId: 1,
              groupPrice: 26000,
              quantity: 7,
              specs: [{ spec_key: "尺寸", spec_value: "双人位" }]
            },
            {
              skuId: 2,
              groupPrice: 28000,
              quantity: 0,
              specs: [{ spec_key: "尺寸", spec_value: "三人位" }]
            }
          ],
          ...overrides
        }
      }
    }
  };
}

test("normalizes AutoProcess rawData contract without duplicate images", () => {
  const product = normalizer.normalizeRawData(rawGoods(), "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283");

  assert.equal(product.goodsId, "680378531283");
  assert.equal(product.price, 260);
  assert.equal(product.originalPrice, 600);
  assert.deepEqual(Array.from(product.mainImages), ["https://img.test/1.jpg", "https://img.test/2.jpg"]);
  assert.equal(product.skuInfo.skuList[1].stock, 0);
  assert.equal(product.skuInfo.specifications[0].name, "尺寸");
  assert.equal(product.listingGate.ok, true);
});

test("does not invent stock when rawData omits inventory", () => {
  const product = normalizer.normalizeRawData(
    rawGoods({ skus: [{ skuId: 1, groupPrice: 26000, specs: [{ spec_key: "颜色", spec_value: "原木" }] }] }),
    "https://mobile.yangkeduo.com/goods.html?goods_id=680378531283"
  );

  assert.equal(product.skuInfo.skuList[0].stock, null);
  assert.equal(product.listingGate.ok, false);
  assert.ok(Array.from(product.listingGate.missing).includes("skuStockComplete"));
});

test("reports login-required rawData as a typed error", () => {
  assert.throws(
    () => normalizer.normalizeRawData({ store: { initDataObj: { needLogin: true } } }, "https://mobile.yangkeduo.com/"),
    (error) => error.code === "login_required"
  );
});
