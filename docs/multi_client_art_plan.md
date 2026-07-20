# 多端美术适配方案 v4（01 Atelier Editorial）——交 OPUS 实现

> **状态：已完成并归档（2026-07-16）。** 旧 HTML/CSS/PNG 概念稿已清理；当前 WPF/Web/Avatar 源码为唯一视觉事实来源，后续仅按具体问题微调。

> 状态：2026-07-11，v4 施工定稿。v4 修正了 v3 的权威源冲突、对比度、主题优先级、可访问性例外、旧版迁移和四端验收缺口。
> 视觉方向：01 Atelier Editorial。夜间是暖炭编辑室，日间是冷日光工作场，铜色是跨主题身份点睛。
> 当前不是绿地实施：2026-07-10 已完成过蓝金 v2 的 A3/A4/A5。本方案必须按“v2 -> v4 迁移”执行，不得把现有主题文件当空白重建。

> 施工进度（2026-07-11 07:05）：A0 迁移清单、A1 token/schema/自动校验、A2 字体/语义图标/六类组件状态契约、
> A3 WPF、A4 Web、A5 iOS/Android 代码迁移已完成。统一验证通过：pytest 187、ruff pass、WPF build 0/0、
> dotnet test 134；Android APK 构建通过；设计校验覆盖 26 个对比度组合、四端核心色值漂移、字体哈希、
> 29 个本地 Lucide 资产与组件契约。Web A6 已完成 390/768/1280/1440 四档 Light/Dark/System 截图，
> 均无横向溢出。尚未完成且不得推断通过：WPF 100/150/200% DPI 全矩阵、macOS/Xcode iOS、Android
> 真机字体 1.0/1.3/2.0 与 TalkBack，以及 1000 项列表压力验收。
> 实现方：OPUS。方案与验收兜底：本文件。

## 0. 施工边界与事实源

## 0. 施工边界与事实源

### 0.1 唯一事实源与对齐要求

1. **视觉基准（唯一权威）**：`C:/Users/Administrator/Documents/SpiritKinAI/ui-concepts/desktop-priority.html?i=0`（.skin-atelier 昼夜双态）及导出图 `exports-desktop-priority/01-atelier-editorial.png`。概念稿的 CSS（`desktop-priority.css` 的 `.skin-atelier` / `.skin-atelier.day` 段，:2629-2671）是 OKLCH 色值权威源。**任何拿不准的视觉决策，回概念稿找答案而不是发挥**。
2. **对齐要求**：桌面端必须与概念稿观感一致——色温、层次表达（四级 surface + 描边不靠投影）、信息密度（安静仪表带）、字体分工、聊天区扁平卡片风格。§1.2 已知局限里点名的例外才允许偏离。
3. `design/tokens.json` 是颜色、字体角色、组件状态和主题映射的唯一机器可读权威源。
4. `design/tokens.schema.json` 是 token 结构约束；A1 必须补齐，schema 校验失败不得继续下游批次。
5. WPF XAML、Web CSS、Swift 和 Android resources 都是 `tokens.json` 的生成或校验产物，不得各自发明色值。
6. A0 将概念稿 HTML/CSS/PNG 复制到主仓库 `design/reference/atelier-editorial/`，去除对 C 盘绝对路径的依赖。
7. `.claude/skills/spiritkin-art-style/SKILL.md` 只做人工速查，必须由 A1 与 `tokens.json` 同步；发生差异时以通过 schema 和对比度检查的 `tokens.json` 为准。

### 0.2 布局保护规则

保护的是信息架构、业务流程和核心区域关系，不是每一个像素：

