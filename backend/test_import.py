import traceback

try:
    from backend.perception import vision_analyzer as va
    print("模块导入成功: backend.perception.vision_analyzer")
    print("可用函数:", [name for name in dir(va) if not name.startswith('_')])
except Exception:
    print("模块导入失败")
    traceback.print_exc()