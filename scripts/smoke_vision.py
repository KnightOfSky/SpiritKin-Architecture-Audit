from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _screen_smoke(output: Path) -> int:
    from backend.perception.screen_io import take_screenshot

    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        path = take_screenshot(save_path=str(output))
    except Exception as exc:
        path = ""
        print(f"error={exc!r}")
    elapsed = time.perf_counter() - started
    ok = bool(path and Path(path).exists() and Path(path).stat().st_size > 0)
    print("# 眼睛/屏幕截图 smoke")
    print(f"elapsed={elapsed:.2f}s")
    print(f"path={path!r}")
    print("[PASS] 屏幕截图可用" if ok else "[FAIL] 屏幕截图失败")
    return 0 if ok else 1


def _ocr_smoke() -> int:
    try:
        from backend.perception.screen_io import extract_text_from_screen
    except ModuleNotFoundError as exc:
        print("# 眼睛/OCR smoke")
        print(f"[FAIL] OCR 依赖缺失：{exc.name}")
        print("提示：如需 OCR，请安装 pytesseract 并配置 Tesseract-OCR 语言包。")
        return 1

    started = time.perf_counter()
    text = extract_text_from_screen()
    elapsed = time.perf_counter() - started
    print("# 眼睛/OCR smoke")
    print(f"elapsed={elapsed:.2f}s")
    print(f"text={text!r}")
    if text:
        print("[PASS] OCR 读取到屏幕文字")
    else:
        print("[WARN] OCR 没有读到文字；可能是屏幕无文字、Tesseract 未安装或语言包缺失")
    return 0


def _camera_smoke() -> int:
    from backend.perception.vision_analyzer import analyze_gesture

    started = time.perf_counter()
    result = analyze_gesture()
    elapsed = time.perf_counter() - started
    ok = bool(result and not str(result).startswith("❌"))
    print("# 眼睛/摄像头手势表情 smoke")
    print(f"elapsed={elapsed:.2f}s")
    print(f"result={result!r}")
    print("[PASS] 摄像头视觉分析可用" if ok else "[FAIL] 摄像头视觉分析失败")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="实机验证眼睛能力：屏幕截图 / OCR / 摄像头手势表情")
    parser.add_argument("--mode", choices=("screen", "ocr", "camera"), default="screen")
    parser.add_argument("--output", default="data/smoke/vision_screen.png", help="screen 模式截图保存位置")
    args = parser.parse_args()

    if args.mode == "ocr":
        return _ocr_smoke()
    if args.mode == "camera":
        return _camera_smoke()
    return _screen_smoke(Path(args.output))


if __name__ == "__main__":
    sys.exit(main())