- WPF 保留 Sidebar / ChatWorkspace / ManagementPanels 三大区域及现有功能入口。
- 不重写业务逻辑，不删除已有控件，不改变工作流、协作、确认门和运行时事件语义。
- 默认只改颜色、画笔、字体角色、图标、圆角、描边、焦点和状态表现。
- 以下属于允许且必须修复的最小布局例外：200% 缩放或系统字体放大后的重排、长文本溢出、键盘焦点、读屏语义、iOS 44pt 和 Android 48dp 触控目标、安全区/系统栏、软键盘遮挡、窄屏折叠。
- 新增主题入口属于批准的结构例外；优先复用当前应用菜单中的主题入口，不重复创建两套控制。设置页镜像入口只有在不制造重复状态源时才允许增加。

## 1. 设计系统

### 1.1 双色温模型

- **Night / 暖炉编辑室**：暖炭中性底，铜色只承担品牌、当前选择和运行脉络。大面积表面保持低彩度，避免整屏棕橙化。
- **Light / 冷日光工作场**：冷亮底、冷蓝结构、暖铜身份点睛。蓝色承担链接、焦点和结构反馈，不与铜争夺主操作层级。
- 密集工作界面以可读性优先；纹理、扫描线、辉光和光束不进入基础组件。只有 3D 舞台或明确的品牌展示位可以克制使用。

### 1.2 已知局限

1. **WPF TextBox 无原生 LineHeight**：聊天气泡用的是可选中/复制的 `TextBox`，WPF TextBox 不支持 LineHeight 属性。换成 RichTextBox/FlowDocument 会牵动现有选中/复制/滚动逻辑，超出纯视觉范围。**处置**：聊天气泡行高保持默认（~1.3），不强求与 Web 概念稿的 1.78 对齐；其他文本位（标题/卡片描述/按钮）能设 LineHeight 的正常跟 token。
2. **右侧工作台降噪**：见 A3 第 6 点"安静仪表带"专项。

### 1.3 Token v4 基准值

以下 hex 已按主要前景/背景组合校正。A1 在 `tokens.json` 中同时记录 OKLCH 与 sRGB hex，并由脚本重新验证，脚本结果优先于手抄表。

| token | Night | Light | 使用约束 |
|---|---|---|---|
| canvas | `#15110C` | `#EEF5FA` | 应用画布 |
| surface | `#1F1914` | `#F5FBFF` | 主内容面 |
| surface-2 | `#2A221C` | `#E9F2F7` | 工具栏、次级面 |
| surface-3 | `#372E27` | `#DEEAF1` | hover/selected 容器 |
| line | `#4E463F` | `#C4D4DF` | 仅作非关键分隔线 |
| control-border | `#73665D` | `#70869A` | 输入框、按钮等可交互边界，需 >= 3:1 |
| text | `#F0EAE4` | `#273A4C` | 正文和关键标签 |
| muted | `#ACA39B` | `#4D667B` | 次级正文，需 >= 4.5:1 |
| faint | `#8B837B` | `#5F7689` | 仅非关键元数据；普通文本仍需 >= 4.5:1 |
| accent | `#E28E3A` | `#126DB6` | focus、链接、结构强调 |
| accent-2 | `#B18872` | `#0E6F9F` | 辅助图表/非正文强调 |
| on-accent | `#1F1914` | `#FFFFFF` | accent 实心底上的文字/图标 |
| copper | `#E99541` | `#B44A00` | 品牌、当前选择、运行脉络 |
| on-copper | `#1F1914` | `#FFFFFF` | copper 实心底上的文字/图标 |
| success-fg | `#5BCC80` | `#187A43` | 成功文字/图标 |
| success-bg | `#173523` | `#E7F5EC` | 成功容器 |
| warning-fg | `#EFBC4B` | `#875900` | 警告文字/图标 |
| warning-bg | `#3A2B10` | `#FFF3D6` | 警告容器 |
| danger-fg | `#FF6B68` | `#B52A2E` | 错误文字/图标 |
| danger-bg | `#3B1D1C` | `#FCEBEC` | 错误容器 |
| info-fg | `#75AFFF` | `#126DB6` | 信息文字/图标 |
| info-bg | `#172A43` | `#E8F2FB` | 信息容器 |
| focus-ring | `#E28E3A` | `#126DB6` | 键盘焦点，不能只靠颜色变化 |

