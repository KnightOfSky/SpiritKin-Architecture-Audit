# Dockerfile
# 项目：SpiritKinAI
# 目标：GPU 支持 + Qwen-VL + 语音助手
# 入口：backend/main.py

FROM continuumio/miniconda3:latest

# 设置时区 & 非交互模式
ENV TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive

# 安装系统依赖（摄像头、音频、视频解码）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    tesseract-ocr \
    libtesseract-dev \
    portaudio19-dev \
    libasound2-dev \
    ffmpeg \
    libv4l-dev \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制 Conda 环境文件
COPY environment.yml .

# 创建 Conda 环境（名称与你本地一致：spirit_kin_env）
RUN conda env create -f environment.yml

# 激活环境并安装 pip 包（如果 environment.yml 里用了 pip）
# 注意：conda activate 在 Dockerfile 中需用 source
SHELL ["conda", "run", "--no-capture-output", "-n", "spirit_kin_env", "/bin/bash", "-c"]

# 复制整个项目
COPY . .

# 暴露端口
# Gradio
EXPOSE 7860 
# WebSocket 
EXPOSE 8765  

# 启动命令：激活环境 + 运行 backend/main.py
CMD ["conda", "run", "--no-capture-output", "-n", "spirit_kin_env", "python", "backend/main.py"]