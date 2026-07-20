# SpiritKinAI 开发宪法

版本：1.1（2026-07-19）
适用范围：桌面端 WPF、Web 控制台、iOS 主控、Android Bridge、Remote Worker、后端 Runtime 与管理文档。

这不是愿景文案，而是提交、评审和验收时必须遵守的工程约束。与本文件冲突的临时实现不得以“先做出来”为理由进入主链路；确需例外时，必须在变更说明中写明范围、回滚方式和补验收项。

## 一、事实源与变更边界

1. 入口文档是 `project_management_overview.md`、`ai_collaboration_context.md`、`landing_and_test_handoff.md` 和 `project_dictionary.md`；架构事实以 `current_architecture_snapshot.md` 为准，稳定内核边界以 `ai_runtime_kernel_spec.md` 为准。
2. 桌面控制面、Command Gateway、Runtime、事件桥和 iOS 主控必须共享结构化 API/DTO、状态枚举和错误语义。前端不得用“看起来成功”的静态数据替代真实接口；接口不可用时必须显示离线/可重试状态。
3. iOS 是移动主控：负责会话、状态、权限确认、任务调度和结果呈现；模型推理、工作流编排、桌面服务、CosyVoice、Android 和 Remote Worker 执行仍由受治理的本地 Runtime/桌面控制面完成。iOS 可以本地执行系统 Shortcuts/App Intents，但不得绕过控制面直接执行高风险桌面或设备动作。
4. 新能力必须先落到已有模块与契约，再接入 UI。不要为同一状态建立第二个事实源；不要因为移动端需要而复制一套“伪桌面后端”。
5. WPF 的大范围 Shell/UI 重构需要明确委派；默认做窄范围修复、契约、运行时接入和验证，保护现有 `MainWindow` 拆分边界、控件名称、业务事件和工作流语义。

## 二、执行安全与可追溯性

1. 所有执行遵循：`Agent/Skill/外部助手 -> ToolRegistry -> 权限/审查/确认门 -> Executor/Worker -> 设备/连接器`。模型、外部 CLI 和远程节点只能提议或执行被允许的能力，不能绕过确认门。
2. 高风险动作（外发、提交、删除、设备控制、凭据使用、远程执行、模型/Skill/训练包升权）必须可见、可拒绝、可审计；自动修复只处理明确的低风险、可逆、幂等问题，其他项转人工确认。
3. Resource Registry 只存资源元数据和 credential reference，不存明文密钥。workspace、session、worker、device 和 resource 的作用域必须显式传递并校验。
4. “自愈”“同步”“GitHub CLI”“Remote Worker”“快捷指令”等名称只有在真实调用链、权限、错误状态和验证证据存在时才可以出现在用户可见 UI；未部署能力必须显示 planned/offline，而不是绿色可用。
5. 每一次状态改变都应能追溯到 API 请求、运行事件、审计记录或持久化快照。日志可以压缩或归档，但不能以删除审计记录来掩盖失败。

## 三点五、Growth Runtime