规则：

1. `faint` 不再按字号豁免；只要作为文字使用，就必须对实际背景达到 4.5:1。大字豁免只适用于 >=18pt 常规字或 >=14pt 粗体字。
2. `line` 不能作为可交互控件的唯一边界；输入框、按钮和选择控件使用 `control-border` 或更强焦点提示。
3. 状态色必须使用 `*-fg` / `*-bg` 配对，不允许把 Night 的亮色直接复用到 Light。
4. 状态不能只靠颜色：warning/danger/success 同时使用图标、文字或形状差异。
5. copper 不是 warning；accent 不是品牌装饰。一个组件同一状态最多只有一个主强调色。

### 1.4 组件状态契约

每个交互组件至少覆盖以下状态，并在日/夜主题各验一次：

| 组件 | 必须状态 | 额外要求 |
|---|---|---|
| Button | default / hover / pressed / focus / disabled / loading | loading 防重复提交，disabled 不只降低透明度 |
| Input / ComboBox | default / hover / focus / invalid / disabled / read-only | 错误紧邻字段，保留用户输入 |
| List row / Nav item | default / hover / selected / keyboard-focus / unavailable | selected 与 focus 可同时识别 |
| Card / Panel | default / loading / empty / partial-error / offline | 禁止用无限嵌套卡片堆层次 |
| Status / Badge | info / success / warning / danger / unknown | 图标或文本辅助，不裸色表意 |
| Dialog / Sheet | default / destructive / busy / error | Esc/返回可用，焦点不逃逸，危险操作明确 |

### 1.5 主题解析契约

统一主题枚举：`system | light | dark`。

解析优先级：

```text
显式用户选择 > WebView 宿主传入 > OS 当前主题 > dark 兜底
```

- WPF：持久化枚举值；`system` 模式监听系统主题变化，不只在启动时读取一次注册表。
- Web：在首个主题 CSS 生效前设置最终 `data-theme`，避免闪白/闪黑。页面不得硬编码 `data-theme="dark"`。
- WebView2：宿主传入已解析的 `light|dark`，优先于页面的 `prefers-color-scheme`。
- iOS/Android：第一阶段跟随系统即可；若以后增加手动选择，复用同一枚举和优先级。
- 主题偏好默认按设备保存，不跨设备同步，除非后续产品需求明确要求。

### 1.6 字体与图标

- WPF/Web 品牌纯拉丁展示位可用 Orbitron；不得用于按钮、表格、设置项或长正文。
- 代码、终端、端口和数据使用 JetBrains Mono，提供 Consolas/Cascadia Mono 回退。
- Web 中文栈：`"Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif`。
- iOS UI 使用 SF Pro / Dynamic Type；Android UI 使用系统 sans / `sp`。移动端不强行移植 Orbitron 到普通控件。
- 禁止网络字体。Orbitron 与 JetBrains Mono 的许可证和子集文件随包落地；中文系统字体不打包。
- 图标共享语义 ID，不共享同一套原生绘制：WPF 用 Fluent/现有 Path，Web 优先 Lucide 单色图标，iOS 用 SF Symbols，Android 用 Material Symbols/平台图标。所有图标按钮有可访问名称和 tooltip/label。

### 1.7 动效

- 产品界面动效只表达状态，常规过渡 150-250ms，禁止弹跳、长序列入场和布局属性动画。
- 主题切换必须原子完成，不允许区域逐块变色或首帧闪烁。
- Web 遵守 `prefers-reduced-motion`；iOS 遵守 Reduce Motion；Android 遵守系统动画缩放。减少动效时改为即时切换或短交叉淡化。

## 2. 迁移批次

### A0：冻结 v2 与迁移准备

