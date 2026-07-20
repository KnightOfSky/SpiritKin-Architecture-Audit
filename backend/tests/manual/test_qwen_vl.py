import sys


def main():
    import torch

    from backend.perception.qwen_vl_analyzer import QwenVLAnalyzer
    from backend.perception.screen_io import take_screenshot

    screenshot_path = take_screenshot(save_path="qwen_test.png")
    if not screenshot_path:
        sys.exit("❌ 截图失败")

    print("🧠 正在加载 Qwen-VL（首次运行较慢）...")
    analyzer = QwenVLAnalyzer(device="cuda" if torch.cuda.is_available() else "cpu")

    query = "这张图显示的是什么界面？有哪些主要元素？"
    response = analyzer.analyze_image(screenshot_path, query)

    if response:
        print("🤖 Qwen-VL 回答:")
        print(response)
    else:
        print("❌ Qwen-VL 推理失败，请检查显存或模型路径")


if __name__ == "__main__":
    main()