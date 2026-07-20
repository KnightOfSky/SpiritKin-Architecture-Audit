# 3D Avatar 陪伴感增强方案（交 GPT 实施）

> **状态更新（2026-07-16）：M5 已完成。** `frontend/avatar_3d.html` 已实现 `idleLife()`、idle wander、动作队列和 `motion.idle_life`；回复语义会生成稳定、可降级、可追踪的 emotion/action，并支持 `full/reduced/static` 动效偏好。本文后续伪代码和依赖估算属于历史方案，实际实现以 `backend/expression/semantic_reaction.py`、`backend/expression/emotion_library.json` 与现有共享 embedding provider 为准。

> 基于现有 `frontend/avatar_3d.html` 的 Three.js + VRM 架构，参考 Cyrene-Agent 的 Live2D 桌宠设计，实现语义驱动的 3D 模型动作 + 情感表达增强。
>
> **不涉及 TTS 嘴型同步**（你明确说了"并没有 TTS 匹配嘴型"），只做身体动作驱动 + 表情语义匹配 + 透明置顶桌宠模式（可选）。

---

## 0. 现状盘点

### 已有能力（frontend/avatar_3d.html）
1. **Three.js 3D 渲染**：VRM/FBX/GLTF 模型加载，OrbitControls 镜头控制
2. **身体动作系统**：component_motion + action_profiles 支持 nod/shake/wave/walk/walk_back/walk_left/walk_right 等动作
3. **骨骼驱动**：head_assembly/body/left_arm/right_arm/left_leg/right_leg 组件动画
4. **情感状态机**：emotion: neutral/happy/thinking/waiting/alert/error/confused（expressions 配置）
5. **字幕显示**：subtitle 浮层 + screen_expression 屏幕表情显示
6. **Runtime 状态聚合**：runtimeStage 聚合任务状态（idle/waiting/executing/planning/need_user/error/completed）
7. **WebSocket 实时桥接**：js/realtime_bridge.js 消费后端事件流

### 缺失能力（相比 Cyrene-Agent）
1. **表情语义匹配（已完成）**：共享 embedding provider 进行语义匹配；显式 `<emotion:...>` 协议仍优先
2. **动作语义匹配（已完成）**：版本化 reaction profile 将回复语义映射为 action，失败时走关键词降级
3. **透明置顶桌宠模式**：当前 embed 模式占满窗口，无"漂浮在桌面上"的效果
4. **表情-动作联动（已完成）**：后端 reaction trace 同时携带 emotion/action/intensity，前端按动效偏好执行
5. **闲置动画（已完成）**：已有 `idleLife()` 呼吸/摇摆、idle wander 与动作间恢复逻辑。

### 2026-07-16 实施与验收证据

1. `backend/app/runtime.py` 在所有助手回复的统一出口补充 `avatar_reaction`，不记录回复原文，只记录 profile、置信度、匹配方式、provider、降级原因与 source hash。
2. provider 不可用、超时或失败时，匹配器回退到同一版本库的关键词规则并标记 `degraded=true`；未命中则安全回到 neutral/idle。
3. `frontend/avatar_3d.html` 优先消费后端 trace，旧字符串匹配仅作为兼容回退；`reduced` 降低动作强度并关闭持续游走，`static` 抑制所有身体动作但保留表情和运行状态。
4. Playwright 在实际 Bangboo GLB 上验证 88 根模型骨骼、47 根已识别骨骼；语义问候触发 wave，static/error 保持根节点和骨骼静止。桌面与移动 canvas 截图均通过非空像素与颜色分布检查。

---

## 1. 表情语义匹配（P0，核心体验）

### 目标
Agent 输出文本 → 向量检索最相关表情 → 自动驱动 3D 模型表现

### 实现路径

