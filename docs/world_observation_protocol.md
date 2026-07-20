# World / Observation 协议

版本：1.0（2026-07-19）
实现：`backend/world/`、`backend/mobile/ios_world.py`

World 与 ARKit 解耦。ARKit、Android Camera、Browser DOM、Desktop Capture、OCR 和远程传感器统一产出 `spiritkin.observation.v1`；`WorldStateStore` 只合并结构化观察。

## 三层数据

| 层 | 内容 | 生命周期 |
|---|---|---|
| Raw | RGB、Depth Map、Point Cloud、Mesh、视频 | 设备侧瞬时处理，当前 API 不接收、不落库 |
| Observation | tracking、pose、plane、object、relation、深度摘要、定位精度 | 默认 7 天，最大 30 天、有界 JSONL |
| World | 实体、空间关系、位置、confidence、provider、fresh/stale | 长期持久化 |

Observation API 拒绝 `capturedImage`、image/base64/bytes、depth map、point cloud、mesh data、video、本地路径、未知字段和 credential key。Workspace、Host 和 Provider ID 由已配对 iOS 控制面覆盖。

## iOS Provider

原生实现使用 `ARView + ARSession + ARWorldTrackingConfiguration`：

- horizontal/vertical plane detection；
- LiDAR 设备启用 mesh with classification；
- 支持时启用 sceneDepth/smoothedSceneDepth，但只发布 `available` 摘要；
- camera pose 转为 position + normalized quaternion；
- Plane center 转换为世界坐标；
- Location 只在 When-In-Use 授权后获取，服务端将经纬度量化到 4 位小数；
- 发布上限 0.5 Hz（每 2 秒一次）；离开页面立即 pause ARSession 与定位。

当前 Provider 没有把 Vision 物体识别伪装为 ARKit 能力，因此 `objects` 默认为空；以后可在设备侧加入受测量的 Vision Provider，再通过同一 Observation 合并。

## 接口

- `POST /ios/observations`：发布一个结构化 Observation；
- `GET /ios/world`：读取当前 workspace 的 World State 与最近 Observation；
- `GET /desktop/runtime-continuity`：桌面读取同一 World 摘要。

Windows 能验证协议、存储、API 和 Swift 静态契约；ARKit、RealityKit、LiDAR、相机/定位权限、耗电与真机网络必须在 Xcode+iPhone 上完成外部验收。
