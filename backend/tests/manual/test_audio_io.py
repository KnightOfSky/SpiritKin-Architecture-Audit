def main():
    from backend.expression.speech import speak
    from backend.perception.audio.listener import listen_from_microphone

    print("👂 请说一句话（10秒内）...")
    text = listen_from_microphone(timeout=5, phrase_time_limit=8)
    if text:
        print(f"🗣️ 识别成功: {text}")
        speak("我听到了：" + text[:20])
    else:
        print("❌ 语音识别失败，请检查麦克风权限或环境噪音")


if __name__ == "__main__":
    main()