#### 1.1 预定义表情库（backend/memory/emotion_library.json）
```json
{
  "neutral": {
    "keywords": ["正常", "平静", "冷静", "稳定", "普通", "default", "normal", "calm"],
    "description": "中性平静状态，无明显情绪波动",
    "intensity": 0,
    "pose": {"yaw": 0, "bob": 0, "scale": 1},
    "auto_action": ""
  },
  "happy": {
    "keywords": ["开心", "高兴", "愉快", "兴奋", "满意", "成功", "完成", "好的", "太棒了", "happy", "joy", "excited", "pleased"],
    "description": "积极愉悦，任务完成或收到正面反馈",
    "intensity": 0.7,
    "pose": {"yaw": 0.02, "bob": 0.015, "scale": 1.02},
    "auto_action": "nod"
  },
  "thinking": {
    "keywords": ["思考", "分析", "规划", "考虑", "让我想想", "嗯", "好问题", "thinking", "analyzing", "planning", "considering"],
    "description": "正在思考、规划或分析问题",
    "intensity": 0.5,
    "pose": {"yaw": -0.05, "bob": -0.01, "scale": 1},
    "auto_action": ""
  },
  "waiting": {
    "keywords": ["等待", "待命", "准备", "就绪", "waiting", "ready", "standby"],
    "description": "等待用户输入或外部事件",
    "intensity": 0.3,
    "pose": {"yaw": 0, "bob": 0.005, "scale": 1},
    "auto_action": ""
  },
  "alert": {
    "keywords": ["注意", "警告", "小心", "重要", "紧急", "alert", "warning", "caution", "important"],
    "description": "需要用户注意或有重要信息",
    "intensity": 0.6,
    "pose": {"yaw": 0.03, "bob": 0.02, "scale": 1.03},
    "auto_action": "nod"
  },
  "error": {
    "keywords": ["错误", "失败", "抱歉", "对不起", "出错", "问题", "无法", "不行", "error", "failed", "sorry", "cannot"],
    "description": "任务失败或遇到错误",
    "intensity": 0.8,
    "pose": {"yaw": -0.03, "bob": -0.015, "scale": 0.98},
    "auto_action": "shake"
  },
  "confused": {
    "keywords": ["困惑", "不明白", "不清楚", "什么", "为什么", "confused", "unclear", "what", "why"],
    "description": "不理解用户意图或信息不足",
    "intensity": 0.5,
    "pose": {"yaw": 0.04, "bob": 0, "scale": 1},
    "auto_action": "shake"
  }
}
```

#### 1.2 向量检索模块（backend/memory/emotion_matcher.py）
```python
import json
from pathlib import Path
from sentence_transformers import SentenceTransformer
import numpy as np

# 使用轻量模型（BGE-small 或 all-MiniLM-L6-v2，~80MB）
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMOTION_LIBRARY_PATH = Path(__file__).parent / "emotion_library.json"

class EmotionMatcher:
    def __init__(self):
        self.model = SentenceTransformer(MODEL_NAME)
        self.emotions = self._load_emotions()
        self.embeddings = self._precompute_embeddings()

    def _load_emotions(self):
        with open(EMOTION_LIBRARY_PATH, encoding="utf-8") as f:
            return json.load(f)

    def _precompute_embeddings(self):
        """预计算所有表情的 description + keywords 向量"""
        embeddings = {}
        for emotion_id, emotion_data in self.emotions.items():
            text = f"{emotion_data['description']} {' '.join(emotion_data['keywords'])}"
            embeddings[emotion_id] = self.model.encode(text, normalize_embeddings=True)
        return embeddings

    def match_emotion(self, text: str, threshold: float = 0.3) -> dict:
        """
        根据文本匹配最相关表情

        Args:
            text: Agent 输出的文本
            threshold: 相似度阈值（低于此值返回 neutral）

        Returns:
            {"emotion": "happy", "intensity": 0.7, "pose": {...}, "auto_action": "nod", "score": 0.85}
        """
        # 1. 字符串匹配（快速路径）
        text_lower = text.lower()
        for emotion_id, emotion_data in self.emotions.items():
            if any(keyword in text_lower for keyword in emotion_data["keywords"]):
                return {
                    "emotion": emotion_id,
                    **emotion_data,
                    "score": 1.0,
                    "match_type": "keyword"
                }

        # 2. 向量检索（语义匹配）
        query_emb = self.model.encode(text, normalize_embeddings=True)
        scores = {
            emotion_id: float(np.dot(query_emb, emb))
            for emotion_id, emb in self.embeddings.items()
        }
        best_emotion = max(scores, key=scores.get)
        best_score = scores[best_emotion]

        if best_score < threshold:
            return {
                "emotion": "neutral",
                **self.emotions["neutral"],
                "score": 0.0,
                "match_type": "fallback"
            }

        return {
            "emotion": best_emotion,
            **self.emotions[best_emotion],
            "score": best_score,
            "match_type": "semantic"
        }

# 全局单例
_matcher = None
def get_emotion_matcher():
    global _matcher
    if _matcher is None:
        _matcher = EmotionMatcher()
    return _matcher
```

