# WPF 桌面端改造前后对比 Spec（交 OPUS 逐条实施）

> **状态：已完成并归档（2026-07-16）。** 本文只用于历史追溯，不再引用或要求恢复旧概念稿。

> 配合 `multi_client_art_plan.md` 与 `multi_client_art_checklist.md` 使用。
> 本 spec 是"照着改不会误判"的逐组件施工清单——列出当前值 vs 目标值，OPUS 逐条替换即可。

---

## 1. 主题字典（Resources/Themes/Fantasy.*.xaml）

### Fantasy.Dark.xaml（夜间，暖炉编辑室）

| 键名 | 旧值（v2 蓝金） | 新值（v4 暖炭+铜） | 说明 |
|---|---|---|---|
| FantasyCanvasBrush | `#1A1D2E` 或类似冷蓝底 | `#15110C` | canvas 暖炭底 |
| FantasySurfaceBrush | `#252939` | `#1F1914` | surface 主内容面 |
| FantasySurface2Brush | 新增或更新 | `#2A221C` | surface-2 工具栏/次级面 |
| FantasySurface3Brush | 新增或更新 | `#372E27` | surface-3 hover 容器 |
| FantasyLineBrush | `#3A3F5C` | `#4E463F` | line 非关键分隔线 |
| FantasyLineStrongBrush | 新增 | `#73665D` | control-border 可交互边界 |
| FantasyTextBrush | `#E8E9F0` 或类似 | `#F0EAE4` | text 正文 |
| FantasyMutedBrush | `#8B92B8` | `#ACA39B` | muted 次级正文 |
| FantasyFaintBrush | 新增 | `#8B837B` | faint 非关键元数据 |
| FantasyAccentBrush | `#4A9EFF` 或类似冷蓝 | `#E28E3A` | accent 夜里铜即结构 |
| FantasyAccent2Brush | 新增 | `#B18872` | accent-2 辅助图表 |
| FantasyCopperBrush | `#D4AF37` 金色 或新增 | `#E99541` | copper 点睛炉火 |
| FantasyOnAccentBrush | 新增 | `#1F1914` | accent 实心底上文字 |
| FantasyOnCopperBrush | 新增 | `#1F1914` | copper 实心底上文字 |
| FantasySuccessFgBrush | `#5BCC80` | `#5BCC80`（保持） | success 文字色 |
| FantasySuccessBgBrush | 新增 | `#173523` | success 容器底色 |
| FantasyWarningFgBrush | `#EFBC4B` | `#EFBC4B`（保持） | warning 文字色 |
| FantasyWarningBgBrush | 新增 | `#3A2B10` | warning 容器底色 |
| FantasyDangerFgBrush | `#ED4A49` 或类似 | `#FF6B68` | danger 文字色 |
| FantasyDangerBgBrush | 新增 | `#3B1D1C` | danger 容器底色 |
| FantasyInfoFgBrush | 新增 | `#75AFFF` | info 文字色 |
| FantasyInfoBgBrush | 新增 | `#172A43` | info 容器底色 |
| FantasyFocusRingBrush | 新增 | `#E28E3A` | 键盘焦点环 |

**操作**：逐个键替换颜色值；旧键名保留（214 处引用靠键名），只换值；新增缺失的键。

### Fantasy.Light.xaml（日间，冷日光工作场）

| 键名 | 新值（v4 冷日光+铜） | 说明 |
|---|---|---|
| FantasyCanvasBrush | `#EEF5FA` | canvas 冷亮底 |
| FantasySurfaceBrush | `#F5FBFF` | surface |
| FantasySurface2Brush | `#E9F2F7` | surface-2 |
| FantasySurface3Brush | `#DEEAF1` | surface-3 |
| FantasyLineBrush | `#C4D4DF` | line |
| FantasyLineStrongBrush | `#70869A` | control-border |
| FantasyTextBrush | `#273A4C` | text 正文 |
| FantasyMutedBrush | `#4D667B` | muted |
| FantasyFaintBrush | `#5F7689` | faint |
| FantasyAccentBrush | `#126DB6` | accent 冷蓝结构 |
| FantasyAccent2Brush | `#0E6F9F` | accent-2 |
| FantasyCopperBrush | `#B44A00` | copper 暖铜点睛不变色相 |
| FantasyOnAccentBrush | `#FFFFFF` | accent 底上白字 |
| FantasyOnCopperBrush | `#FFFFFF` | copper 底上白字 |
| FantasySuccessFgBrush | `#187A43` | success 日间深绿 |
| FantasySuccessBgBrush | `#E7F5EC` | success 容器浅绿 |
| FantasyWarningFgBrush | `#875900` | warning 日间深棕 |
| FantasyWarningBgBrush | `#FFF3D6` | warning 容器浅黄 |
| FantasyDangerFgBrush | `#B52A2E` | danger 日间深红 |
| FantasyDangerBgBrush | `#FCEBEC` | danger 容器浅红 |
| FantasyInfoFgBrush | `#126DB6` | info 日间蓝 |
| FantasyInfoBgBrush | `#E8F2FB` | info 容器浅蓝 |
| FantasyFocusRingBrush | `#126DB6` | 键盘焦点环冷蓝 |

