# SpiritKin 3D 可动模型接入规范

当前 3D 形象按“邦布式电子屏角色”处理，默认使用稳定邦布参考 GLB：

- 不拉嘴巴、不拉脸。
- 不默认旋转内部骨骼；旧 SpiritKin 自动绑骨会牵连耳朵、脸罩、装饰件，前端程序化旋转会撕裂模型。
- 表情优先使用 GLB 内嵌电子屏材质：前端 Canvas 直接绑定到 `Bangboo_GLBScreen_Display` 材质。
- 独立 `screen_expression` Canvas 贴片已默认禁用；它曾经过大并漂浮在模型前方。
- 旧 `controls_legs` 自动切腿资产不再作为默认路线；那条路线会破坏模型完整性。

当前默认入口是稳定路线：

- `frontend/models/spirit3d/manifest.json` 固定加载 `models/spirit3d/reference/bangboo_pmx_glb_screen.glb?v=bangboo-visor-panel-11`。
- `screen_expression.enabled` 为 `false`。
- `builtin_face_expression.enabled` 为 `true`，`style` 为 `glb_screen_hud`。
- `builtin_face_expression.target_materials` 指向 `Bangboo_GLBScreen_Display`。
- `motion.procedural_bones` 为 `false`，`bone_motion` 为 `0`。
- `SpiritKinAI.cleanrig.glb`、`SpiritKinAI.rigged.glb`、`SpiritKinAI.controls_legs.glb`、`SpiritKinAI.riglite.glb` 仅作为实验资产保留，不应作为默认模型激活。
- 若未来要恢复真实头、手、腿动作，必须先在 Blender 里重新拆分/绑定干净 rig，并人工检查权重。

前端 `frontend/avatar_3d.html` 已默认启用内嵌屏幕表情层，配置位于：

```json
"screen_expression": {
  "enabled": false
},
"builtin_face_expression": {
  "enabled": true,
  "style": "glb_screen_hud",
  "target_materials": ["Bangboo_GLBScreen_Display"],
  "canvas_width": 2048,
  "canvas_height": 640
}
```

如果屏幕表情位置不贴合目镜，优先调整 `scripts/blender_bangboo_glb_screen.py` 中的 `surface_offset`、`height`、`z_center`、`front_y` 或 `visor_mask()`，重新导出 `bangboo_pmx_glb_screen.glb` 后用截图对比。不要重新启用独立大 Canvas 贴片。

## PMX 参考结论

用户提供的邦布 PMX 模型已解析为参考报告：

```text
frontend/models/spirit3d/reference/bangboo_pmx.raw_report.json
```

该 PMX 已转换为项目内默认参考资产的来源之一。原始报告显示：

- 3462 顶点、3809 面、7 个材质。
- 72 根骨骼，其中包含头、上半身、下半身、左右手臂、左右腿、眼睛、耳朵链。
- 27 个刚体、26 个关节，用于耳朵和挂件类部件的物理约束。
- 眼睛使用独立 `Bangboo_Eous001_Eye_D.png` 材质，身体使用 `Bangboo_Eous001_Body_D.png`。

对 SpiritKin 当前模型的启发：

- 目镜/电子眼应该是独立材质或独立贴片层，不应该直接混在身体贴图里。
- 邦布式“弹”来自干净骨骼、权重和物理约束，不应该靠前端自动切割 mesh。
- 真正细节动作需要 Blender 中补：头、上身、下身、左右手、左右腿、耳朵/挂件链、目镜屏幕材质槽。

当前默认 GLB 已删除原始 `Bangboo_Eye_Display` 眼部面，并新增独立屏幕对象：

- object：`Bangboo_GLBScreen_Display`
- material：`Bangboo_GLBScreen_Display`
- generator：`scripts/blender_bangboo_glb_screen.py`
- report：`frontend/models/spirit3d/reference/bangboo_pmx_glb_screen.report.json`

当前默认版本为 `bangboo-visor-panel-11`。该版本使用 `compact_arch_front_visor_curve`，是在 `panel-10` 过大的反馈后回收尺寸的版本，优先保持邦布原面罩比例。

## 当前前端动作接口

当前 `avatar_3d.html` 仍保留 `component_motion.action_profiles` 入口，但默认邦布参考 GLB 不启用旧 SpiritKin 分件动作。右侧按钮和 Runtime 事件走同一套入口：

```json
{
  "type": "avatar.motion",
  "payload": {
    "action": "wave",
    "duration_ms": 1400,
    "strength": 1
  }
}
```

当前稳定策略：

- `screen_expression` 不再用于默认显示。
- `builtin_face_expression` 负责 neutral、happy、thinking、waiting、alert、error、confused 等电子屏状态。
- 旧 SpiritKin `component_motion` 可作为实验保留，但不要直接打开 `controls_legs` 切腿资产。

旧 SpiritKin 分件控制资产仍保留，可用于对比或后续重建 clean rig：

- `ctrl_head_assembly`
- `ctrl_body`
- `ctrl_left_arm`
- `ctrl_right_arm`

当前默认邦布 GLB 不依赖这些控制节点。若要恢复身体动作，优先在 Blender 里做干净对象层级或 rig，再把 motion 配置升级为新资产专用配置。

如果后续总智能体要控制身体模块，应优先发 `avatar.motion` 事件，而不是模拟点击前端按钮。

