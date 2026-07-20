# MainWindow 拆分方案（缺陷 #4 桌面侧）

> 方案制定：Claude（2026-07-04）。执行：GPT。每切片提交后由 Claude 审查 diff。
> 配套背景见 `docs/ai_collaboration_context.md`「2026-07-03 Full-Project Defect Review」#4。

## 现状事实（2026-07-04 调研核实）

- `partial class MainWindow` 分散在 **126 个文件 / 28,398 行**，已按 `Features/<簇>/` 物理分目录（Workflows 27 文件 4,957 行最大；Shell 14/2,768；Agents 11/2,734；Context 3/2,502 其中 `CollaborationPanel.cs` 单文件 1,915 行；Navigation 11/2,446；Runtime 11/2,345；Composer 9/2,041；其余见下方切片表）。
- `MainWindow.xaml` 本身只有 83 行，组合 10 个 UserControl；巨型 XAML 是 `ManagementPanelsView.xaml`（3,211 行）。UserControl 层只做「元素公开 + 事件转发」，真正的接线在 `Features/Shell/MainWindowBootstrap.cs`：**302 处 `Click +=`**（189 个 async lambda + 94 个同步 lambda + 18 个方法组），另有 110 个 `*_Click` 方法。
- 共享状态集中在 `Features/Shell/MainWindowState.cs`：**约 257 个私有字段**。跨簇热点：`_state`（31 文件引用）、`ApiBase`（31）、`RenderState*`（22 文件 / 79 个 Render 方法）、`_jsonOptions`（13）、`_http`（7）。
- `ViewModels/` 16 个文件全是被动 DTO；**项目内 0 个 ICommand**。真正独立的服务类只有 `Services/DesktopApiClient.cs`。
- **无任何 C# 测试工程**，无 .sln。csproj 为 net8.0-windows SDK 风格，加测试工程无障碍。CI 已有 `dotnet build -c Release`（windows-latest）。

## 目标与非目标

**目标**：把业务逻辑从 `partial MainWindow` 移入普通类（每簇一个 controller），让 MainWindow 退化为组合根；共享状态按簇归属拆开；新逻辑可单测。验收指标：`partial class MainWindow` 文件数 126 → **≤ 10**（仅 Shell 组合根残留），每个提取出的 controller 至少有构造/纯逻辑单测。

**非目标（明确不做）**：
- 不做全面 MVVM 化（不强推 ICommand/绑定重写）。UserControl 的「属性公开 + 事件转发」层保留为接缝，MVVM 化留待后续独立决策。
- 不改任何 XAML 视觉结构、不改行为。`ManagementPanelsView.xaml` 的 3,211 行本轮不动。
- 不拆多项目（除新增测试工程外）。

## 硬性规则（对齐 Python 侧 carve 的既定约定）

1. **行为保持**。每切片只做搬移+改引用，禁止顺手重构逻辑、改字符串、改事件顺序。
2. **不留委托空壳**。逻辑移入 controller 后，MainWindow partial 里的旧方法必须删除，调用点直接改调 controller；禁止留 `void Foo() => _fooController.Foo();` 这类转发（Bootstrap 接线 lambda 改为直接调 controller 除外，那是接线不是空壳）。
3. **controller 不得持有 MainWindow 引用**。依赖通过构造注入：`DesktopApiClient`/`HttpClient`/`JsonSerializerOptions`/本簇 state 类/必要的 UI 回调（`Action`/`Func` 委托或窄接口）。如果发现某簇必须拿整个 MainWindow 才能工作，停下来在工作反馈里说明耦合点，不要硬塞。
4. **UI 线程约定显式化**。controller 内需要回 UI 线程的地方通过注入的 `Dispatcher` 或回调委托完成，不隐式假设。
5. **每切片一轮验证后提交**：`dotnet build desktop/SpiritKinDesktop/SpiritKinDesktop.csproj -c Release` 零新增 warning + `dotnet test`（切片 0 之后）+ 手工冒烟（启动桌面，打开该簇对应面板，确认原功能可用）。一切片一提交，commit message 说明簇名和行数变化。
6. **命名/位置**：`Features/<簇>/<簇>Controller.cs`（普通类）+ `Features/<簇>/<簇>State.cs`（该簇字段）。生成文件 `RealtimeContract.g.cs` 不动。

## 切片顺序

| 切片 | 内容 | 规模 | 备注 |
|---|---|---|---|
| 0 | 新建 `desktop/SpiritKinDesktop.Tests`（xunit，net8.0-windows），CI 加 `dotnet test`；建 .sln 收编两工程 | 小 | 先有回归门禁再动刀 |
| 1 | 试点 5 个单文件簇：Mcp、Safety、Search、Modules、Evolution → 各自成 controller | ~1,800 行 | 目的是确立注入模式；每个附单测 |
| 2 | `MainWindowState.cs` 257 字段按簇归属拆成 per-cluster state 类；MainWindow 保留各 state 实例作为组合根字段 | 大 | 只搬字段不改逻辑；`_state`/`ApiBase`/`_http`/`_jsonOptions` 等真正全局的留在 Shell |
| 3 | Workflows 簇（27 文件 / 4,957 行，自含性最好） | 大 | 图渲染逻辑独立，优先 |
| 4 | Agents（11/2,734）、Composer（9/2,041）、Learning（7/1,521）、MobileManagement（5/1,164）、Workbench（5/1,029）、Services（4/630）、Workspace（6/1,644） | 大 | 每簇一切片，按此顺序 |
| 5 | Context/CollaborationPanel（1,915 行单文件） | 中 | 先在文件内按职责分组再提取 |
| 6 | Navigation（11/2,446，含跨面板联动 CrossPanelSelectionHandlers） | 中 | 依赖多簇 controller，放在其后 |
| 7 | Runtime 渲染管线（11/2,345，RenderState 79 个 Render 方法） | 大 | 22 个文件引用 RenderState，是最深耦合，倒数第二 |
| 8 | Shell/Bootstrap：302 处事件接线按簇下放为各 controller 的 `Wire(XxxView view)` 方法；MainWindow 只剩构造 + 逐簇 Wire 调用 | 大 | 最后收口 |

## 每切片工作反馈要求（供审查）

GPT 每完成一切片，在 `docs/ai_collaboration_context.md` 追加工作反馈，包含：簇名、移出行数、controller 依赖清单（注入了什么）、遇到的耦合点及处理方式、build/test/冒烟结果。Claude 据此 + diff 审查，不通过则该切片返工后再进下一片。

## 已知风险

- Bootstrap 的 async lambda 大量捕获 MainWindow 成员，切片 8 之前它们会临时改为捕获 controller/state 实例——允许，但不允许为此新增公开可变字段。
- 手工冒烟不可省：桌面无 UI 自动化，`dotnet build` 通过 ≠ 面板可用。
- 若某切片发现两簇共享私有方法，归属给主要使用方，另一方通过 controller 公开方法调用；禁止复制粘贴两份。
