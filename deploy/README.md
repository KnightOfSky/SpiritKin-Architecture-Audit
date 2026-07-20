## SpiritKinAI 部署基线（当前阶段）

### 目标
- 保持当前以本地开发为主的节奏
- 提供一致的本地 / 容器运行入口
- 为后续单机云服务器部署保留清晰迁移路径

### 当前依赖文件分工
- `requirements.txt`：适合 `venv + pip`、CI 和轻量本地安装
- `environment.yml`：适合 Conda / GPU / 多媒体依赖较多的环境
- `deploy/Dockerfile`：当前容器化基础镜像入口
- `.env.example`：环境变量模板，复制为 `.env` 后可用于覆盖运行时配置

### 运行时配置优先级
`SpiritKinRuntime` 目前按以下顺序解析配置：

1. 显式构造参数
2. 环境变量
3. `config/config.yaml`
4. 默认值

当前已接入的环境变量：
- `SPIRIT_HOTWORD`
- `SPIRIT_KNOWLEDGE_BACKEND`（`keyword` / `embedding`）

### 本地运行（推荐开发期优先）

#### 方式 A：Conda
1. `conda env create -f environment.yml`
2. `conda activate spirit_kin_env`
3. 可选：复制 `.env.example` 为 `.env`，按需调整变量
4. `python -m backend.main`

#### 方式 B：venv + pip
1. `python -m venv .venv`
2. 激活虚拟环境
3. `pip install -r requirements.txt`
4. 可选：先设置 `SPIRIT_HOTWORD` / `SPIRIT_KNOWLEDGE_BACKEND`
5. `python -m backend.main`

### Docker 构建与运行

#### 单独构建镜像
在仓库根目录执行：

- `docker build -f deploy/Dockerfile -t spiritkinai:dev .`

#### 直接运行容器
- `docker run --rm -it --env-file .env -p 8765:8765 spiritkinai:dev`

说明：
- 当前 `deploy/Dockerfile` 走的是 Conda 环境创建路径
- 如果要启用摄像头 / 音频 / GPU，需要按宿主机环境额外挂载设备与驱动能力
- 若容器中需要项目文档检索，镜像里必须包含 `docs/`（本仓库已为此调整 `.dockerignore`）

### Docker Compose
可在仓库根目录执行：

- `docker compose -f deploy/docker-compose.yml up --build`

Compose 会把以下环境变量传入容器：
- `SPIRIT_HOTWORD`
- `SPIRIT_KNOWLEDGE_BACKEND`
- `DISPLAY`
- `NVIDIA_VISIBLE_DEVICES`
- `NVIDIA_DRIVER_CAPABILITIES`

### 当前容器化边界
当前部署基线更偏向“开发 / 单机验证”，还不是正式生产方案。主要边界包括：

- 语音、摄像头、TTS、Live2D 对宿主机设备依赖较强
- `.onnx`、模型权重等大文件通常不建议直接烘焙进镜像
- 正式向量库、对象存储、集中日志、队列系统仍未接入

### 后续迁云建议

#### 阶段 1：单机云服务器
- 保持单容器或单进程部署
- 用 `.env` 或平台环境变量管理运行时开关
- 优先把日志、配置和依赖稳定住

#### 阶段 2：服务化拆分
- 将 AgentCluster / 检索 / 执行器逐步拆成独立服务
- 接入持久化向量库与对象存储
- 增加健康检查、监控与失败重试

#### 阶段 3：受控自动修补基础设施
- 工具失败结构化日志
- 沙箱执行与回滚
- 自动测试选择与验证闭环
- 人工批准边界

### 当前建议
- 开发阶段优先本地跑通
- 需要复现实验环境时再用 Docker
- 需要长期运行或远程访问时，再迁到单机云端