**操作**：全新建或覆盖旧 Light 字典（如果之前没有日主题）；键名与 Dark 对齐。

---

## 2. 聊天气泡（Controls/ChatWorkspaceView.xaml）

### 当前问题
- 投影：多处 `Effect="{StaticResource SoftShadow}"` → 夜里投影不可见，概念稿靠描边表层次
- 圆角不统一：部分 `CornerRadius="10"` 部分 `CornerRadius="6"`
- 缺少 user/assistant 底色分级

### 改动（逐处替换）

**聊天消息气泡容器**（约 :201 / :217 两处示例卡片 Border）：
```xml
<!-- 旧 -->
<Border BorderBrush="{DynamicResource LineBrush}"
        BorderThickness="1"
        CornerRadius="6"
        Background="{DynamicResource FantasySurfaceBrush}"
        Padding="10"
        Margin="0,0,0,10">

<!-- 新：去掉投影（如果有 Effect），统一圆角 6px，padding 改 14 -->
<Border BorderBrush="{DynamicResource FantasyLineBrush}"
        BorderThickness="1"
        CornerRadius="6"
        Background="{DynamicResource FantasySurfaceBrush}"
        Padding="14"
        Margin="0,0,0,14">
```

**user 消息 vs assistant 消息底色分级**（需在 DataTemplate 或代码里根据角色切换）：
- user 消息 → `Background="{DynamicResource FantasySurface2Brush}"`
- assistant 消息 → `Background="{DynamicResource FantasySurfaceBrush}"`

---

## 3. 工作卡（CollaborationPanel 渲染）

### 当前问题
- 工作卡可能有投影（代码里动态创建）
- 圆角可能不是 6px

### 改动（DesktopRenderRuntime.cs 或相关渲染代码）

```csharp
// 旧：可能有 Effect = softShadow
var border = new Border
{
    CornerRadius = new CornerRadius(8),
    Effect = Application.Current.Resources["SoftShadow"] as Effect,
    // ...
};

// 新：去投影，圆角 6，加描边
var border = new Border
{
    CornerRadius = new CornerRadius(6),
    BorderBrush = Application.Current.Resources["FantasyLineBrush"] as Brush,
    BorderThickness = new Thickness(1),
    Background = Application.Current.Resources["FantasySurfaceBrush"] as Brush,
    Padding = new Thickness(14),
    // Effect 去掉
};
```

---

## 4. 管理面板卡片（Controls/WorkbenchShellView.xaml）

### 当前问题
- :59 有 `Effect="{StaticResource SoftShadow}"`
- padding 是 `Padding="14,13"`（微调到 14）

### 改动

```xml
<!-- 旧 -->
<Border BorderBrush="{DynamicResource FantasyCardBorderBrush}"
        BorderThickness="1"
        CornerRadius="10"
        Background="{DynamicResource FantasySurfaceBrush}"
        Padding="14,13"
        Margin="0,0,0,14"
        Effect="{StaticResource SoftShadow}">

<!-- 新：去投影，圆角改 6，padding 统一 14 -->
<Border BorderBrush="{DynamicResource FantasyLineBrush}"
        BorderThickness="1"
        CornerRadius="6"
        Background="{DynamicResource FantasySurfaceBrush}"
        Padding="14"
        Margin="0,0,0,14">
```

**全局扫描**：用 Grep 找所有 `Effect="{StaticResource SoftShadow}"` 的 Border，除了悬浮菜单/弹窗，其他卡片类都去掉。

---

## 5. 按钮样式（Resources/MainWindowResources.xaml）

### 当前问题
- TinyButton / PrimaryButton / SecondaryButton 圆角可能是 3 或 5
- PrimaryButton 底色可能不是 copper

### 改动

