# PDD RPA 环境准备与故障排查

PDD 链路使用“手机分享网页链接 -> 登录态浏览器扩展抓取 -> productData 门禁 -> 电商工作流 -> Android/Worker 执行”的受审计路径。项目不保存 PDD Cookie，不绕过验证码、风控或平台规则，不提供批量账号控制。

## 环境准备

1. 启动控制面和移动链接接收端，完成 Android、`browser_extension`、Remote Worker 三种角色的独立配对。
2. 在 Edge/Chrome 开发者模式加载 `browser-extension/pdd-product-extractor`。
3. 在本机浏览器人工登录 PDD，登录 Profile 只留在本机；扩展只使用 `browser_extension` Token。
4. 确认手机回传的是 `yangkeduo.com`/`pinduoduo.com` 商品网页链接，不使用小程序短链或 OCR 猜测值。
5. 确认工作流 `ecommerce.auto_listing.v1` 可见，Remote Worker 广播 `ecommerce.auto_listing` capability，Android Bridge 已授予必要的 Accessibility/MediaProjection 权限。

## 数据与发布门禁

扩展优先读取页面 `rawData` 并补充 DOM 图片，生成 `spiritkin.pdd_product_data.v1`。进入草稿阶段前必须满足：数字 goodsId、标题、至少两张主图和一张详情图、SKU 列表、每个 SKU 的真实价格和库存。DOM 降级数据可进入人工复核，但 `listingGate.ok` 必须保持 `false`。

`ecommerce.auto_listing.v1` 的顺序为选品、采集、手机链接入队、productData 绑定、完整性审核、生成草稿、发布前人工审核、发布或保留草稿。生产模式没有审核通过时不能提交。

图片裁剪/缩放/水印通过已注册的 `ffmpeg.transcode` Worker 执行，输入、输出和水印文件都必须位于工作区。例如：

```json
{
  "input_path": "state/ecommerce/assets/main.jpg",
  "output_path": "state/ecommerce/processed/main.png",
  "overwrite": true,
  "args": ["-i", "state/ecommerce/assets/watermark.png", "-filter_complex", "[0:v]scale=800:-2,crop=800:800[base];[base][1:v]overlay=W-w-24:H-h-24", "-frames:v", "1"]
}
```

执行器使用参数数组且 `shell=False`，并拒绝工作区外路径。代理只通过 Worker 本机 `proxy_url` 配置注入子进程，不上传代理地址、浏览器 Profile、Cookie 或店铺凭据；该接口用于合规网络配置，不用于绕过平台限制。

## 分批验收顺序

1. 扩展自动测试与 normalizer 测试通过。
2. 手机分享一条网页商品链接，扩展成功领取且控制面状态从 `processing` 到 `completed`。
3. 检查 JSON Artifact 与电商任务绑定，`listingGate` 对缺字段严格失败。
4. preview 模式生成上架草稿，全程不提交。
5. 人工审核标题、SKU、价格、库存、图片和平台规则。
6. 在专用测试账号/测试商品上开启 production，录制草稿创建全过程；发布仍保留人工确认。

## 故障排查

- 扩展无链接：检查角色是否为 `browser_extension`、origin 权限、手机链接域名和队列 workspace。
- productData 有空库存：保持 `null` 并打回，不得填 `100/999` 占位。
- listingGate 一直失败：先看 Artifact 的 `missing` 和 `checks`，再核对页面 rawData 是否因登录失效而缺字段。
- Android 命令离线：检查配对、心跳、Accessibility/MediaProjection 和目标 device_id。
- Worker 只返回 planned commands：生产模式未启用或审核未通过，这是预期的 fail-closed 行为。
- FFmpeg 失败：检查可执行文件、工作区路径、滤镜参数和输出目录；stderr 会进入自愈轨迹但不会绕过审核自动发布。

自动回归入口：`tests/test_pdd_browser_extension.py`、`browser-extension/pdd-product-extractor/tests/normalizer.test.mjs`、`backend/tests/unit/test_ecommerce_task_queue_tools.py`、`backend/tests/unit/test_workflow_graph.py`、`tests/test_control_plane_worker.py`。
