import base64
import io
import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from backend.app.settings import (
    resolve_vision_api_key,
    resolve_vision_base_url,
    resolve_vision_generation_profile,
    resolve_vision_model,
    resolve_vision_provider,
)

from .image_utils import load_image

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

REPO_ROOT = Path(__file__).resolve().parents[2]
VISION_MODEL_DIR = REPO_ROOT / "models" / "vision"
FACE_PROTO = VISION_MODEL_DIR / "deploy.prototxt"
FACE_MODEL = VISION_MODEL_DIR / "res10_300x300_ssd_iter_140000_fp16.caffemodel"

_client = None
_client_key = None
_face_net = None


def _get_client(*, provider: str | None = None, base_url: str | None = None, api_key: str | None = None, config_path: str = "config/config.yaml"):
    global _client, _client_key
    resolved_provider = resolve_vision_provider(provider, config_path=config_path)
    resolved_base_url = resolve_vision_base_url(base_url, config_path=config_path)
    resolved_api_key = resolve_vision_api_key(api_key, config_path=config_path)
    client_key = (resolved_provider, resolved_base_url, resolved_api_key)

    if _client is None or _client_key != client_key:
        if OpenAI is None:
            raise RuntimeError("缺少 openai 依赖，无法调用视觉分析客户端")
        if resolved_provider != "openai_compatible":
            raise ValueError(f"当前仅支持 openai_compatible 视觉 provider，收到: {resolved_provider}")
        _client = OpenAI(base_url=resolved_base_url, api_key=resolved_api_key)
        _client_key = client_key
    return _client

def _ensure_face_model():
    os.makedirs(VISION_MODEL_DIR, exist_ok=True)
    if not (FACE_PROTO.exists() and FACE_MODEL.exists()):
        print("📥 首次使用：正在下载 OpenCV DNN 人脸检测模型...")
        import urllib.request
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt",
            str(FACE_PROTO)
        )
        urllib.request.urlretrieve(
            "https://github.com/opencv/opencv_3rdparty/raw/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000_fp16.caffemodel",
            str(FACE_MODEL)
        )
        print("✅ 人脸检测模型已保存至 models/vision/")


def _get_face_net():
    global _face_net
    if _face_net is None:
        _ensure_face_model()
        _face_net = cv2.dnn.readNetFromCaffe(str(FACE_PROTO), str(FACE_MODEL))
    return _face_net

def _detect_largest_face(img_bgr: np.ndarray):
    """返回最大人脸的 (x, y, w, h)，若无则返回 None"""
    h, w = img_bgr.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(img_bgr, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
    face_net = _get_face_net()
    face_net.setInput(blob)
    detections = face_net.forward()

    max_conf = 0
    best_box = None
    for i in range(detections.shape[2]):
        conf = detections[0, 0, i, 2]
        if conf > 0.5 and conf > max_conf:
            max_conf = conf
            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            (x1, y1, x2, y2) = box.astype("int")
            best_box = (x1, y1, x2 - x1, y2 - y1)
    return best_box

def _cv2_to_base64(img_bgr: np.ndarray) -> str:
    """将 OpenCV BGR 图像转为 base64 字符串（供 VL 模型使用）"""
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb).resize((320, 320))
    buffered = io.BytesIO()
    pil_img.save(buffered, format="JPEG", quality=90)
    return base64.b64encode(buffered.getvalue()).decode()

def analyze_image(
    input_data,
    *,
    model_name: str | None = None,
    mode: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    config_path: str = "config/config.yaml",
) -> str:
    """
    主入口函数：支持任意图像输入源
    input_data 可以是：
      - bytes（如 request.files['img'].read()）
      - base64 字符串（如前端传来的 data:image/...）
      - 本地路径（如 'test.jpg'）
      - 或 OpenCV 图像（np.ndarray，内部使用）
    """
    try:
        # 1. 加载图像（自动识别格式）
        if isinstance(input_data, np.ndarray):
            img_bgr = input_data.copy()
        else:
            img_bgr = load_image(input_data)

        # 2. 人脸检测 + 裁剪
        face_box = _detect_largest_face(img_bgr)
        if face_box:
            x, y, w, h = face_box
            margin = int(0.2 * min(w, h))
            x1 = max(0, x - margin)
            y1 = max(0, y - margin)
            x2 = min(img_bgr.shape[1], x + w + margin)
            y2 = min(img_bgr.shape[0], y + h + margin)
            cropped = img_bgr[y1:y2, x1:x2]
        else:
            cropped = img_bgr  # 无人脸则用全图

        # 3. 转为 base64 供 Qwen-VL 使用
        b64_img = _cv2_to_base64(cropped)

        # 4. 调用配置化视觉模型
        resolved_model_name = resolve_vision_model(model_name, config_path=config_path)
        profile = resolve_vision_generation_profile(mode, config_path=config_path)
        response = _get_client(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            config_path=config_path,
        ).chat.completions.create(
            model=resolved_model_name,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "只描述用户当前的手势或表情，如‘微笑’、‘比OK’、‘挥手’。无动作则答‘无明显动作’。不要解释，不超过15字。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]
            }],
            max_tokens=max_tokens or int(profile["max_tokens"]),
            temperature=temperature if temperature is not None else float(profile["temperature"]),
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"❌ 分析失败: {str(e)}"

# === 兼容旧接口：摄像头实时分析 ===
def analyze_gesture(
    *,
    model_name: str | None = None,
    mode: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    config_path: str = "config/config.yaml",
):
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return "❌ 无法获取摄像头画面"
    return analyze_image(
        frame,
        model_name=model_name,
        mode=mode,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=temperature,
        config_path=config_path,
    )  # 直接传 np.ndarray