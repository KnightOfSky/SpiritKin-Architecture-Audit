def main():
    from backend.app.runtime import main as runtime_main

    print("🎙️ 端到端测试开始！")
    print("👉 请说：'帮我分析这个界面' 或 '截图'")
    runtime_main()


if __name__ == "__main__":
    main()