```xml
<!-- TinyButton / SecondaryButton -->
<Setter Property="Template">
    <Setter.Value>
        <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}"
                    CornerRadius="4"  <!-- 改成 4 -->
                    Padding="{TemplateBinding Padding}">
                ...
            </Border>
        </ControlTemplate>
    </Setter.Value>
</Setter>

<!-- PrimaryButton -->
<Style x:Key="PrimaryButton" TargetType="Button">
    <Setter Property="Background" Value="{DynamicResource FantasyCopperBrush}" />  <!-- 用铜色 -->
    <Setter Property="Foreground" Value="{DynamicResource FantasyOnCopperBrush}" />  <!-- 铜底上文字 -->
    <Setter Property="BorderThickness" Value="0" />
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="Button">
                <Border Background="{TemplateBinding Background}"
                        CornerRadius="4"  <!-- 改成 4 -->
                        Padding="{TemplateBinding Padding}">
                    ...
                </Border>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
</Style>
```

---

## 6. 输入框（TextBox 默认样式）

### 当前问题
- 圆角可能不统一
- 边框色可能用 LineBrush（太弱，应该用 LineStrongBrush）

### 改动

```xml
<Style TargetType="TextBox">
    <Setter Property="BorderBrush" Value="{DynamicResource FantasyLineStrongBrush}" />  <!-- 改用 control-border -->
    <Setter Property="BorderThickness" Value="1" />
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="TextBox">
                <Border Background="{TemplateBinding Background}"
                        BorderBrush="{TemplateBinding BorderBrush}"
                        BorderThickness="{TemplateBinding BorderThickness}"
                        CornerRadius="4"  <!-- 统一 4 -->
                        Padding="8,6">
                    ...
                </Border>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
</Style>
```

---

## 7. 侧栏选中态（Controls/WorkspaceSidebarView.xaml）

### 当前问题
- 选中项没有左侧铜色指示条（概念稿左栏选中态有明显竖条）

### 改动

侧栏 ListBoxItem 模板里，选中态加左侧 3px 宽铜色竖条：

```xml
<Style TargetType="ListBoxItem">
    <Setter Property="Template">
        <Setter.Value>
            <ControlTemplate TargetType="ListBoxItem">
                <Border Background="{TemplateBinding Background}"
                        BorderThickness="0"
                        Padding="12,8">
                    <Grid>
                        <!-- 左侧铜色指示条：只在选中时显示 -->
                        <Rectangle x:Name="SelectionIndicator"
                                   Width="3"
                                   HorizontalAlignment="Left"
                                   Fill="{DynamicResource FantasyCopperBrush}"
                                   Visibility="Collapsed" />
                        <ContentPresenter Margin="8,0,0,0" />
                    </Grid>
                </Border>
                <ControlTemplate.Triggers>
                    <Trigger Property="IsSelected" Value="True">
                        <Setter TargetName="SelectionIndicator" Property="Visibility" Value="Visible" />
                        <Setter Property="Background" Value="{DynamicResource FantasySurface3Brush}" />
                    </Trigger>
                </ControlTemplate.Triggers>
            </ControlTemplate>
        </Setter.Value>
    </Setter>
</Style>
```

---

## 8. 品牌字（Controls/WindowBrandMark.xaml）

### 当前问题
- 可能用 New Rocker 字体（已弃用）
- 颜色可能不是 copper

### 改动

```xml
<!-- 旧 -->
<TextBlock Text="SpiritKin"
           FontFamily="Assets/Fonts/#New Rocker"
           Foreground="{DynamicResource FantasyAccentBrush}"
           ... />

<!-- 新 -->
<TextBlock Text="SpiritKin"
           FontFamily="Assets/Fonts/#Orbitron"
           Foreground="{DynamicResource FantasyCopperBrush}"
           FontWeight="Bold"
           ... />
```

---

## 9. 终端区（Controls/IntegratedTerminalPanelView.xaml）

### 当前问题
- 终端字体可能不是等宽 JetBrains Mono

### 改动

```xml
<TextBox x:Name="TerminalOutputBox"
         FontFamily="Assets/Fonts/#JetBrains Mono"  <!-- 改成等宽字体 -->
         FontSize="12"
         Background="{DynamicResource FantasyCanvasBrush}"
         Foreground="{DynamicResource FantasyTextBrush}"
         ... />
```

---

## 10. 右侧工作台降噪（Controls/WorkbenchShellView.xaml）

### 当前问题
- Grid.Row="1" 的 WorkbenchStatusPanelElement 默认展开，密集堆卡片

