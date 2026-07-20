# 桌面美术美化方案（交 OPUS 实现，Claude 验收）

> **状态：历史方案已完成并归档（2026-07-16）。** 当前桌面实现是唯一事实来源；旧 UI 概念稿已清理，后续只按实际界面问题做小范围微调，不再按本方案发起整轮重做。

> 2026-07-06 由 Claude 起草。分工：OPUS 按本方案实现，Claude 验收（UI 改动必须肉眼确认 + 截图）。
> **铁律：本方案只允许改 XAML / 资源字典 / Style / 模板与纯展示辅助代码，禁止改任何业务逻辑、事件处理语义、控件 x:Name。**

## ⚠️ 2026-07-06 验收判定：阶段 2/3 未按方案交付，退回补做

用户实测反馈"美化一点没变化"，判定属实。已交付三个提交与方案的对应关系：
- `23a362d`（Phase 1 token 化）＝ 方案阶段 1 ✅ —— 本阶段**设计上就无视觉变化**，正常；
- `2f15144`（锚点圆点淡入动画）＝ 仅方案阶段 2 第 3 条的一小部分；
- `a399c4c`（CheckBox 圆角模板）＝ 仅方案阶段 3 第 5 条的一小部分。

**缺口（用户可见的主体全部未做）**：阶段 2 的消息气泡差异化、工作卡视觉降档 + 状态色条、
参与者 chips 胶囊化、输入区焦点态；阶段 3 的面板卡片分组、Sidebar 选中指示条、状态徽标组件化。
请 OPUS 按下方原方案逐条补齐，仍然每阶段单独提交 + 截图对比。

## 现状基调（已核实）

- 已有一套 "Fantasy" 浅色主题：主蓝 `#0250CC` + 点缀金 `#FDC800`，白底卡片 + 柔和投影
  （`Resources/MainWindowResources.xaml:3-30`，含 `CardShadow/SoftShadow/FloatShadow` 三级景深）。
- 全局 Button/TextBlock 有模板化 Style（MainWindowResources.xaml），基础质感是好的。
- **主要问题：硬编码色值大量散落**，未走资源引用（统计，个/文件）：
  `ManagementPanelsView.xaml` **137**、`ChatWorkspaceView.xaml` 32、`WorkbenchShellView.xaml` 30、
  `WorkspaceSidebarView.xaml` 7、`GlobalSearchOverlayView.xaml` 6、其余少量。
  散落色值基本与资源字典同值（#FFFFFF/#111827/#4B5563/#F7FAFF/#0250CC…），是"没引用"而非"不一致"。

## 阶段 1：色彩 Token 化（先做，机械性强、回归风险最低）

1. 把所有 XAML 硬编码 hex 替换为 `{StaticResource FantasyXxxBrush}` 等既有资源；
   对照表（资源字典已有，缺的补进 MainWindowResources.xaml，命名沿用 `Fantasy*` 前缀）：
   - `#FFFFFF`→FantasySurfaceBrush、`#111827`→FantasyTextBrush、`#4B5563`→FantasyMutedBrush、
     `#F7FAFF`→FantasyCanvasBrush、`#0250CC`→FantasyPrimaryBrush、`#D8E2F2`→FantasyLineBrush、
     `#D97706`→FantasyWarningBrush、`#16A34A`→FantasySuccessBrush、`#FDC800`→FantasySecondaryBrush、
     `#FFF7DA`→FantasyGoldWashBrush、`#EDF4FF`→FantasyHoverBrush、`#F1F6FF`→PanelAltBrush。
   - 带 alpha 的（如 `#7FFFFFFF`）新增语义资源（如 `FantasyScrimBrush`），不要留裸值。
2. 顺序：ManagementPanelsView → ChatWorkspaceView → WorkbenchShellView → 其余。
   每个文件替换完 `dotnet build` 一次再继续。
3. 此阶段**不改任何视觉效果**——替换前后截图必须像素级一致（验收标准）。

## 阶段 2：聊天工作区精修（ChatWorkspaceView + MainWindowDataTemplates）

