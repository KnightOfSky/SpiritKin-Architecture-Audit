# 多端美术概念稿对齐清单（交 OPUS 对照施工）

> **状态：已完成并归档（2026-07-16）。** 旧概念稿已清理，后续验收直接以当前产品界面、功能状态和回归截图为准。

> 本清单配合 `multi_client_art_plan.md` 使用。方案定义"什么是允许改的"，本清单定义"**必须改成什么样**"。
> 视觉基准：`C:/Users/Administrator/Documents/SpiritKinAI/ui-concepts/desktop-priority.html?i=0`（昼夜切换看右上角按钮）

## 铁律再强调

**布局一律不动** = Grid 列定义、控件顺序、功能入口位置、DOM 结构不变。
**表现层必须对齐** = 下列 10 条视觉要素必须改成跟概念稿一致，不是架构层、可以改、必须改。

---

## 必须对齐的 10 条视觉要素

### 1. 双色温（最核心）

**概念稿**（desktop-priority.css :2629-2671）：
- 夜（默认）= 暖炉编辑室：canvas `oklch(18% 0.012 66)` 暖炭底，copper `oklch(74% 0.14 62)` 琥珀铜点睛，纯暖
- 日 = 清醒冷日光：canvas `oklch(96.5% 0.01 238)` 冷亮底，accent `oklch(56% 0.15 250)` 冷蓝结构，copper `oklch(54% 0.17 56)` 暖铜身份点睛不变，冷暖对撞

**WPF 必须**：
- `Fantasy.Dark.xaml` 换成夜色板（canvas #15110C / surface #1F1914 / copper #E99541），去掉旧的蓝金 v2 残留
- `Fantasy.Light.xaml` 用日色板（canvas #EEF5FA / surface #F5FBFF / accent 冷蓝 #126DB6 / copper #B44A00）
- 不许发明新色值，只从 `design/tokens.json` v4 取

### 2. 层次表达（四级底色 + 描边，不靠投影）

**概念稿**：
- 卡片层次靠 surface < surface-2 < surface-3 四级底色台阶 + 1px `line` 色描边
- **投影只用于浮窗/菜单**，常规卡片（聊天气泡、工作卡、管理面板卡片）不用投影（夜里投影不可见）

**WPF 必须**：
- ChatWorkspaceView.xaml 聊天气泡：去掉 `Effect="{StaticResource SoftShadow}"`，改 `BorderBrush="{DynamicResource FantasyLineBrush}" BorderThickness="1"`
- WorkbenchShellView.xaml 管理面板卡片（:59 `Effect="{StaticResource SoftShadow}"`）：去投影，加 1px 描边
- 工作卡（CollaborationPanel 渲染的卡片）：同样去投影改描边
- 保留投影的只有：悬浮菜单（ContextMenu）、弹窗（Dialog）、Tooltip

### 3. 圆角统一（概念稿 6px 主卡片，4px 小组件）

**概念稿**：
- 主卡片（聊天气泡、工作卡、管理面板卡）= `border-radius: 6px`
- 小按钮/输入框 = `border-radius: 4px`
- 图标容器/chip = `border-radius: 4px`

**WPF 必须**：
- ChatWorkspaceView.xaml :201/217 卡片 `CornerRadius="6"` 已对（保持）
- 按钮样式（TinyButton / PrimaryButton）改 `CornerRadius="4"`（现在可能是 3 或 5）
- 输入框 TextBox 默认样式改 `CornerRadius="4"`

### 4. 字体分工（Orbitron 展示 / JetBrains Mono 数据 / 系统字体正文）

**概念稿**：
- 品牌展示位（LOGO、数字时间）= Orbitron（只拉丁，无中文字形）
- 代码/终端/端口/数据 = JetBrains Mono
- 正文/标题/按钮 = 系统字体栈（WPF 直接雅黑，Web Noto Sans SC → 雅黑）

**WPF 必须**：
- WindowBrandMark.xaml 品牌字改 `FontFamily="Assets/Fonts/#Orbitron"`（去掉 New Rocker）
- IntegratedTerminalPanelView 终端区字体改 `FontFamily="Assets/Fonts/#JetBrains Mono"`
- 工作流端口号、数据位改 JetBrains Mono
- 正文保持系统默认（Microsoft YaHei 或 Segoe UI）

### 5. 信息密度（右侧工作台降噪成安静仪表带）

**概念稿右栏观感**：
- 干净、只显关键数字、不密集堆卡片

**WPF 必须**（方案 A3 第 6 点）：
- WorkbenchShellView.xaml 默认折叠，顶部只显一行：`changes 86 · 分支 codex/... · ✓ gh`
- 点击展开才显示完整卡片（工作区路径、按钮、终端）
- 3D 模型区保持原位，不动

