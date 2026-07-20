def main():
    from backend.perception.screen_io import extract_text_from_screen

    input("📌 请先打开一个含中文文字的窗口，然后按回车继续...")
    text = extract_text_from_screen(lang="chi_sim+eng")
    if text and "[OCR_FAILED]" not in text:
        print("📄 本地 OCR 识别结果:")
        print(text)
    else:
        print("❌ 本地 OCR 失败，请检查 Tesseract 中文包是否安装")


if __name__ == "__main__":
    main()