1. **消息气泡**：用户/助手气泡差异化——用户气泡 FantasyPrimaryBrush 浅染背景（如 8% 透明度蓝）+ 右对齐圆角 12/12/2/12；
   助手气泡白底 SoftShadow + 圆角 12/12/12/2；行距、内边距统一 12/10。
2. **协作工作卡（思考卡）**：与正式回复气泡做视觉层级区分——工作卡降一档（更浅底色 PanelAltBrush、无投影、左侧 3px 状态色条：
   运行中=FantasyInfoBrush、完成=FantasySuccessBrush、失败=FantasyDangerBrush），回复气泡保持全强度。
   这与"回复锚定在自己思考卡正下方"的配对布局呼应，让"思考→定稿"一眼可读。
3. **话题锚点**（右侧圆点导航，`TopicAnchorNavigation.cs` 渲染的 ItemsControl）：
   - 圆点 hover 放大 1.25x + tooltip 首句已存在，补淡入动画（150ms）；
   - 用户蓝点用 FantasyPrimaryBrush、模型定稿绿点用 FantasySuccessBrush（当前语义保持不变）；
   - 当前视口对应锚点高亮描边（若需代码支持则跳过，纯 Style 能做的做）。
4. **参与者 chips**（头部）：统一为圆角胶囊（半径 10，FantasyHoverBrush 底 + FantasyLineBrush 描边），
   移除按钮 "×" hover 变 FantasyDangerBrush。
5. **输入区**：输入框获得焦点时描边过渡到 FantasyPrimaryBrush（100ms ColorAnimation），发送按钮主色实心化。

## 阶段 3：管理面板与外壳（ManagementPanelsView / WorkbenchShellView / Sidebar）

1. 面板分组标题统一：14px 半粗 + FantasyMutedBrush 小节说明，节间距 16。
2. 长表单区（模型/协作/学习面板）用卡片分组（白底 + SoftShadow + 圆角 10），替代目前的平铺描边。
3. Sidebar 选中项：左侧 3px 主色指示条 + FantasySelectedBrush 底，替代仅变底色。
4. 状态徽标统一组件化：`运行中/已停止/异常` 用同一 Style（圆点 + 文字），色取语义 Brush。
5. CheckBox/ComboBox/TextBox 模板统一圆角 6 与焦点态（与全局 Button 模板同语言）。

## 阶段 4（可选，单独报批后再做）：暗色主题

- 阶段 1 完成后色彩已全部 token 化，暗色只需第二份 ResourceDictionary 换值 + App.xaml 切换。
- 本轮不实现，仅在阶段 1 保证"无裸色值"这一前提。

## 禁改区

- 一切 `x:Name`（后台代码大量按名字访问，如 `ChatWorkspace.TopicAnchorsList`、`ActiveMetaTextElement`、`CollaborationWorkerDryRunBox`）。
- 消息列表的虚拟化/差量渲染相关属性（VirtualizingPanel 设置、ItemsSource 绑定方式）——动了会破坏"选中复制"与锚点点击的修复。
- 话题锚点 ItemsControl 的事件挂接方式（ButtonBase.ClickEvent 冒泡处理，`TopicAnchorNavigation.cs:21`）。
- 任何 `.cs` 中的逻辑（仅允许新增纯展示的 ValueConverter，且需在方案回执中列明）。
- `MainWindowResources.xaml` 既有资源的 **key 名**（可加不可改删）。

## 验收标准（Claude 执行）

1. `dotnet build desktop/SpiritKinDesktop` 0 warning 0 error；`dotnet test` 桌面基线 118 通过。
2. 阶段 1：抽查 3 个面板截图与改前一致；`grep -c '#[0-9A-Fa-f]\{6\}' Controls/*.xaml` 裸色值数量降到个位数（允许 BrandMark 等少量例外，需列清单）。
3. 阶段 2/3：官方启动器起全栈，肉眼确认聊天区（气泡/工作卡/锚点/chips）与三个管理面板；发起一场协作辩论确认工作卡状态色条随生命周期变化。
4. 每阶段单独提交，附截图（改前/改后对比）。
