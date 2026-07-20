# M9 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / Python 3.12 / 当前无 ADB 设备
- 状态：本机协议通过，真机、麦克风和公网搜索待外部验收

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | DeviceBackend 契约 | Local 与 Android 实现的截图、点击、输入、启动/停止契约通过 | 通过 |
| 2 | SearchProvider | Brave/DDG 超时、解析、错误映射与降级用例通过 | 通过 |
| 3 | 增量 ASR/VAD | 分块、partial/final、VAD 和不可用错误专项通过 | 通过 |
| 4 | 合成语音输入 | Edge TTS 生成 5.81 秒中文 MP3/VTT；本地 faster-whisper-base CPU int8 成功识别，不需要开启麦克风或播放声音 | 通过 |
| 5 | Android APK | `2026.06.25.7` 候选包由最新源码重新构建，compile/target SDK 35，APK v1/v2/v3 签名通过；普通操作按钮触控高度由 46dp 修正为规范要求的 48dp。当前发布闸门仍批准 `.4`，`.7` 待真机复核和人工批准 | 本机构建通过，待发布批准 |
| 6 | 真机/真实输入 | 当前 ADB 设备列表为空；Brave 未配置 key，DDG DNS 在当前网络不可达；真实麦克风体验未执行 | 待外部验收 |

- 证据：`backend/tests/unit/test_android_bridge.py`、`test_search_provider.py`、`test_streaming_listener.py`、`tmp/synthetic-audio-20260718/`、`mobile-link-bridge/out/release-manifest.json`。
- 结论：协议实现通过；真机、真实搜索和语音输入待验收。