## 下一阶段模型要求

当前网页层已经能做稳定展示、屏幕表情和保守分件动作。要进入更细致的邦布式物理动作，需要在 Blender 里制作新的干净 rig：

- 身体主体：`root / center / pelvis / torso / head`。
- 手臂：左右 shoulder、arm、forearm 或简化单段 arm。
- 腿：左右 upper_leg、lower_leg、foot，权重必须人工修正，不能再用自动切割。
- 耳朵/挂件：每侧 4 到 8 段链式骨骼，配合弹簧或物理约束。
- 目镜：独立 mesh 或独立材质槽，用于屏幕表情贴图，不受身体贴图影响。

没有这套干净 rig 前，前端只做“安全分件动作”，不启用内部骨骼旋转。

注意：当前 `SpiritKinAI.controls.glb` 没有 PMX 那样的 72 根骨骼、27 个刚体和 26 个关节，所以只能模拟“PMX 风格的轻微弹性”。真正的耳朵/挂件延迟、腿部 IK、身体软弹，需要进入 Blender 制作 clean rig 后再开启。

## 推荐目标格式

优先级：

1. **VRM**：最适合虚拟角色，支持标准表情、口型、头部/身体骨骼。
2. **GLB/GLTF**：适合通用 3D 角色，贴图可内嵌，浏览器兼容最好。
3. **带 rig/morph/clip 的 FBX**：可用，但浏览器材质兼容较差。

## 最低可动要求

要实现传统 3D 角色的真正表情和说话，模型至少需要以下之一：

- 脸部 morph/blendshape：如 `smile`, `blink`, `aa`, `ih`, `ou`, `ee`, `oh`。
- 骨骼 rig：至少包含 `head`, `neck`, `spine/chest`。
- 动画 clips：idle、talk、wave、nod 等。

## Blender 导出 GLB 建议

1. 导入 FBX。
2. 确认模型有 Armature 和骨骼权重。
3. 如需表情，添加 Shape Keys。
4. `File -> Export -> glTF 2.0`。
5. 推荐设置：
   - Format: `GLB`
   - Include: Selected Objects
   - Transform: `+Y Up` 默认即可
   - Data: 勾选 `Shape Keys`
   - Animation: 勾选 `Animation`
   - Materials: 尽量使用 PBR/Principled BSDF

## 自动化 Blender 处理入口

项目已提供一个基础自动处理流水线：

```powershell
python scripts/prepare_avatar3d_model.py --activate
```

它会调用 Blender 执行：

- 导入 `frontend/models/spirit3d/SpiritKinAI.fbx`
- 归一化模型高度
- 如果没有骨架，创建基础 humanoid 骨架
- 尝试自动权重绑定
- 默认不生成 Shape Keys，避免把身体、头盔、屏幕、装饰件误当成脸部网格拉裂
- 导出 `frontend/models/spirit3d/SpiritKinAI.rigged.glb`
- 生成并可激活 `frontend/models/spirit3d/manifest.rigged.json`

注意：自动导出的 `rigged/cleanrig` 资产只能用于实验验证。没有人工修权重前，不要把它们写入默认 `manifest.json`。

如果已经在 Blender 中确认了“只包含脸部”的网格，可以显式打开危险模式：

```powershell
python scripts/prepare_avatar3d_model.py --blender "E:\blender.exe" --unsafe-shape-keys --enable-morph-bindings
```

不要对当前 `SpiritKinAI.fbx` 直接启用这两个参数；它是风格化整身模型，自动区域推断会破坏脸部和身体。

如果 Blender 不在 PATH，指定路径：

```powershell
python scripts/prepare_avatar3d_model.py --blender "C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" --activate
```

只打印命令、不执行：

```powershell
python scripts/prepare_avatar3d_model.py --print-command
```

当前脚本做的是启发式绑定，适合先让项目跑起来。要达到稳定商用品质，需要在 Blender 中人工检查：

- 自动权重是否把头、身体、手臂分配正确
- 面部 Shape Keys 是否只影响脸部区域
- 嘴型是否会破面
- 眼睛/眉毛是否存在独立网格或可变形区域
- 贴图材质是否被 GLB 正确打包

## VRM 路线

1. 在 Blender 中准备 humanoid rig。
2. 安装 VRM Add-on for Blender。
3. 设置 humanoid bone mapping。
4. 设置 expressions / blendshape：
   - `happy`
   - `sad`
   - `angry`
   - `relaxed`
   - `surprised`
   - `aa`, `ih`, `ou`, `ee`, `oh`
5. 导出 `.vrm`。
6. 放到：`frontend/models/spirit3d/SpiritKinAI.vrm`。
7. 打开：
   - `avatar_3d.html?config=models/spirit3d/manifest.vrm.example.json`

## 贴图目录

如果继续使用 FBX，必须把 FBX 引用的贴图一起放进项目。

常见结构：

```text
frontend/models/spirit3d/SpiritKinAI.fbx
frontend/models/spirit3d/SpiritKinAI.fbm/*.png
```

如果控制台出现 `404 File not found`，说明 FBX 里引用了不存在的外部贴图。

## 当前前端已支持

`frontend/avatar_3d.html` 现在支持：

- `.fbx`
- `.glb`
- `.gltf`
- `.vrm`

VRM 会启用标准表情/口型接口；GLB/GLTF 如果带 animation clips，会自动播放。