1. Growth Runtime 是 Evolution/Governance 下的“研发部门”，不是绕过治理的新执行器。它统一承载 Capability Gap Analysis、Workflow Mining、Skill Growth、Tool Growth、Code Builder 和 Model Builder 的候选产物。
2. 成长链固定为：`Need -> Capability Gap -> Skill/Tool/Code/Model -> Sandbox/Dry Run/Benchmark -> Review -> Registry`。候选可以自动生成，注册和激活不能自动发生。
3. Tool Builder 可以研究、发现和准备安装计划，但不得在生产 workspace 自动下载、安装、执行外部代码；Code Builder 只能产出沙箱候选和验证报告；Model Builder 只能产出模型/评测候选。
4. 每个候选必须记录稳定 `candidate_id`、kind、来源请求、缺口、workspace、阶段、风险、证据、审核者、审核理由和 activation 状态。Registry 记录“已注册候选”，不等同于已经启用能力。
5. Runtime 轨迹观察器可以按重复失败阈值生成 Skill 候选，也可以按重复 workflow/run/node 轨迹生成 Workflow 候选；观察器不得直接修改 Skill/Workflow Registry。
6. 候选阶段必须通过顺序化 `advance_stage` 提交 evidence；`review` 需要审核者，`registry` 只能由已批准候选进入。
7. Growth Runtime 不创造新的 Context、Worker、Skill Router 或 Manager 概念；它读写已有 Context/Trajectory/Capability/Skill/Workflow/Worker/Governance 契约。
8. Planner 发现执行能力缺失时，必须进入 Capability Gap Analysis 并创建候选；不能只返回“没有这个能力”。重复请求按稳定 candidate/gap identity 去重。
9. 桌面、iOS 原生和 iOS/PWA 读取同一 Growth candidate registry；客户端展示候选类型、状态、当前阶段和 workspace，但不得绕过桌面治理直接激活。
10. Growth 公共 API 不接受客户端传入的 event/registry 文件路径。阶段证据、审核和登记必须使用受管状态目录；审核/登记要求显式确认、非空证据、服务端确认的操作者身份和 workspace 匹配。普通 iOS workspace 不能治理全局候选或其他 workspace 候选。
11. 桌面与 iOS 可以通过同一治理 API 提交阶段证据、驳回、批准和登记；按钮只能按候选当前阶段开放。登记后的 `activation.enabled` 仍必须为 `false`，能力启用属于另一条独立、可确认、可回滚的治理动作。
12. Builder 准备动作只生成结构化 Builder Artifact：允许读取本地 Tool/MCP/Model/Worker 清单和操作者声明的来源，不得动态发现远程 MCP、联网下载、运行外部代码、安装依赖、推进候选阶段或启用能力。Artifact 路径由服务端受管，客户端只接收有界摘要；完整清单留在受管状态目录等待后续 Sandbox/Review。
13. Builder 预检是独立的、显式确认的治理动作：只允许在候选对应的 `design|sandbox|dry_run|benchmark` 阶段运行受管静态沙箱预检，生成带完整性、workspace、写入范围、联网/执行/安装/激活护栏和本地 Registry 匹配结果的报告。预检不推进阶段、不批准、不登记、不激活；真实外部安装、代码执行和远程研究必须另有隔离沙箱、权限和人工验收证据。
14. Builder 之间的升级必须使用服务端声明的单向升级图，并记录 `root_candidate_id`、`parent_candidate_id`、深度、理由、证据和操作者。技术升级创建子候选并冻结父候选；无法继续时显式转为 `needs_human`。客户端不得自行推断反向路径，也不得把“转人工”显示成完成。
15. 远程研究必须使用服务端固定、允许清单内的公开元数据端点和有界单次请求；客户端不得覆盖 URL、注入认证信息或触发限流重试。搜索词必须通过独立字段显式提交，不得复用审核证据，并在联网前拒绝明显凭据内容。研究结果只能成为 Candidate evidence，不得自动克隆、下载、安装、执行、推进阶段、登记或激活。
16. 真实候选代码只能在显式启用的容器 Sandbox 中执行：镜像必须是本地已存在且经过配置批准的 immutable digest，禁止自动拉取、host bind mount、网络访问和主机执行。Bundle 必须有界、可校验、无明文凭据；容器必须只读、非 root、drop capabilities、no-new-privileges、资源限额并在超时/失败时清理。执行报告不等于阶段通过，不能自动推进、登记或激活。

## 第十九章：Benchmark Constitution（评测宪法）