#### 1.3 后端事件流注入表情（backend/app/realtime_bridge.py）
```python
# 在 broadcast_realtime_event 中注入表情匹配

def broadcast_realtime_event(event_type: str, payload: dict, ...):
    # 原有逻辑...

    # 新增：assistant.message 事件自动匹配表情
    if event_type == "assistant.message" and "text" in payload:
        from backend.memory.emotion_matcher import get_emotion_matcher
        try:
            matcher = get_emotion_matcher()
            emotion_match = matcher.match_emotion(payload["text"])

            # 注入表情到 payload
            payload["emotion_match"] = {
                "emotion": emotion_match["emotion"],
                "intensity": emotion_match["intensity"],
                "pose": emotion_match["pose"],
                "auto_action": emotion_match["auto_action"],
                "score": emotion_match["score"],
                "match_type": emotion_match["match_type"]
            }
        except Exception as exc:
            # 匹配失败不影响事件广播
            pass

    # 继续原有广播逻辑...
```

#### 1.4 前端消费表情（frontend/avatar_3d.html）
```javascript
// 在 WebSocket onmessage 中消费 emotion_match

function handleRealtimeEvent(eventType, payload) {
    // 原有逻辑...

    // 新增：消费 emotion_match
    if (payload.emotion_match) {
        const match = payload.emotion_match;

        // 设置情感状态（已有逻辑）
        state.emotion = match.emotion;
        updateHUD();

        // 应用表情 pose
        if (match.pose) {
            emotionImpulse.yaw = match.pose.yaw || 0;
            emotionImpulse.bob = match.pose.bob || 0;
            emotionImpulse.scale = match.pose.scale || 1;
            emotionImpulse.decay = 0.95; // 衰减系数
        }

        // 自动触发动作
        if (match.auto_action && !motionImpulse.active) {
            queueAction(match.auto_action, match.intensity || 1);
        }

        // HUD 显示匹配类型（调试用）
        if (match.match_type === "semantic") {
            console.log(`[Emotion] Semantic match: ${match.emotion} (score: ${match.score.toFixed(2)})`);
        }
    }

    // 继续原有逻辑...
}
```

---

## 2. 闲置动画（P1，生命感）

### 目标
模型 idle 时有微弱呼吸/摇摆，避免完全静止

### 实现路径

#### 2.1 配置扩展（manifest.json）
```json
{
  "motion": {
    "idle_life": true,
    "idle_life_amp": 1.0,
    "idle_breathing_scale": 0.008,
    "idle_bob": 0.004,
    "idle_yaw": 0.015
  }
}
```

#### 2.2 前端渲染循环（avatar_3d.html）
```javascript
// 在动画循环中应用 idle_life

function animate() {
    requestAnimationFrame(animate);
    const dt = clk.getDelta();
    const now = performance.now();

    // 原有逻辑...

    // 新增：idle_life 呼吸和微弱摇摆
    if (currentConfig.motion?.idle_life && !motionImpulse.active && state.phase === 'idle') {
        const breathPhase = (now * 0.0005) % (Math.PI * 2); // 慢速呼吸周期
        const swayPhase = (now * 0.0003) % (Math.PI * 2);   // 更慢的摇摆

        const breathScale = Math.sin(breathPhase) * (currentConfig.motion.idle_breathing_scale || 0.008);
        const bobAmount = Math.sin(breathPhase) * (currentConfig.motion.idle_bob || 0.004);
        const yawAmount = Math.sin(swayPhase) * (currentConfig.motion.idle_yaw || 0.015);

        // 应用到 avatarRoot 或 head_assembly
        if (avatarRoot) {
            avatarRoot.scale.y = 1 + breathScale * (currentConfig.motion.idle_life_amp || 1);
            avatarRoot.position.y = manualYOffset + bobAmount;
            avatarRoot.rotation.y = yawAmount;
        }
    }

    // 继续原有逻辑...
    renderer.render(scene, cam);
}
```

---

## 3. 透明置顶桌宠模式（P2，可选）

### 目标
像 Cyrene-Agent 的 Live2D 一样，3D 模型漂浮在桌面上，透明背景 + 鼠标穿透（点击时穿透到后面窗口）

### 实现路径

#### 3.1 WPF 透明置顶窗口（desktop/SpiritKinDesktop/Features/Avatar/AvatarFloatWindow.xaml）
```xml
<Window x:Class="SpiritKinDesktop.Features.Avatar.AvatarFloatWindow"
        xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="SpiritKin Avatar"
        Width="320" Height="480"
        WindowStyle="None"
        AllowsTransparency="True"
        Background="Transparent"
        Topmost="True"
        ShowInTaskbar="False"
        ResizeMode="NoResize">
    <Grid>
        <!-- WebView2 加载 avatar_3d.html?embed=1&float=1 -->
        <wv2:WebView2 x:Name="AvatarWebView" />
    </Grid>
</Window>
```

