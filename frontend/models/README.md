## Live2D 模型目录约定

- 建议结构：`frontend/models/<role>/<role>.model3.json`
- 建议在 `frontend/models/manifest.json` 中维护角色配置
- 页面支持：`live2d.html?role=spirit&config=models/manifest.json&autoload=1`
- 默认仓库提供 `manifest.json`，但 `spirit.ready=false`，表示真实模型资源尚未放入。

### manifest 字段

- `defaultRole`: 默认角色名
- `roles.<name>.model`: model3.json URL
- `roles.<name>.ready`: 是否允许页面按该角色自动加载真实模型；没有放入资源前保持 `false`
- `roles.<name>.scale`: 可选缩放
- `roles.<name>.expressions`: emotion -> expression 名称映射
- `roles.<name>.motions`: action/emotion -> motion 名称映射

放入真实资源后：

1. 将模型包放到 `frontend/models/<role>/`。
2. 确认 `roles.<role>.model` 指向真实 `.model3.json`。
3. 按模型里的 expression / motion 名称更新映射。
4. 将 `roles.<role>.ready` 改为 `true`。
5. 运行 `python scripts/validate_live2d_manifest.py` 检查路径与字段。