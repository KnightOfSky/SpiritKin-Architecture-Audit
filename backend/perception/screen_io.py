import base64
import os
from io import BytesIO

from PIL import Image, ImageGrab

# 全局缓存 analyzer（避免重复加载）
_qwen_analyzer = None


def _get_cv2_np():
    import cv2
    import numpy as np

    return cv2, np


def _get_pytesseract():
    import pytesseract

    return pytesseract

def get_qwen_analyzer():
    global _qwen_analyzer
    if _qwen_analyzer is None:
        from .qwen_vl_analyzer import QwenVLAnalyzer

        _qwen_analyzer = QwenVLAnalyzer()
    return _qwen_analyzer

def take_screenshot(region=None, save_path=None):
    """
    截图函数
    :param region: 元组 (left, top, width, height)，如果为 None 则全屏
    :param save_path: 保存路径，如果为 None 则返回 base64 编码
    :return: 文件路径或 base64 字符串
    """
    try:
        if region:
            left, top, width, height = region
            bbox = (left, top, left + width, top + height)
            img = ImageGrab.grab(bbox=bbox)
        else:
            img = ImageGrab.grab()

        if save_path:
            img.save(save_path)
            return os.path.abspath(save_path)
        else:
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            return base64.b64encode(buffered.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"截图失败：{e}")
        return ""

def preprocess_image_for_ocr(image_path):
    """使用 OpenCV 对图像进行预处理"""
    cv2, np = _get_cv2_np()
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError("无法加载图像")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.medianBlur(gray, 3)
    binary = cv2.adaptiveThreshold(
        denoised, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11, 2
    )
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    angle = _detect_skew(cleaned)
    if abs(angle) > 1:
        cleaned = _rotate_image(cleaned, angle)

    processed_path = image_path.replace(".png", "_processed.png")
    cv2.imwrite(processed_path, cleaned)
    return processed_path

def _detect_skew(image):
    """简易倾斜检测（基于霍夫变换）"""
    cv2, np = _get_cv2_np()
    edges = cv2.Canny(image, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=100)
    if lines is not None:
        angles = []
        for _rho, theta in lines[:, 0]:
            angle = (theta * 180 / np.pi) - 90
            if -45 < angle < 45:
                angles.append(angle)
        if angles:
            return np.mean(angles)
    return 0

def _rotate_image(image, angle):
    """旋转图像"""
    cv2, _ = _get_cv2_np()
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rotated

def extract_text_from_screen(region=None, lang="chi_sim+eng"):
    """纯本地 OCR（无网络依赖）"""
    raw_path = take_screenshot(region=region, save_path="temp_raw.png")
    if not raw_path:
        return ""

    try:
        processed_path = preprocess_image_for_ocr(raw_path)
        config = f"--oem 3 --psm 6 -l {lang}"
        pytesseract = _get_pytesseract()
        text = pytesseract.image_to_string(Image.open(processed_path), config=config)
        return text.strip()
    except Exception as e:
        print(f"⚠️ OCR 失败: {e}")
        return ""

def understand_screen_with_qwen(query: str, region=None):
    """使用 Qwen-VL 理解屏幕内容（高级语义分析）"""
    screenshot_path = take_screenshot(region=region, save_path="temp_qwen.png")
    if not screenshot_path:
        return ""
    analyzer = get_qwen_analyzer()
    return analyzer.analyze_image(screenshot_path, query)