1. SpiritKin 中任何 Model、Agent、Workflow、Skill、Worker、Tool、Code、Vision、Training、Prompt 或 Runtime 的新增、替换和升级，都必须使用统一 Benchmark；禁止凭主观体验、厂商宣传或单次演示替换生产组件。原则是 `Everything is Measured`。
2. 所有评测必须输出统一、可审计的契约：`benchmark_id`、`target`、`target_type`、`version`、`baseline_version`、`dataset`、`success_rate`、`latency_ms`、`cost`、`retry_count`、`review_count`、`quality_score`、`overall_score`、Before/After、delta、measurement source、workspace、操作者和 Promotion Gate。
3. `overall_score` 和 Promotion Gate 必须由服务端统一计算，客户端不得提交或覆盖最终结论。缺字段、非法数值、来源不明、低于最低成功率/质量分、成功率或质量回退、总分未严格提升时一律 fail closed。
4. Growth 候选进入 Review 前必须存在同一 candidate 的最新 `promotion_gate.passed=true` 评测。Skill、Tool、Code 还必须关联已通过的隔离执行报告；Workflow 必须关联 Dry Run；评测报告本身不得推进阶段、批准、登记或激活能力。
5. Growth Benchmark 必须保留 Before/After 同数据集对比；没有证明提升的候选不能 Promotion。更低延迟和成本可作为证据，但不能抵消成功率、质量或安全性回退。
6. Model Benchmark 必须经过 Model Jury。至少两个不同、具名评审来源基于同一评测报告给出可审计结论后，模型候选才可通过评测门；Jury 分析不能替代数值评测，也不能直接切换生产模型。
7. Benchmark 数据集、版本和测量来源必须稳定标识。更换数据集、提示、环境、硬件或评分规则时必须形成新评测版本，禁止把不可比结果伪装成提升。
8. 桌面端、iOS 原生和 iOS/PWA 必须读取同一 Benchmark 快照与门控状态。移动端只能提交显式确认的结构化测量，操作者身份由已配对控制面覆盖，不能自行伪造通过状态。

## 第二十章：Runtime Host Constitution（运行时主机宪法）

1. Workflow、Queue、Worker、Checkpoint 和 World 属于共享 Runtime，不属于 Desktop、iOS 或任一 Remote Worker。Desktop、iOS、Cloud、Remote、Edge 只是 Runtime Host 或 Adapter；关闭桌面客户端不得被定义为取消长期 Workflow。
2. 每个 workspace 同时只能有一个有效 Workflow 执行租约。Host Election 必须持久化单调递增 epoch 和不可公开的 fencing secret；旧 Host、过期 lease 或错误 workspace 的写入必须 fail closed，禁止双主。
3. Host 注册必须声明 `host_id`、workspace、host type、能力、是否可执行 Workflow、是否可观察、优先级、心跳 TTL 和状态。公开快照不得包含 endpoint credential、lease secret、令牌或内部状态路径。
4. Workflow Checkpoint 必须保留同一 `run_id` 的完整节点状态、队列、Pending Skill/Worker、Context reference、源 Host、源 epoch、Definition digest、sequence 和完整性校验。迁移是 Resume，不是 Restart；已完成节点不得重跑。
5. Checkpoint 之后若 Workflow Definition 或受管 Run 已发生更新，旧 Checkpoint 不得覆盖新状态。正在执行的节点恢复时必须进入 `reconcile_inflight` 审核，确认是否已经产生外部副作用后才能继续，禁止自动重放提交、发布、扣费、删除或设备动作。
6. Host 心跳只在 Run 的 `updated_at` 变化时生成新 Checkpoint，避免无界状态增长；Workspace Host 只能为本 workspace 的 Run 建检查点。迁移请求必须显式确认并绑定已存在的 Checkpoint 与在线目标 Host。
7. iOS Controller 可以登记控制/观察 Adapter、查看 workspace Host 和请求 Election/Migration，但默认不能获取执行租约 secret、claim 迁移或直接 Resume。只有目标 Runtime Host 取得新 lease 后才可恢复。
8. Remote Worker 属于 Runtime Bus，不属于 Desktop。Worker 的任务 lease 和 Runtime Host 的执行 lease 是两层独立契约：前者约束任务归属，后者约束 Workflow 主控；二者都必须有 workspace、TTL、幂等和审计。

## 第二十一章：World / Observation Constitution（世界与观察宪法）