1. 记录当前 `git status`，不得覆盖用户未提交改动。
2. 保存 v2 WPF/Web 日夜截图和当前 token 清单，作为回滚与残留对照。
3. 将概念稿 HTML/CSS、Night PNG，以及新增的 Light PNG 复制到 `design/reference/atelier-editorial/`，去除主施工流程对 C 盘绝对路径的依赖。
4. 输出 v2 -> v4 文件清单：旧 token、New Rocker、裸 hex、固定 `data-theme`、StaticResource、Android `Color.rgb`。
5. 定义回滚点：每批独立提交或独立 patch；某端失败只回滚该端，不回滚已通过验收的其他端。

退出条件：参考资产在仓库内可读；迁移清单与实际扫描一致；没有修改业务逻辑。

### A1：Token、schema 与自动检查

1. 把现有 `design/tokens.json` 从 v2 升级到 v4，不新建第二份 token 文件。
2. 新建 `design/tokens.schema.json`，约束版本、主题、hex/oklch 双写、语义配对和平台映射。
3. 增加只读检查脚本：schema 校验、hex 格式、必需 token、前景/背景对比度、四端版本注释、裸色残留。
4. 生成或同步 Web/WPF/iOS/Android 映射；同步 `.claude/skills/spiritkin-art-style/SKILL.md`。

退出条件：所有规定文本组合 >=4.5:1；大字和非文本控件按规范 >=3:1；不存在 v2/v3 双权威。

### A2：资产与组件契约前置

1. [完成 2026-07-11] 本地打包 Orbitron、JetBrains Mono，删除 New Rocker 的生产引用；保留安全回退字体。
2. [完成 2026-07-11] 建立语义图标清单及四端映射，禁止手绘一批无法扩展的孤立图标。
3. [完成 2026-07-11] 先完成 Button、Input、Nav/List、Card/Panel、Status、Dialog 六类状态矩阵，再进入页面级改造。

退出条件：字体缺失时布局仍可用；图标语义一致；组件状态日夜完整。

### A3：WPF（主战场）

1. 保留 `Fantasy*` 键名；主题资源拆分/整理到 `Fantasy.Light.xaml` 和 `Fantasy.Dark.xaml`。
2. 需要运行时切换的颜色统一使用 `DynamicResource`，扫描残余 `StaticResource Fantasy*`。
3. 复用现有 `ThemeManager.cs` 和应用菜单主题入口，不重复新建管理器；补齐 system 实时监听、持久化和 WebView2 联动。
4. 收编健康横幅、品牌标记、ViewModel 状态画笔和工作流节点等残留裸色。
5. 打磨顺序：标题栏/侧栏 -> 聊天气泡与工作卡 -> 确认门/错误状态 -> 管理面板 -> 终端/日志 -> 3D 舞台。
6. **安静仪表带（右侧工作台降噪）**：
   - `WorkbenchShellView.xaml` 的 `WorkbenchStatusPanelElement`（Grid.Row="1"）改为顶部折叠式仪表条：
     - 默认只显示一行关键指标：`「changes 86 · 分支 codex/project-ui-governance · ✓ gh」`（changes 数用 copper 点睛，分支名截断尾部，gh 可用/不可用分别显示 ✓/×）
     - 点击展开才显示完整卡片内容（工作区路径、打开工作区/管理项目按钮、提交或推送按钮、终端区）
     - 折叠/展开状态持久化到既有桌面状态存储（`desktop_console/state.json`），默认**折叠**
   - 3D 模型区（`AvatarPanelElement` Grid.Row="3"）保持现有位置与交互，不动
   - 终端区（`IntegratedTerminalPanelView`）在展开态时才可见，折叠时隐藏
   - 观感目标：降噪到只显关键数字的安静仪表，适配概念稿右栏干净风格；功能不减少，只是默认收起

退出条件：日/夜/system 实时切换、重启记忆、键盘焦点、150%/200% DPI 和长文本均通过；控件无非必要位移。

### A4：Web