### 6. 聊天区观感（扁平卡片风格，不是圆润气泡）

**概念稿** `.msg-bubble`：
- 扁平矩形卡片，圆角 6px
- 底色 surface / surface-2 分级（user vs assistant）
- 1px 描边，不用投影
- 头像 + 正文垂直排列（不是横向）

**WPF 必须**：
- ChatWorkspaceView.xaml 消息气泡容器：保持现有垂直 StackPanel 布局（头像在上、正文在下）
- 去掉气泡投影，加 1px 描边
- user 消息用 surface-2 底色，assistant 消息用 surface 底色（或反过来，总之要分级）
- 圆角统一 6px

### 7. 铜色点睛（选中态、运行脉络、品牌，不是装饰）

**概念稿**：
- copper 只用于：当前选中（侧栏指示条）、运行中状态（工作卡 running）、品牌 LOGO
- **不用于**：普通按钮背景、大面积填充、装饰性图标

**WPF 必须**：
- WorkspaceSidebarView.xaml 选中项左侧加 3px 宽 copper 色竖条指示（概念稿左栏选中态有明显铜条）
- 工作卡 running 状态的边框或顶部进度条用 copper
- 品牌 LOGO（WindowBrandMark.xaml）用 copper
- 其他位置慎用铜色（不是主题色，是点睛）

### 8. 冷蓝 accent（日主题结构强调，不抢铜的身份位）

**概念稿日主题**：
- accent 冷蓝 `oklch(56% 0.15 250)` 只做结构：焦点态、链接、描边强化
- 不用于大面积填充、不抢铜的身份点睛位

**WPF 必须**：
- Fantasy.Light.xaml 的 FantasyAccentBrush 改冷蓝 #126DB6
- 焦点态（FocusVisualStyle）用 accent 冷蓝描边
- 链接文字（超链接）用 accent 冷蓝
- 按钮 hover 描边可用 accent 强化（但 primary 按钮底色仍用 copper）

### 9. 状态色配对（success/warning/danger 必须用 fg+bg 配对，不裸用亮色）

**概念稿与 tokens.json v4**：
- success: fg `#5BCC80` / bg `#173523`（夜）, fg `#187A43` / bg `#E7F5EC`（日）
- warning: fg `#EFBC4B` / bg `#3A2B10`（夜）, fg `#875900` / bg `#FFF3D6`（日）
- danger: fg `#FF6B68` / bg `#3B1D1C`（夜）, fg `#B52A2E` / bg `#FCEBEC`（日）

**WPF 必须**：
- 健康横幅（MainWindow.xaml 顶部 warning）：用 `Background="{DynamicResource FantasyWarningBgBrush}" Foreground="{DynamicResource FantasyWarningFgBrush}"`
- 错误提示：用 danger-fg + danger-bg 配对
- 成功提示：用 success-fg + success-bg 配对
- 不许直接用夜间亮色（#5BCC80）当日间文字色

### 10. 间距与呼吸感（概念稿卡片间距 14px，内边距 14-16px）

**概念稿** `.card`：
- 卡片间距（相邻卡片 margin-bottom）= 14px
- 卡片内边距（padding）= 14px 或 16px
- 标题与正文间距 = 8px

**WPF 必须**：
- WorkbenchShellView.xaml 管理面板卡片 `Margin="0,0,0,14"`（现在是 `:59 Margin="0,0,0,14"` 已对）
- 卡片内 `Padding="14"` 或 `Padding="16,14"`（现在 `:59 Padding="14,13"` 接近，微调到 14）
- 标题与描述间距 `Margin="0,0,0,8"`（:61 已对）

---

## 验收方式

1. **并排对照**：启动桌面后，浏览器打开概念稿昼夜两态，目测对照上述 10 条
2. **取色器抽查**：用 PowerToys ColorPicker 或 Snipaste 取 WPF 渲染后的 canvas/surface/copper/accent，与 tokens.json v4 hex 值比对（±2 色阶内算通过，WPF 渲染有亚像素误差）
3. **切换测试**：日 → 夜 → 跟随系统，10 条要素都正确变色且控件零位移
4. **DPI 测试**：100% / 150% / 200%，层次、间距、圆角视觉一致

不通过标准：
- 夜间还是旧蓝金 v2 色板 → 色温未对齐
- 卡片还有大投影 → 层次表达未对齐
- 右侧工作台密集堆卡片 → 信息密度未降噪
- 终端/数据位不是等宽字体 → 字体分工未落地
- 侧栏选中项没有铜色指示条 → 铜色点睛缺失