#### 3.2 鼠标穿透（AvatarFloatWindow.xaml.cs）
```csharp
using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

namespace SpiritKinDesktop.Features.Avatar;

public partial class AvatarFloatWindow : Window
{
    private const int GWL_EXSTYLE = -20;
    private const int WS_EX_TRANSPARENT = 0x00000020;
    private const int WS_EX_LAYERED = 0x00080000;

    [DllImport("user32.dll")]
    private static extern int GetWindowLong(IntPtr hwnd, int index);

    [DllImport("user32.dll")]
    private static extern int SetWindowLong(IntPtr hwnd, int index, int newStyle);

    public AvatarFloatWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        // 设置鼠标穿透
        var hwnd = new WindowInteropHelper(this).Handle;
        var extendedStyle = GetWindowLong(hwnd, GWL_EXSTYLE);
        SetWindowLong(hwnd, GWL_EXSTYLE, extendedStyle | WS_EX_TRANSPARENT | WS_EX_LAYERED);

        // 加载 avatar_3d.html
        var url = $"http://127.0.0.1:8787/avatar_3d.html?embed=1&float=1&ws=...&token=...";
        AvatarWebView.Source = new Uri(url);
    }
}
```

#### 3.3 前端适配（avatar_3d.html）
```javascript
// 检测 float 模式
if (params.get('float') === '1') {
    document.body.classList.add('float-mode');
}
```

```css
/* float 模式样式 */
body.float-mode {
    background: transparent !important;
}

body.float-mode #stage {
    background: transparent !important;
}

body.float-mode #stage::before,
body.float-mode #stage::after,
body.float-mode .atelier-hearth-floor {
    display: none; /* 去掉背景光效 */
}

body.float-mode .side,
body.float-mode .hud,
body.float-mode .runtime-stage {
    display: none; /* 隐藏所有 UI，只留 3D 模型 */
}

body.float-mode .subtitle {
    /* 保留字幕，调整位置到模型下方 */
    left: 10px;
    right: 10px;
    bottom: 10px;
}
```

---

## 4. 表情-动作联动（P1，自动表现）

### 目标
情感变化时自动触发合适的身体动作，无需手动 `<action:nod>`

### 实现路径

#### 4.1 联动规则（emotion_library.json 的 auto_action）
已在 §1.1 定义：
- happy → nod（点头）
- error → shake（摇头）
- alert → nod（点头）
- confused → shake（摇头）
- thinking → ""（无动作，只调整 pose）
- waiting → ""（无动作）

#### 4.2 前端消费（已在 §1.4 实现）
```javascript
// 自动触发动作
if (match.auto_action && !motionImpulse.active) {
    queueAction(match.auto_action, match.intensity || 1);
}
```

#### 4.3 动作队列（避免动作重叠）
```javascript
// 已有 motionQueue，确保逻辑正确

function queueAction(actionType, strength = 1) {
    if (!actionType) return;

    // 如果当前有动作在播放，加入队列
    if (motionImpulse.active) {
        motionQueue.push({ type: actionType, strength });
        return;
    }

    // 否则立即播放
    playAction(actionType, strength);
}

function playAction(actionType, strength = 1) {
    const profile = actionProfile(actionType);
    motionImpulse = {
        type: actionType,
        startedAt: performance.now(),
        duration: profile.duration_ms || 1000,
        strength: strength * (profile.strength || 1)
    };
}

// 在动画循环中检查队列
function animate() {
    // ...原有逻辑

    // 动作播放完毕时，播放队列中的下一个
    if (motionImpulse.active && performance.now() >= motionImpulse.startedAt + motionImpulse.duration) {
        motionImpulse.active = false;

        if (motionQueue.length > 0) {
            const next = motionQueue.shift();
            playAction(next.type, next.strength);
        }
    }
}
```

---

## 5. 动作语义匹配（P2，可选）

### 目标
Agent 输出 "我同意" → 自动触发 nod，无需 `<action:nod>`

### 实现路径

#### 5.1 动作库（backend/memory/action_library.json）
```json
{
  "nod": {
    "keywords": ["同意", "是的", "对", "没错", "好的", "可以", "确认", "yes", "ok", "agree", "confirm"],
    "description": "表示同意、确认或肯定"
  },
  "shake": {
    "keywords": ["不", "否", "拒绝", "不行", "不可以", "不对", "错", "no", "reject", "disagree"],
    "description": "表示拒绝、否定或不同意"
  },
  "wave": {
    "keywords": ["你好", "嗨", "哈喽", "打招呼", "再见", "hello", "hi", "bye", "goodbye"],
    "description": "打招呼或告别"
  },
  "think": {
    "keywords": ["让我想想", "思考", "考虑", "分析", "hmm", "thinking"],
    "description": "表示正在思考"
  }
}
```