1. `frontend/styles/fantasy-tokens.css` 消费 v4 token；删除错误的 v1/v2 注释和旧 New Rocker 声明。
2. `console.css` 与目标页面去除裸色和固定 `data-theme="dark"`；显式主题属性必须覆盖系统媒体查询。
3. 处理 `desktop_console.html` 内联颜色；不为“零内联”改变业务 DOM，只有可访问性/响应式所需的最小结构调整例外。
4. 覆盖 index、desktop_console、spirit_avatar、avatar_3d、audit_report、replay_report；`live2d.html` 继续弃用，不投入。
5. 3D/HUD 与终端可保留专用深色媒体面，但其文字、边界和控件仍使用语义 token。

退出条件：无主题闪烁；系统/显式/宿主优先级正确；键盘、200% zoom、390/768/1280/1440 宽度无溢出。

### A5：iOS / Android

#### iOS

- `FantasyTheme` 对齐 v4，优先使用动态语义色或 Asset Catalog，而不是在视图内散落 hex。
- 使用 Dynamic Type、SF Symbols、safe area 和系统导航/返回手势；触控目标 >=44pt。
- Windows 环境不能宣称真机通过；缺少 Xcode 时标记为“代码完成，待 macOS/Xcode 验收”。

#### Android

- 颜色进入 `res/values/colors.xml` 与 `res/values-night/colors.xml`，Java 通过资源角色取色；不继续扩张 `initPalette()` 的手工 `Color.rgb` 双板。
- 保持当前 Android Views 构建链，不为换肤强行引入新框架；组件尺寸和语义遵循 Material 3，触控目标 >=48dp，文字使用 `sp`。
- 检查 edge-to-edge/insets、系统 Back、软键盘遮挡、TalkBack 标签和列表图片内存边界。

退出条件：主题、字体放大、触控目标、系统栏、返回行为和读屏标签通过；大列表不因全量位图导致明显卡顿或 OOM。

### A6：跨端验收与交付

1. 运行：
   `PYTHONIOENCODING=utf-8 python -X utf8 scripts/run_verification.py --note "美术 v4 A_x：..."`
2. 每批记录实际 pytest/dotnet 数量，不把旧数量写成永恒基线；要求相关测试不减少、失败数为 0、build 0 error。
3. 运行 token schema、对比度、裸色和版本漂移检查。
4. 保存以下截图矩阵：
   - WPF：Light/Dark/System，100%/150%/200% DPI，至少 1440x900 与 1920x1080。
   - Web：Light/Dark/System，390x844、768x1024、1280x720、1440x900。
   - iOS：小屏与大屏、Light/Dark、最大 Dynamic Type；无 Xcode 时明确待验。
   - Android：Light/Dark、360x800 与常用真机、字体 1.0/1.3/2.0、TalkBack。
5. 状态矩阵：正常、loading、empty、offline、401/403、500、validation error、长中文、长英文、emoji、1000 项列表。
6. 主题切换不得丢失输入、滚动位置、当前选择或运行状态；减少动效设置下不得出现装饰动画。

## 3. 禁改区

- 协作链路、发言队列、确认门、Workflow、Runtime、Context 的业务语义与数据契约。
- `ChatWorkspaceView.xaml` 头部列关系 `[*][Auto][Auto]`，除非自动化/人工证据证明它阻断可访问性；触发例外时必须单独说明并最小修改。
- 现有 `Fantasy*` 对外资源键名；允许新增语义键，不允许无迁移删除旧键。
- 用户已有未提交改动、测试台账历史和弃用决策。

## 4. OPUS 交付格式

每个批次完成后必须回报：

1. 改动文件与迁移项。
2. token/schema/对比度/裸色检查结果。
3. 自动测试和构建结果。
4. 已完成的截图/设备矩阵；无法执行的 iOS/真机项必须明确标记待验，不得推断通过。
5. 已知残留、回滚方式和下一批入口。

没有完成 A0/A1，不得直接开始全端换色；没有完成 A6，不得把“代码已改”描述为“四端美术已验收”。
