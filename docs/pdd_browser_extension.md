# PDD 浏览器抓取扩展

## 定位

手机端负责从 PDD 复制并回传网页商品链接；浏览器扩展使用已经登录的
Chrome/Edge Profile 打开链接、读取 `window.rawData`，再把规范化
`productData` 回传 SpiritKin 控制面。

主链如下：

```text
Android link share
  -> SpiritKin mobile_links
  -> browser_extension claims web link
  -> logged-in PDD tab
  -> rawData + DOM image enrichment
  -> spiritkin.pdd_product_data.v1
  -> JSON Artifact + completed mobile link
```

手机桥接只接收 `yangkeduo.com` 或 `pinduoduo.com` 商品网页链接。微信小程序
短链与旧 OCR adapter 已删除，不再作为备用路径。

## 代码

- 扩展：`browser-extension/pdd-product-extractor`
- 控制面 HTTP：`scripts/mobile_link_receiver.py`
- 链接领取与结果状态：`scripts/control_plane_store.py`

扩展迁移自 `E:\AutoProcessAP\pdd-batch-extractor`，但运行时不依赖
AutoProcess。保留了 rawData、SKU、主图和详情图的字段语义；旧 Vue 注入、
旧 API 端口、占位库存和旧 popup 已删除。

## 配对与权限

扩展使用 `browser_extension` 角色的配对 Token。它不能使用或保存
`SPIRITKIN_MANAGEMENT_TOKEN`。

在认证后的控制台中，进入工作区的“配对与安装”，点击“生成抓取扩展配对码”。
扩展设置页会请求用户配置的控制面 origin 权限，然后调用：

- `POST /extension/pair`
- `GET /extension/status`
- `POST /extension/links/claim`
- `POST /extension/results`
- `POST /extension/links/requeue`

扩展配对后默认自动轮询手机回传队列。领取操作会把链接原子更新为
`processing`。成功结果保存为 `pdd_product_data` JSON Artifact，同时绑定到
对应电商任务并更新 `listingGate`，链接更新为 `completed`；失败更新为
`failed`，可从扩展结果页重新排队。

## 数据门禁

完整上架门禁要求：

- 来源为 rawData；
- 数字 `goodsId`；
- 标题；
- 至少 2 张主图、1 张详情图；
- SKU 存在；
- 每个 SKU 都有真实价格和库存。

DOM 降级结果可以回传和审阅，但 `listingGate.ok` 必须为 `false`。
缺失库存保持 `null`，不得使用 `100`、`999` 等占位值。

## 安装与验证

在 Edge/Chrome 扩展管理页打开开发者模式，加载：

```text
D:\SpiritKinAI\browser-extension\pdd-product-extractor
```

自动验证：

```powershell
python -m pytest -q tests\test_pdd_browser_extension.py
node --test browser-extension\pdd-product-extractor\tests\normalizer.test.mjs
```

静态 UI 预览：

```text
http://127.0.0.1:8766/browser-extension/pdd-product-extractor/sidepanel.html
```
