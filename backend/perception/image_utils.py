# perception/image_utils.py
import base64
import os

import cv2
import numpy as np


def bytes_to_cv2_image(image_bytes: bytes) -> np.ndarray:
    """
    将 bytes（如 HTTP 上传的图片）转为 OpenCV BGR 图像
    """
    if not isinstance(image_bytes, bytes):
        raise TypeError("输入必须是 bytes 类型")
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图像数据，请检查是否为有效图片")
    return img

def base64_to_cv2_image(b64_str: str) -> np.ndarray:
    """
    将 base64 字符串（支持 data:image 前缀）转为 OpenCV BGR 图像
    """
    if not isinstance(b64_str, str):
        raise TypeError("输入必须是字符串")
    
    if b64_str.startswith("data:image"):
        b64_str = b64_str.split(",", 1)[1]
    
    try:
        image_bytes = base64.b64decode(b64_str)
    except Exception as e:
        raise ValueError(f"Base64 解码失败: {e}") from e
    
    return bytes_to_cv2_image(image_bytes)

def load_image(input_data):
    """
    智能加载图像：支持 bytes、base64 字符串、本地文件路径
    返回 OpenCV BGR 格式图像 (np.ndarray)
    """
    if isinstance(input_data, bytes):
        return bytes_to_cv2_image(input_data)
    elif isinstance(input_data, str):
        if input_data.startswith(("data:image", "/9j/", "iVBOR")):
            return base64_to_cv2_image(input_data)
        elif os.path.isfile(input_data):
            img = cv2.imread(input_data)
            if img is None:
                raise ValueError(f"无法读取本地图片: {input_data}")
            return img
    raise TypeError("不支持的输入类型。请传入 bytes、base64 字符串或有效图片路径。")