### 改动（概要，详细见方案 A3 第 6 点）

1. 新建顶部仪表条（默认显示）：
```xml
<Border x:Name="WorkbenchMetricsBarElement" Grid.Row="1" Height="32"
        Background="{DynamicResource FantasySurface2Brush}"
        BorderBrush="{DynamicResource FantasyLineBrush}" BorderThickness="0,0,0,1"
        Padding="12,0">
    <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
        <TextBlock Text="changes " Foreground="{DynamicResource FantasyMutedBrush}" />
        <TextBlock x:Name="MetricsChangesCount" Text="86" Foreground="{DynamicResource FantasyCopperBrush}" FontWeight="SemiBold" />
        <TextBlock Text=" · 分支 " Foreground="{DynamicResource FantasyMutedBrush}" Margin="8,0,0,0" />
        <TextBlock x:Name="MetricsBranchName" Text="codex/..." Foreground="{DynamicResource FantasyTextBrush}" />
        <TextBlock Text=" · ✓ gh" Foreground="{DynamicResource FantasySuccessFgBrush}" Margin="8,0,0,0" />
        <Button x:Name="ToggleWorkbenchDetailsButton" Content="展开 ⌄" Style="{StaticResource TinyButton}" Margin="12,0,0,0" />
    </StackPanel>
</Border>
```

2. 原 `WorkbenchStatusPanelElement`（ScrollViewer + 卡片堆）：
   - 默认 `Visibility="Collapsed"`
   - 点击"展开"按钮时切换 Visible/Collapsed
   - 状态持久化到 `State.Settings["workbench.details_expanded"]`

---

## 11. 健康横幅（MainWindow.xaml 顶部）

### 当前问题
- 可能用裸色值（不是 DynamicResource）
- warning 底色可能不配对

### 改动

```xml
<!-- 旧 -->
<Border Background="#3A2B10" BorderBrush="#EFBC4B" ... >
    <TextBlock Foreground="#EFBC4B" ... />
</Border>

<!-- 新 -->
<Border Background="{DynamicResource FantasyWarningBgBrush}"
        BorderBrush="{DynamicResource FantasyWarningFgBrush}"
        BorderThickness="0,0,0,1">
    <StackPanel Orientation="Horizontal">
        <TextBlock Text="⚠" Foreground="{DynamicResource FantasyWarningFgBrush}" Margin="0,0,6,0" />  <!-- 加图标 -->
        <TextBlock Foreground="{DynamicResource FantasyWarningFgBrush}" ... />
    </StackPanel>
</Border>
```

---

## 12. 焦点态（FocusVisualStyle 全局样式）

### 当前问题
- 可能没有统一的焦点环样式

### 改动（App.xaml 或 MainWindowResources.xaml）

```xml
<Style x:Key="DefaultFocusVisual">
    <Setter Property="Control.Template">
        <Setter.Value>
            <ControlTemplate>
                <Rectangle Stroke="{DynamicResource FantasyFocusRingBrush}"
                           StrokeThickness="2"
                           StrokeDashArray="1 2"
                           SnapsToDevicePixels="True" />
            </ControlTemplate>
        </Setter.Value>
    </Setter>
</Style>

<!-- 全局应用 -->
<Style TargetType="Button">
    <Setter Property="FocusVisualStyle" Value="{StaticResource DefaultFocusVisual}" />
</Style>
<Style TargetType="TextBox">
    <Setter Property="FocusVisualStyle" Value="{StaticResource DefaultFocusVisual}" />
</Style>
<!-- 其他可聚焦控件同理 -->
```

---

## 改动总结（OPUS 施工顺序建议）

1. **先改主题字典**（Fantasy.Dark.xaml / Fantasy.Light.xaml）→ 色温立刻生效
2. **去投影加描边**（全局扫描 SoftShadow，卡片类去掉）→ 层次表达对齐
3. **统一圆角**（卡片 6px、按钮/输入框 4px）→ 观感一致
4. **字体替换**（品牌 Orbitron、终端 JetBrains Mono）→ 字体分工落地
5. **侧栏选中指示条**（左侧 3px 铜条）→ 铜色点睛
6. **右侧工作台折叠**（默认只显仪表条）→ 信息密度降噪
7. **状态色配对**（健康横幅等用 fg+bg）→ 昼夜对比度合规
8. **焦点态**（统一焦点环样式）→ 键盘导航可见

每改完一项，重启桌面并排对照概念稿，确认视觉一致后再改下一项。