#### 5.2 向量检索（复用 EmotionMatcher 模式）
```python
# backend/memory/action_matcher.py

class ActionMatcher:
    # 与 EmotionMatcher 相同实现，只是匹配 action_library.json
    pass

# 在 realtime_bridge.py 中同时注入 action_match
payload["action_match"] = get_action_matcher().match_action(payload["text"])
```

#### 5.3 前端消费
```javascript
if (payload.action_match && payload.action_match.action) {
    queueAction(payload.action_match.action, 1);
}
```

---

## 6. 实施顺序与验收

### 实施顺序（建议 GPT 按此顺序）
1. **P0-1 表情库 + 向量检索**（§1.1 + §1.2）→ 可独立测试
2. **P0-2 后端注入表情**（§1.3）→ 可在 realtime_bridge 中 print 验证
3. **P0-3 前端消费表情**（§1.4）→ 看到 emotion 自动切换
4. **P1-1 闲置动画**（§2）→ 模型有呼吸感
5. **P1-2 表情-动作联动**（§4）→ happy 自动 nod
6. **P2-1 透明置顶桌宠**（§3）→ 可选功能，UI 体验升级
7. **P2-2 动作语义匹配**（§5）→ 可选功能，锦上添花

### 验收方法
1. **表情语义匹配**：
   - 桌面端发消息："太棒了，成功了！" → Avatar 自动切换到 happy 表情 + 点头
   - 发消息："出错了，失败" → Avatar 自动切换到 error 表情 + 摇头
   - 发消息："让我想想这个问题" → Avatar 切换到 thinking 表情（头微低）
2. **闲置动画**：
   - idle 状态下，模型有微弱呼吸/摇摆（不是完全静止）
3. **透明置顶桌宠**：
   - WPF 主窗口外，3D 模型漂浮在桌面右下角
   - 鼠标点击模型区域穿透到后面窗口
4. **表情-动作联动**：
   - 后端返回 error 状态 → 模型自动摇头
   - 后端返回 completed 状态 → 模型自动点头

### 风险与局限
1. **向量模型下载**：sentence-transformers/all-MiniLM-L6-v2 约 80MB，首次启动需联网下载（可本地打包）
2. **向量检索延迟**：首次加载模型 ~2s，之后每次匹配 <50ms（可接受）
3. **透明置顶窗口内存**：WebView2 约 100MB，用户可选关闭
4. **表情识别准确率**：语义匹配约 70-80% 准确，fallback 到 neutral 不影响体验

---

## 7. 与 Cyrene-Agent 对比

| 功能 | Cyrene-Agent (Live2D) | SpiritKinAI (3D Model) | 本方案 |
|---|---|---|---|
| 模型类型 | 2D Live2D（pixi.js） | 3D VRM/FBX（Three.js） | 3D VRM/FBX |
| 嘴型同步 | ✅ TTS 音频驱动 | ❌ 无 | ❌ 无（你明确不需要） |
| 身体动作 | ✅ 表情 + 动作 | ✅ 已有 nod/shake/wave/walk | ✅ 复用现有 action_profiles |
| 表情语义匹配 | ✅ BGE-M3 向量检索 | ✅ 共享 embedding + 关键词降级 | ✅ 已落地，不新增 Avatar 专用模型 |
| 透明置顶桌宠 | ✅ Electron 透明窗口 | ❌ 无 | ✅ WPF 透明窗口 + WebView2 |
| 闲置动画 | ✅ Live2D 呼吸 | ✅ 呼吸/摇摆/游走 | ✅ 已落地，支持 reduced/static |
| 内存占用 | ~150MB (Electron + pixi) | ~50MB (WebView2) | ~150MB (加透明窗口) |

**优势**：3D 模型比 2D 更立体、科技感更强，且你已有完整动作系统，只需补语义匹配。
**劣势**：无嘴型同步（但你说不需要），内存占用略高。

---

## 总结

本方案在你现有 3D Avatar 架构上的落地状态：
1. **P0 表情语义匹配**（共享向量服务 + 可追踪降级）→ 已完成
2. **P1 闲置动画**（呼吸/摇摆）→ 已完成
3. **P1 表情-动作联动**（happy→nod/wave, error→shake）→ 已完成
4. **P2 透明置顶桌宠**（可选）→ 桌面陪伴感
5. **P2 动作语义匹配** → 已完成

剩余可选项只有透明置顶桌宠模式，不属于本轮 M5 范围。