1. ARKit、Android Camera、Browser DOM、Desktop Capture、Remote Camera、OCR、USB Camera 和 Robot Camera 都只是 Observation Provider。World 核心只接收版本化 Observation，不得导入 ARKit/RealityKit 等平台框架。
2. 数据固定分三级：Raw（RGB、Depth Map、Point Cloud、Mesh、视频）只允许设备侧瞬时处理且默认不上传；Observation 保存有界结构化事件；World State 长期保存实体、关系、位置、置信度、新鲜度和 Provider 状态。LLM 默认读取 World，不读取原始画面。
3. Observation 公共 API 必须拒绝原始图像、Base64、二进制、深度图、点云、本地路径、未知字段和凭据字段。客户端不能覆盖已认证 workspace、Host 或 Provider identity。
4. 统一 Observation 至少包含 `observation_id`、workspace、host、provider、provider type、session/sequence、observed_at、tracking、camera pose、objects、planes、relations、confidence 和 retention policy；所有数组、文本、坐标和数值必须有边界。
5. World Entity 应优先使用 Provider 稳定 entity/anchor identity；缺少稳定 ID 时只能使用明确、可审计的空间近似键。World 更新是确定性合并，不由 LLM 直接改写；过期实体标为 stale，而不是伪装为当前事实。
6. iOS Observation Provider 使用 RealityKit + ARKit；可以在设备侧读取 Camera、IMU fusion、Plane、Scene Reconstruction、Depth availability、Tracking 和经授权的位置，但向 Runtime 只发送结构化摘要。GPS 默认量化到 4 位小数，原始坐标精度、录像、RGB 帧、Depth Map 和 Point Cloud 不落库。
7. Observation 发布必须限频、前台可见、可停止并尊重相机/定位权限；页面离开或 App 生命周期结束时停止 ARSession 和定位更新。没有 LiDAR 或 Scene Depth 时明确降级，不能把 RGB Tracking 显示成 LiDAR 可用。
8. Desktop、iOS 原生和 iOS/PWA 必须读取同一 World State 和 Runtime Host 快照。World 与 Workflow 通过稳定 reference 连接，不得让每个客户端维护私有、互相覆盖的“世界模型”。

## 三、性能与交互预算

1. 主线程/主 actor 不得执行网络、模型、文件扫描、JSON 大对象解析、图片解码或进程启动。所有请求必须异步、可取消、带超时，并有明确的 queued/running/succeeded/failed/timeout 状态。
2. iOS/PWA 首屏和标签切换优先保证可用：切换只更新目标面板，不触发全站重排、重复网络请求或隐藏 3D 渲染；隐藏的 Avatar、轮询和事件订阅必须暂停，页面不可见时停止非必要同步。
3. 同一资源的并发刷新必须 single-flight/coalesced；重复点击不得产生并行请求。周期同步只在前台和相关面板活跃时运行，失败采用退避和可见重试，不得用高频定时器制造卡顿。
4. WPF 后台监控可以持续运行，但不可在隐藏面板上反复重建列表、替换画笔或触发大范围布局；监控请求与 UI 渲染解耦，只有可见面板才刷新文字和集合。
5. 列表必须有边界：优先增量更新/虚拟化/分页，图片缩略图有尺寸上限，禁止一次性解码全量位图。动效表达状态即可，常规过渡 150–250ms，并尊重 Reduce Motion。
6. 运行时日志必须有边界：高频无效探针只记录聚合计数或采样，禁止逐请求写完整 traceback；日志清理不得删除状态、凭据引用或模型资产。
7. 主题解析统一为 `system|light|dark`，优先级为显式选择 > 宿主传入 > OS > dark 兜底；切换原子完成，不能闪白、闪黑或逐块变色。Web、WPF、iOS、Android 必须消费同一套语义 token。
8. 移动控件触控目标至少 44pt（Android 48dp）；动态字体、safe area、返回手势、键盘遮挡和读屏标签属于功能验收，不是美化可选项。
9. 多进程共享状态必须先获得外部文件锁再读取受锁字节；锁争用采用有界等待或结构化失败，不能把 `PermissionError`、半写 JSON 或空 HTTP 响应暴露给客户端。并发刷新回归必须同时覆盖桌面与移动控制面。
10. 远程 Embedding、Reranker、模型辅助和搜索 Provider 必须使用短时有界超时；连接成功但响应停滞也视为不可用。失败后进入有界熔断并回退本地确定性实现，禁止每个会话或每次标签切换重复等待同一故障 Provider。

