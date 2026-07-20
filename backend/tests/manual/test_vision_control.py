import time


def main():
    from backend.devices.local_pc import LocalPCDevice
    from backend.perception.screen_io import take_screenshot

    device = LocalPCDevice()
    w, h = device.get_screen_size()
    print(f"🖥️ 屏幕分辨率: {w}x{h}")

    path = take_screenshot(save_path="test_screen.png")
    if path:
        print(f"📸 截图成功: {path}")
    else:
        print("❌ 截图失败")

    print("🖱️ 移动鼠标到 (100, 100)...")
    device.move_to(100, 100)
    time.sleep(0.5)
    device.move_to(w // 2, h // 2)


if __name__ == "__main__":
    main()