## 四、跨端能力与数据池

1. 桌面端是能力完整度的参考面；iOS 主控跟随桌面端公开的会话、领域、Capability、Skill、Workflow、Resource、监控和任务契约，不通过复制字段或硬编码数量“模拟同步”。
2. 领域按业务归类管理；电商域统一承载 `/ios/terminal`、商品/素材/发布预检、Android 上架和 Remote Worker 任务。Skill/Workflow 池必须是真 CRUD，按 workspace/domain 隔离，内置项有明确不可删除或需确认的策略。
3. 会话必须使用稳定 `session_id`，创建、切换、归档、删除和消息同步要有幂等语义；本地缓存只能作为离线显示，不能覆盖桌面事实源。
4. 3D Avatar 是表达层，不得阻塞主控。它必须有可暂停/恢复的生命周期，切换到非聊天页时停止渲染；照片头像只保存在用户明确选择的设备范围内，并允许恢复默认。
5. Shortcuts/App Intents/Share Sheet/URL Scheme 是受限入口，复用同一权限和确认策略。不能把“能发 HTTP 请求”包装成“可以直接操控 iPhone”；系统能力、后台执行和真机网络配对必须分别标注验证等级。
6. Web/PWA 静态服务只能暴露显式公开资产根；`.env*`、`.git/`、`state/`、凭据、审计和源码目录不得通过静态服务器访问。兼容旧 URL 必须通过受限前缀映射实现，禁止把项目根目录作为通用 HTTP 文件根。
7. 主控 UI 中的在线、正常、已连接、已完成、Worker 数量和 Workflow 进度必须来自带 workspace 的事实源快照；加载前显示中性占位，失败显示结构化错误。禁止用硬编码演示数据或 Toast 冒充已创建、已配对、已修复或已下发。

## 五、验证与交付

1. 代码变更按风险选择验证：契约/后端至少单测和 HTTP smoke；前端必须有真实浏览器交互、Light/Dark/System 和移动视口截图；WPF 必须有可用的 build/test 或明确记录环境阻塞；原生 iOS 必须标注 Windows 无法替代 Xcode/真机验收。
2. 自动化全绿不等于用户体验通过。凡涉及卡顿、排序、双工、切换、模型响应、布局或触控的修复，必须补充手测路径和性能观察；测试台账记录“自动化通过、手测未过”时，结论只能是 PARTIAL。
3. 失败必须可诊断：错误消息包含阶段、请求/任务标识、是否可重试和下一步；不得吞异常后回退为成功数据。
4. 文档必须分类入库。被取代的文档移入 `docs/archive/`，不随意删除；运行日志、缓存、构建产物和截图属于可再生物，清理时不触碰源码、模型、凭据引用和审计状态。
5. 每次交付说明：改了什么、依据哪个契约、验证了什么、哪些仍待真机/生产环境验证。没有证据的能力不得宣称完成。

## 六、例外流程

例外必须同时包含：

- 具体违反的条款和原因；
- 影响面（端、workspace、用户、风险等级）；
- 临时开关、回滚路径和过期时间；
- 补充测试或真机验收计划。

例外未写入变更记录前，按本宪法的默认规则执行。

## 依据

- `docs/project_management_overview.md`
- `docs/current_architecture_snapshot.md`
- `docs/ai_collaboration_context.md`
- `docs/landing_and_test_handoff.md`
- `docs/manual_tests/2026-07-18_ios_terminal_avatar_acceptance.md`
- `docs/multi_client_art_plan.md`
- `docs/test-ledger.md`
